from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from paper_reader.runtime_control import RuntimeControl
from paper_reader.settings import AppPaths


def _paths(tmp_path: Path, suffix: str = "") -> AppPaths:
    return AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / f"cache{suffix}",
        library_root=tmp_path / f"library{suffix}",
    )


def test_runtime_control_requests_bound_shutdown_once() -> None:
    control = RuntimeControl()
    shutdown = MagicMock()
    control.bind_shutdown(shutdown)

    control.request_restart("onboarding_data_reset")
    control.request_restart("duplicate_request")

    assert control.restart_requested is True
    assert control.restart_reason == "onboarding_data_reset"
    shutdown.assert_called_once_with()


def test_runtime_control_shuts_down_when_bound_after_request() -> None:
    control = RuntimeControl()
    shutdown = MagicMock()

    control.request_restart("runtime_paths_changed")
    control.bind_shutdown(shutdown)

    shutdown.assert_called_once_with()


def test_owned_server_restarts_with_refreshed_paths(tmp_path: Path) -> None:
    from paper_reader.cli import _run_owned_server

    initial_paths = _paths(tmp_path, "-initial")
    refreshed_paths = _paths(tmp_path, "-refreshed")
    lock = MagicMock()
    listeners = [MagicMock(), MagicMock()]
    for index, listener in enumerate(listeners, start=1):
        listener.getsockname.return_value = ("127.0.0.1", 41000 + index)
    servers = [MagicMock(), MagicMock()]
    monitors = [MagicMock(), MagicMock()]
    for monitor in monitors:
        monitor.error = None
        monitor.was_ready = True
    controls: list[RuntimeControl] = []

    def create_app(**kwargs: object) -> object:
        control = kwargs["runtime_control"]
        assert isinstance(control, RuntimeControl)
        controls.append(control)
        return object()

    servers[0].run.side_effect = lambda **_: controls[0].request_restart("data_reset")

    with (
        patch("paper_reader.cli.AppPaths.default", return_value=refreshed_paths),
        patch("paper_reader.cli.create_app", side_effect=create_app) as app_factory,
        patch("paper_reader.cli.apply_pending_data_reset") as apply_reset,
        patch("paper_reader.cli._loopback_socket", side_effect=listeners),
        patch("paper_reader.cli.LauncherMonitor", side_effect=monitors),
        patch("paper_reader.cli.uvicorn.Config"),
        patch("paper_reader.cli.uvicorn.Server", side_effect=servers),
        patch("paper_reader.cli.token_urlsafe", return_value="a" * 43),
    ):
        result = _run_owned_server(
            paths=initial_paths,
            instance_lock=lock,
            port=0,
            open_browser=False,
            ready_file=None,
            shutdown_after=None,
        )

    assert result == 0
    assert [call.args[0] for call in apply_reset.call_args_list] == [
        initial_paths,
        refreshed_paths,
    ]
    assert [call.kwargs["paths"] for call in app_factory.call_args_list] == [
        initial_paths,
        refreshed_paths,
    ]
    assert len(controls) == 2
    for listener in listeners:
        listener.close.assert_called_once_with()


def test_owned_server_fails_closed_after_two_consecutive_restarts(
    tmp_path: Path,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    from paper_reader.cli import _run_owned_server

    paths = _paths(tmp_path)
    lock = MagicMock()
    listeners = [MagicMock() for _ in range(3)]
    servers = [MagicMock() for _ in range(3)]
    monitors = [MagicMock() for _ in range(3)]
    controls: list[RuntimeControl] = []
    for index, listener in enumerate(listeners):
        listener.getsockname.return_value = ("127.0.0.1", 42000 + index)
    for monitor in monitors:
        monitor.error = None
        monitor.was_ready = True

    def create_app(**kwargs: object) -> object:
        control = kwargs["runtime_control"]
        assert isinstance(control, RuntimeControl)
        controls.append(control)
        return object()

    for index, server in enumerate(servers):
        server.run.side_effect = (
            lambda current=index, **_: controls[current].request_restart("loop")
        )

    with (
        patch("paper_reader.cli.AppPaths.default", return_value=paths),
        patch("paper_reader.cli.create_app", side_effect=create_app),
        patch("paper_reader.cli.apply_pending_data_reset"),
        patch("paper_reader.cli._loopback_socket", side_effect=listeners),
        patch("paper_reader.cli.LauncherMonitor", side_effect=monitors),
        patch("paper_reader.cli.uvicorn.Config"),
        patch("paper_reader.cli.uvicorn.Server", side_effect=servers),
        patch("paper_reader.cli.token_urlsafe", return_value="a" * 43),
    ):
        result = _run_owned_server(
            paths=paths,
            instance_lock=lock,
            port=0,
            open_browser=False,
            ready_file=None,
            shutdown_after=None,
        )

    assert result == 8
    assert "restart_limit_exceeded" in capsys.readouterr().err
    assert len(controls) == 3
