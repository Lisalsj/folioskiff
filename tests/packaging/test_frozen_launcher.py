from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paper_reader.settings import AppPaths

_DUAL_PROCESS_OWNER_LIFETIME_SECONDS = 45.0
_DUAL_PROCESS_STARTUP_TIMEOUT_SECONDS = 30.0
_DUAL_PROCESS_EXIT_TIMEOUT_SECONDS = 60.0


def _paths(tmp_path: Path) -> AppPaths:
    return AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )


def _write_state(root: Path, *, port: int, pid: int, secret: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "instance.json").write_text(
        json.dumps(
            {
                "schema_version": "paper-reader-instance-v1",
                "pid": pid,
                "created_at": "2026-07-14T10:30:00Z",
                "port": port,
                "activation_secret": secret,
            }
        ),
        encoding="utf-8",
    )


class _ActivationHandler(BaseHTTPRequestHandler):
    expected_secret = "a" * 43
    response_pid = os.getpid()
    seen_body = b""

    def do_POST(self) -> None:  # noqa: N802 - stdlib protocol name
        type(self).seen_body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path != "/api/v1/session/activate":
            self.send_error(404)
            return
        payload = json.loads(type(self).seen_body)
        if payload != {"activation_secret": type(self).expected_secret}:
            self.send_error(401)
            return
        encoded = json.dumps(
            {
                "bootstrap_token": "b" * 43,
                "expires_in_seconds": 300,
                "pid": type(self).response_pid,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_: object) -> None:
        return


def test_activation_client_uses_direct_loopback_and_validates_server_identity(
    tmp_path: Path,
) -> None:
    from paper_reader.instance import activate_existing_instance

    server = ThreadingHTTPServer(("127.0.0.1", 0), _ActivationHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_address[1])
        _write_state(
            tmp_path,
            port=port,
            pid=os.getpid(),
            secret=_ActivationHandler.expected_secret,
        )

        result = activate_existing_instance(tmp_path, timeout_seconds=1)

        assert result.url == f"http://127.0.0.1:{port}/#bootstrap={'b' * 43}"
        assert result.pid == os.getpid()
        assert result.expires_in_seconds == 300
        assert _ActivationHandler.seen_body == json.dumps(
            {"activation_secret": _ActivationHandler.expected_secret},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert _ActivationHandler.expected_secret not in repr(result)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_activation_client_rejects_port_reuse_or_unreachable_state(tmp_path: Path) -> None:
    from paper_reader.instance import ActivationError, activate_existing_instance

    _ActivationHandler.response_pid = os.getpid() + 1
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ActivationHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _write_state(
            tmp_path,
            port=int(server.server_address[1]),
            pid=os.getpid(),
            secret=_ActivationHandler.expected_secret,
        )
        with pytest.raises(ActivationError, match="identity") as captured:
            activate_existing_instance(tmp_path, timeout_seconds=1)
        assert captured.value.code == "instance_identity_mismatch"
        assert _ActivationHandler.expected_secret not in str(captured.value)
    finally:
        _ActivationHandler.response_pid = os.getpid()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    _write_state(tmp_path, port=1, pid=123, secret="z" * 43)
    with pytest.raises(ActivationError, match="unreachable") as captured:
        activate_existing_instance(tmp_path, timeout_seconds=0.1)
    assert captured.value.code == "instance_unreachable"
    assert "instance.json" not in str(captured.value)


def test_activation_client_waits_for_the_owned_server_to_publish_ready_state(
    tmp_path: Path,
) -> None:
    from paper_reader.instance import activate_existing_instance

    server = ThreadingHTTPServer(("127.0.0.1", 0), _ActivationHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    def publish_later() -> None:
        time.sleep(0.1)
        _write_state(
            tmp_path,
            port=int(server.server_address[1]),
            pid=os.getpid(),
            secret=_ActivationHandler.expected_secret,
        )

    publisher = threading.Thread(target=publish_later, daemon=True)
    publisher.start()
    try:
        result = activate_existing_instance(tmp_path, timeout_seconds=1)
        assert result.pid == os.getpid()
    finally:
        publisher.join(timeout=2)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def test_second_serve_activates_without_creating_the_application_or_socket(
    tmp_path: Path,
) -> None:
    from paper_reader.cli import run_server
    from paper_reader.instance import ActivationResult

    paths = _paths(tmp_path)
    lock = MagicMock()
    lock.acquire.return_value = False
    activation = ActivationResult(
        url="http://127.0.0.1:45678/#bootstrap=fresh",
        pid=123,
        expires_in_seconds=300,
    )
    with (
        patch("paper_reader.cli.AppPaths.default", return_value=paths),
        patch("paper_reader.cli.ApplicationInstanceLock", return_value=lock),
        patch("paper_reader.cli.activate_existing_instance", return_value=activation),
        patch("paper_reader.cli.create_app") as create_app,
        patch("paper_reader.cli._loopback_socket") as loopback_socket,
        patch("paper_reader.cli.open_browser_safely", return_value=True) as open_browser,
    ):
        result = run_server(port=0, open_browser=True)

    assert result == 0
    create_app.assert_not_called()
    loopback_socket.assert_not_called()
    open_browser.assert_called_once_with(activation.url)
    lock.close.assert_called_once_with()


def test_packaging_smoke_flags_are_hidden_and_validated() -> None:
    from paper_reader.cli import build_parser

    help_text = build_parser().format_help()
    args = build_parser().parse_args(
        ["serve", "--no-browser", "--ready-file", "ready.json", "--shutdown-after", "2.5"]
    )

    assert args.ready_file == Path("ready.json")
    assert args.shutdown_after == 2.5
    assert "ready-file" not in help_text
    assert "shutdown-after" not in help_text
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--shutdown-after", "0"])


def test_launcher_monitor_publishes_ready_state_without_secrets(tmp_path: Path) -> None:
    from paper_reader.packaging.windows import LauncherMonitor

    server = MagicMock()
    server.started = False
    server.should_exit = False
    lock = MagicMock()
    ready_file = tmp_path / "smoke" / "ready.json"
    with patch("paper_reader.packaging.windows.open_browser_safely", return_value=True) as opener:
        monitor = LauncherMonitor(
            server=server,
            instance_lock=lock,
            port=45678,
            activation_secret="a" * 43,
            initial_url="http://127.0.0.1:45678/#bootstrap=" + ("b" * 43),
            open_browser=True,
            ready_file=ready_file,
            shutdown_after=None,
        )
        monitor.start()
        time.sleep(0.05)
        lock.publish.assert_not_called()
        assert ready_file.exists() is False

        server.started = True
        assert monitor.wait_until_ready(timeout=2) is True
        payload = json.loads(ready_file.read_text(encoding="utf-8"))
        raw = ready_file.read_text(encoding="utf-8")
        monitor.stop()

    lock.publish.assert_called_once_with(port=45678, activation_secret="a" * 43)
    opener.assert_called_once_with("http://127.0.0.1:45678/#bootstrap=" + ("b" * 43))
    assert payload == {
        "schema_version": "paper-reader-ready-v1",
        "pid": os.getpid(),
        "port": 45678,
    }
    assert "a" * 43 not in raw
    assert "b" * 43 not in raw
    assert monitor.error is None
    assert ready_file.exists() is False


def test_launcher_monitor_shutdown_and_browser_failure_are_contained(tmp_path: Path) -> None:
    from paper_reader.packaging.windows import LauncherMonitor

    server = MagicMock()
    server.started = True
    server.should_exit = False
    lock = MagicMock()
    with patch(
        "paper_reader.packaging.windows.open_browser_safely",
        side_effect=RuntimeError("browser integration failed with private data"),
    ):
        monitor = LauncherMonitor(
            server=server,
            instance_lock=lock,
            port=45678,
            activation_secret="a" * 43,
            initial_url="http://127.0.0.1:45678/#bootstrap=" + ("b" * 43),
            open_browser=True,
            ready_file=None,
            shutdown_after=0.05,
        )
        monitor.start()
        assert monitor.wait_until_ready(timeout=2) is True
        deadline = time.monotonic() + 2
        while server.should_exit is False and time.monotonic() < deadline:
            time.sleep(0.01)
        monitor.stop()

    assert server.should_exit is True
    assert monitor.error is None


def test_browser_helper_contains_desktop_integration_failures() -> None:
    from paper_reader.packaging.windows import open_browser_safely

    with patch(
        "paper_reader.packaging.windows.webbrowser.open",
        side_effect=OSError("browser failure with private values"),
    ):
        assert open_browser_safely("http://127.0.0.1:45678/#bootstrap=secret") is False


def test_launcher_monitor_does_not_report_ready_when_server_stops_during_startup() -> None:
    from paper_reader.packaging.windows import LauncherMonitor

    server = MagicMock()
    server.started = False
    server.should_exit = False
    lock = MagicMock()
    monitor = LauncherMonitor(
        server=server,
        instance_lock=lock,
        port=45678,
        activation_secret="a" * 43,
        initial_url="http://127.0.0.1:45678/",
        open_browser=False,
        ready_file=None,
        shutdown_after=None,
    )
    monitor.start()
    server.should_exit = True
    time.sleep(0.05)
    monitor.stop()

    assert monitor.was_ready is False
    lock.publish.assert_not_called()


def test_launcher_monitor_rejects_a_linked_ready_file_when_supported(tmp_path: Path) -> None:
    from paper_reader.packaging.windows import LauncherMonitor

    target = tmp_path / "target.json"
    target.write_text("do-not-replace", encoding="utf-8")
    ready_file = tmp_path / "ready.json"
    try:
        ready_file.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows profile")
    server = MagicMock()
    server.started = True
    server.should_exit = False
    monitor = LauncherMonitor(
        server=server,
        instance_lock=MagicMock(),
        port=45678,
        activation_secret="a" * 43,
        initial_url="http://127.0.0.1:45678/",
        open_browser=False,
        ready_file=ready_file,
        shutdown_after=None,
    )
    monitor.start()
    assert monitor.wait_until_ready(timeout=2) is False
    monitor.stop()

    assert monitor.error is not None
    assert monitor.error.code == "launcher_ready_failed"
    assert server.should_exit is True
    assert target.read_text(encoding="utf-8") == "do-not-replace"


@pytest.mark.skipif(os.name != "nt", reason="Windows dual-process launcher contract")
@pytest.mark.resource_serial
def test_real_dual_process_launcher_activates_then_cleans_normal_shutdown(
    tmp_path: Path,
) -> None:
    assert _DUAL_PROCESS_OWNER_LIFETIME_SECONDS > _DUAL_PROCESS_STARTUP_TIMEOUT_SECONDS
    assert _DUAL_PROCESS_EXIT_TIMEOUT_SECONDS > _DUAL_PROCESS_OWNER_LIFETIME_SECONDS

    state = tmp_path / "状态"
    cache = tmp_path / "cache"
    library = tmp_path / "library"
    ready = tmp_path / "ready.json"
    script = r"""
import sys
from pathlib import Path
from unittest.mock import patch
import paper_reader.cli as cli
from paper_reader.settings import AppPaths

paths = AppPaths.from_overrides(
    state_root=Path(sys.argv[1]),
    cache_root=Path(sys.argv[2]),
    library_root=Path(sys.argv[3]),
)
mode = sys.argv[4]
if mode == "owner":
        arguments = [
            "serve", "--no-browser", "--ready-file", sys.argv[5],
            "--shutdown-after", sys.argv[6],
        ]
else:
    arguments = ["serve", "--no-browser"]
    def forbidden(*args, **kwargs):
        raise AssertionError("contender touched application startup")
    cli.create_app = forbidden
    cli._loopback_socket = forbidden
with (
    patch.object(cli.AppPaths, "bootstrap_state_root", return_value=paths.state_root),
    patch.object(cli.AppPaths, "default", return_value=paths),
):
    raise SystemExit(cli.main(arguments))
"""
    owner = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(state),
            str(cache),
            str(library),
            "owner",
            str(ready),
            str(_DUAL_PROCESS_OWNER_LIFETIME_SECONDS),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and owner.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready.exists():
            stdout, stderr = owner.communicate(timeout=5)
            pytest.fail(
                "owner never became ready: "
                f"rc={owner.returncode}, out={stdout!r}, err={stderr!r}"
            )

        instance_path = state / "instance.json"
        instance_payload = json.loads(instance_path.read_text(encoding="utf-8"))
        contender = subprocess.run(
            [sys.executable, "-c", script, str(state), str(cache), str(library), "contender", "-"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_DUAL_PROCESS_STARTUP_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert contender.returncode == 0, contender.stderr
        assert "contender touched application startup" not in contender.stderr
        assert instance_payload["activation_secret"] not in contender.stdout + contender.stderr

        stdout, stderr = owner.communicate(timeout=_DUAL_PROCESS_EXIT_TIMEOUT_SECONDS)
        assert owner.returncode == 0, stderr
        assert instance_payload["activation_secret"] not in stdout + stderr
        assert ready.exists() is False
        assert instance_path.exists() is False
        assert state.joinpath("paper-reader.sqlite3").is_file()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=_DUAL_PROCESS_EXIT_TIMEOUT_SECONDS)
        for stream in (owner.stdout, owner.stderr):
            if stream is not None:
                stream.close()
