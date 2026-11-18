"""Command-line launcher for paper-reader."""

from __future__ import annotations

import argparse
import os
import socket
import sys
from collections.abc import Sequence
from pathlib import Path
from secrets import token_urlsafe

import uvicorn

from paper_reader import __version__
from paper_reader.app import create_app
from paper_reader.backups.startup import RestoreStartupError
from paper_reader.credentials import KeyringCredentialStore
from paper_reader.instance import (
    ActivationError,
    ApplicationInstanceLock,
    InstanceLockError,
    activate_existing_instance,
)
from paper_reader.logging import build_uvicorn_log_config
from paper_reader.onboarding.reset import DataResetError, apply_pending_data_reset
from paper_reader.packaging.windows import (
    InstallerRunMarker,
    InstallerRunMarkerError,
    LauncherMonitor,
    open_browser_safely,
)
from paper_reader.reader.native_worker import run_native_worker
from paper_reader.runtime_control import RuntimeControl
from paper_reader.settings import AppPaths


def _positive_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not 0 < parsed <= 3600:
        raise argparse.ArgumentTypeError("must be greater than 0 and at most 3600")
    return parsed


def _loopback_port(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if not 0 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("must be between 0 and 65535")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-reader")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="Start the local application")
    serve.add_argument(
        "--port",
        type=_loopback_port,
        default=0,
        help="Loopback port, 0 chooses a free port",
    )
    serve.add_argument(
        "--no-browser",
        action="store_false",
        dest="open_browser",
        default=True,
        help="Do not open the browser automatically",
    )
    serve.add_argument(
        "--ready-file",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    serve.add_argument(
        "--shutdown-after",
        type=_positive_seconds,
        default=None,
        help=argparse.SUPPRESS,
    )
    subcommands.add_parser("native-worker", help=argparse.SUPPRESS)
    return parser


def _loopback_socket(port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(128)
    return listener


def run_server(
    *,
    port: int,
    open_browser: bool,
    ready_file: Path | None = None,
    shutdown_after: float | None = None,
) -> int:
    bootstrap_root = AppPaths.bootstrap_state_root()
    instance_lock = ApplicationInstanceLock(bootstrap_root)
    installer_marker = InstallerRunMarker()
    try:
        try:
            acquired = instance_lock.acquire()
        except InstanceLockError:
            print(
                "paper-reader could not establish a safe profile lock (instance_lock_failed).",
                file=sys.stderr,
            )
            return 5
        if not acquired:
            try:
                activation = activate_existing_instance(bootstrap_root)
            except ActivationError as error:
                print(
                    f"paper-reader could not activate the existing instance ({error.code}). "
                    "Close it or wait briefly, then retry.",
                    file=sys.stderr,
                )
                return 3
            if open_browser:
                open_browser_safely(activation.url)
            return 0

        try:
            installer_marker.acquire()
        except InstallerRunMarkerError:
            print(
                "paper-reader could not establish its installer safety marker "
                "(installer_run_marker_failed).",
                file=sys.stderr,
            )
            return 7

        try:
            paths = AppPaths.default()
            instance_lock.set_alias_root(paths.state_root)
            return _run_owned_server(
                paths=paths,
                instance_lock=instance_lock,
                port=port,
                open_browser=open_browser,
                ready_file=ready_file,
                shutdown_after=shutdown_after,
            )
        finally:
            installer_marker.close()
    finally:
        instance_lock.close()


def _run_owned_server(
    *,
    paths: AppPaths,
    instance_lock: ApplicationInstanceLock,
    port: int,
    open_browser: bool,
    ready_file: Path | None,
    shutdown_after: float | None,
) -> int:
    current_paths = paths
    for restart_count in range(3):
        try:
            apply_pending_data_reset(current_paths)
        except DataResetError:
            print(
                "paper-reader could not safely rebuild its local data "
                "(data_reset_failed).",
                file=sys.stderr,
            )
            return 9
        runtime_control = RuntimeControl()
        result = _run_owned_server_once(
            paths=current_paths,
            instance_lock=instance_lock,
            port=port,
            open_browser=open_browser,
            ready_file=ready_file,
            shutdown_after=shutdown_after,
            runtime_control=runtime_control,
        )
        if result != 0 or not runtime_control.restart_requested:
            return result
        if restart_count >= 2:
            print(
                "paper-reader stopped a repeated restart chain "
                "(restart_limit_exceeded).",
                file=sys.stderr,
            )
            return 8
        current_paths = AppPaths.default()
        instance_lock.set_alias_root(current_paths.state_root)
    raise AssertionError("restart loop must return within three server cycles")


def _run_owned_server_once(
    *,
    paths: AppPaths,
    instance_lock: ApplicationInstanceLock,
    port: int,
    open_browser: bool,
    ready_file: Path | None,
    shutdown_after: float | None,
    runtime_control: RuntimeControl,
) -> int:
    bootstrap_token = token_urlsafe(32)
    activation_secret = token_urlsafe(32)
    try:
        app = create_app(
            paths=paths,
            bootstrap_token=bootstrap_token,
            activation_secret=activation_secret,
            credential_store=KeyringCredentialStore(),
            automatic_update_checks=os.environ.get("PAPER_READER_DISABLE_UPDATE_CHECKS") != "1",
            runtime_control=runtime_control,
        )
    except RestoreStartupError as error:
        print(f"paper-reader could not finish the pending restore: {error}", file=sys.stderr)
        return 2
    try:
        listener = _loopback_socket(port)
    except OSError:
        print(
            "paper-reader could not reserve its loopback port (loopback_bind_failed).",
            file=sys.stderr,
        )
        return 6
    selected_port = int(listener.getsockname()[1])
    url = f"http://127.0.0.1:{selected_port}/#bootstrap={bootstrap_token}"

    config = uvicorn.Config(
        app=app,
        log_level="info",
        log_config=build_uvicorn_log_config(),
    )
    server = uvicorn.Server(config)
    runtime_control.bind_shutdown(lambda: setattr(server, "should_exit", True))
    monitor = LauncherMonitor(
        server=server,
        instance_lock=instance_lock,
        port=selected_port,
        activation_secret=activation_secret,
        initial_url=url,
        open_browser=open_browser,
        ready_file=ready_file,
        shutdown_after=shutdown_after,
    )
    monitor.start()
    try:
        server.run(sockets=[listener])
    finally:
        monitor.stop()
        listener.close()
    if monitor.error is not None or not monitor.was_ready:
        error_code = monitor.error.code if monitor.error is not None else "server_not_ready"
        print(
            f"paper-reader could not become ready ({error_code}).",
            file=sys.stderr,
        )
        return 4
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return run_server(
            port=args.port,
            open_browser=args.open_browser,
            ready_file=args.ready_file,
            shutdown_after=args.shutdown_after,
        )
    if args.command == "native-worker":
        return run_native_worker()
    raise AssertionError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
