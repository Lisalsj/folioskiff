from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paper_reader.runtime_control import RuntimeControl
from paper_reader.settings import AppPaths


def test_launcher_socket_only_binds_ipv4_loopback() -> None:
    from paper_reader.cli import _loopback_socket

    listener = _loopback_socket(0)
    try:
        host, port = listener.getsockname()
    finally:
        listener.close()

    assert host == "127.0.0.1"
    assert port > 0


def test_serve_command_defaults_to_dynamic_loopback_port() -> None:
    from paper_reader.cli import build_parser

    args = build_parser().parse_args(["serve", "--no-browser"])

    assert args.command == "serve"
    assert args.port == 0
    assert args.open_browser is False
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--port", "-1"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--port", "65536"])


def test_launcher_publishes_only_after_server_ready_and_reuses_prebound_socket(
    tmp_path: Path,
) -> None:
    from paper_reader.cli import run_server

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    listener = MagicMock()
    listener.getsockname.return_value = ("127.0.0.1", 43210)
    server = MagicMock()
    lock = MagicMock()
    lock.acquire.return_value = True
    monitor = MagicMock()
    monitor.error = None

    with (
        patch(
            "paper_reader.cli.token_urlsafe",
            side_effect=["bootstrap-secret", "activation-secret"],
        ),
        patch("paper_reader.cli.AppPaths.default", return_value=paths),
        patch("paper_reader.cli.ApplicationInstanceLock", return_value=lock),
        patch("paper_reader.cli.create_app") as create_app,
        patch("paper_reader.cli._loopback_socket", return_value=listener),
        patch("paper_reader.cli.LauncherMonitor", return_value=monitor) as monitor_type,
        patch("paper_reader.cli.uvicorn.Config"),
        patch("paper_reader.cli.uvicorn.Server", return_value=server),
    ):
        result = run_server(port=0, open_browser=True)

    create_app.assert_called_once_with(
        paths=paths,
        bootstrap_token="bootstrap-secret",
        activation_secret="activation-secret",
        credential_store=create_app.call_args.kwargs["credential_store"],
        automatic_update_checks=True,
        runtime_control=create_app.call_args.kwargs["runtime_control"],
    )
    assert isinstance(create_app.call_args.kwargs["runtime_control"], RuntimeControl)
    monitor_type.assert_called_once_with(
        server=server,
        instance_lock=lock,
        port=43210,
        activation_secret="activation-secret",
        initial_url="http://127.0.0.1:43210/#bootstrap=bootstrap-secret",
        open_browser=True,
        ready_file=None,
        shutdown_after=None,
    )
    monitor.start.assert_called_once_with()
    server.run.assert_called_once_with(sockets=[listener])
    monitor.stop.assert_called_once_with()
    lock.close.assert_called_once_with()
    assert result == 0


def test_restore_failure_happens_before_socket_or_instance_publication(
    tmp_path: Path,
) -> None:
    from paper_reader.backups.startup import RestoreStartupError
    from paper_reader.cli import run_server

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "private-state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    lock = MagicMock()
    lock.acquire.return_value = True
    with (
        patch("paper_reader.cli.AppPaths.default", return_value=paths),
        patch("paper_reader.cli.ApplicationInstanceLock", return_value=lock),
        patch(
            "paper_reader.cli.create_app",
            side_effect=RestoreStartupError("restore_failed", "safe restore failure"),
        ),
        patch("paper_reader.cli._loopback_socket") as loopback_socket,
        patch("paper_reader.cli.LauncherMonitor") as monitor_type,
    ):
        result = run_server(port=0, open_browser=False)

    assert result == 2
    loopback_socket.assert_not_called()
    monitor_type.assert_not_called()
    lock.publish.assert_not_called()
    lock.close.assert_called_once_with()
    assert paths.state_root.joinpath("instance.json").exists() is False


def test_unsafe_profile_lock_fails_closed_without_paths_or_startup(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from paper_reader.cli import run_server
    from paper_reader.instance import InstanceLockError

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "private-state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    lock = MagicMock()
    lock.acquire.side_effect = InstanceLockError("unsafe D:/private/profile path")
    with (
        patch("paper_reader.cli.AppPaths.default", return_value=paths),
        patch("paper_reader.cli.ApplicationInstanceLock", return_value=lock),
        patch("paper_reader.cli.create_app") as create_app,
        patch("paper_reader.cli._loopback_socket") as loopback_socket,
    ):
        result = run_server(port=0, open_browser=False)

    error = capsys.readouterr().err
    assert result == 5
    assert "instance_lock_failed" in error
    assert "private-state" not in error
    assert "D:/private" not in error
    create_app.assert_not_called()
    loopback_socket.assert_not_called()
    lock.close.assert_called_once_with()


def test_loopback_bind_failure_is_stable_and_never_publishes_instance(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from paper_reader.cli import run_server

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    lock = MagicMock()
    lock.acquire.return_value = True
    with (
        patch("paper_reader.cli.AppPaths.default", return_value=paths),
        patch("paper_reader.cli.ApplicationInstanceLock", return_value=lock),
        patch("paper_reader.cli.create_app"),
        patch("paper_reader.cli._loopback_socket", side_effect=OSError("private bind data")),
        patch("paper_reader.cli.LauncherMonitor") as monitor_type,
    ):
        result = run_server(port=43123, open_browser=False)

    error = capsys.readouterr().err
    assert result == 6
    assert "loopback_bind_failed" in error
    assert "private bind data" not in error
    lock.publish.assert_not_called()
    lock.close.assert_called_once_with()
    monitor_type.assert_not_called()


def test_unreachable_existing_instance_never_falls_through_to_startup(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from paper_reader.cli import run_server
    from paper_reader.instance import ActivationError

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    lock = MagicMock()
    lock.acquire.return_value = False
    with (
        patch("paper_reader.cli.AppPaths.default", return_value=paths),
        patch("paper_reader.cli.ApplicationInstanceLock", return_value=lock),
        patch(
            "paper_reader.cli.activate_existing_instance",
            side_effect=ActivationError(
                "instance_unreachable",
                "unreachable D:/private/path secret-value",
            ),
        ),
        patch("paper_reader.cli.create_app") as create_app,
        patch("paper_reader.cli._loopback_socket") as loopback_socket,
    ):
        result = run_server(port=0, open_browser=True)

    error = capsys.readouterr().err
    assert result == 3
    assert "instance_unreachable" in error
    assert "D:/private" not in error
    assert "secret-value" not in error
    create_app.assert_not_called()
    loopback_socket.assert_not_called()
    lock.close.assert_called_once_with()


def test_interrupt_always_removes_published_instance_state(tmp_path: Path) -> None:
    from paper_reader.cli import run_server
    from paper_reader.instance import ApplicationInstanceLock

    paths = AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )
    lock = ApplicationInstanceLock(paths.state_root)
    listener = MagicMock()
    listener.getsockname.return_value = ("127.0.0.1", 45678)
    server = MagicMock()
    server.run.side_effect = KeyboardInterrupt
    monitor = MagicMock()
    monitor.start.side_effect = lambda: lock.publish(
        port=45678,
        activation_secret="a" * 43,
    )
    with (
        patch("paper_reader.cli.AppPaths.default", return_value=paths),
        patch("paper_reader.cli.ApplicationInstanceLock", return_value=lock),
        patch("paper_reader.cli.token_urlsafe", side_effect=["b" * 43, "a" * 43]),
        patch("paper_reader.cli.create_app"),
        patch("paper_reader.cli._loopback_socket", return_value=listener),
        patch("paper_reader.cli.LauncherMonitor", return_value=monitor),
        patch("paper_reader.cli.uvicorn.Config"),
        patch("paper_reader.cli.uvicorn.Server", return_value=server),
        pytest.raises(KeyboardInterrupt),
    ):
        run_server(port=0, open_browser=False)

    assert paths.state_root.joinpath("instance.json").exists() is False
    assert lock.file_descriptor is None
    monitor.stop.assert_called_once_with()
    listener.close.assert_called_once_with()
