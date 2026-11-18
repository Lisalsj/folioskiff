"""Measure source-free Windows onedir startup with fresh and reused profiles."""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

READY_TIMEOUT_SECONDS = 30.0
STARTUP_BUDGET_MS = 8_000.0


class StartupBenchmarkError(RuntimeError):
    """The packaged application did not satisfy the benchmark protocol."""


def _percentile_95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _summary(samples: list[dict[str, float]]) -> dict[str, Any]:
    if not samples:
        raise StartupBenchmarkError("at least one startup sample is required")
    result: dict[str, Any] = {"samples": samples}
    for field in ("ready_ms", "api_ms", "spa_ms"):
        values = [sample[field] for sample in samples]
        result[field] = {
            "min": round(min(values), 3),
            "median": round(_median(values), 3),
            "p95": round(_percentile_95(values), 3),
            "max": round(max(values), 3),
        }
    result["startup_budget_ms"] = STARTUP_BUDGET_MS
    result["within_budget"] = result["spa_ms"]["p95"] <= STARTUP_BUDGET_MS
    return result


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _validate_distribution(executable: Path) -> dict[str, str]:
    resolved = executable.resolve(strict=True)
    if resolved.name.casefold() != "paper-reader.exe":
        raise StartupBenchmarkError("the benchmark requires paper-reader.exe")
    if not (resolved.parent / "_internal").is_dir():
        raise StartupBenchmarkError("the executable is not a source-free onedir bundle")
    manifest_path = resolved.parent / "release-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StartupBenchmarkError("the onedir manifest is unavailable") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "paper-reader-bundle-manifest-v1"
        or manifest.get("platform") != "windows"
        or not isinstance(manifest.get("version"), str)
    ):
        raise StartupBenchmarkError("the onedir manifest identity is invalid")
    return {"version": manifest["version"], "executable": resolved.name}


def _read_ready(path: Path) -> dict[str, int] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "paper-reader-ready-v1"
        or not isinstance(payload.get("pid"), int)
        or not isinstance(payload.get("port"), int)
        or not 1 <= payload["port"] <= 65535
    ):
        raise StartupBenchmarkError("the packaged ready file is invalid")
    return {"pid": payload["pid"], "port": payload["port"]}


def _request_until(
    *,
    port: int,
    path: str,
    deadline: float,
    expected: bytes,
) -> None:
    last_error: Exception | None = None
    while time.perf_counter() < deadline:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
        try:
            connection.request("GET", path, headers={"Connection": "close"})
            response = connection.getresponse()
            body = response.read()
            if response.status == 200 and expected in body:
                return
            last_error = StartupBenchmarkError(f"unexpected response for {path}")
        except (OSError, http.client.HTTPException) as error:
            last_error = error
        finally:
            connection.close()
        time.sleep(0.01)
    raise StartupBenchmarkError(
        f"the packaged endpoint did not become ready: {path}"
    ) from last_error


def _sample_startup(
    executable: Path,
    *,
    profile: Path,
    sample_id: str,
) -> dict[str, float]:
    state = profile / "state"
    cache = profile / "cache"
    library = profile / "library"
    ready = profile / f"ready-{sample_id}.json"
    for directory in (state, cache, library):
        directory.mkdir(parents=True, exist_ok=True)
    ready.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "PAPER_READER_STATE_ROOT": str(state.resolve()),
            "PAPER_READER_CACHE_ROOT": str(cache.resolve()),
            "PAPER_READER_LIBRARY_ROOT": str(library.resolve()),
            "PAPER_READER_DISABLE_UPDATE_CHECKS": "1",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )
    command = (
        str(executable),
        "serve",
        "--no-browser",
        "--ready-file",
        str(ready.resolve()),
        "--shutdown-after",
        "2.5",
    )
    started = time.perf_counter()
    process = subprocess.Popen(  # noqa: S603 - exact audited executable and closed arguments
        command,
        cwd=executable.parent,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = started + READY_TIMEOUT_SECONDS
    try:
        ready_payload: dict[str, int] | None = None
        while time.perf_counter() < deadline:
            ready_payload = _read_ready(ready)
            if ready_payload is not None:
                break
            exit_code = process.poll()
            if exit_code is not None:
                raise StartupBenchmarkError(
                    f"the packaged process exited before ready ({exit_code})"
                )
            time.sleep(0.01)
        if ready_payload is None:
            raise StartupBenchmarkError("the packaged process did not publish ready state")
        ready_ms = (time.perf_counter() - started) * 1_000
        _request_until(
            port=ready_payload["port"],
            path="/api/v1/health",
            deadline=deadline,
            expected=b'"status":"ok"',
        )
        api_ms = (time.perf_counter() - started) * 1_000
        _request_until(
            port=ready_payload["port"],
            path="/",
            deadline=deadline,
            expected=b'id="app"',
        )
        spa_ms = (time.perf_counter() - started) * 1_000
        exit_code = process.wait(timeout=10)
        if exit_code != 0:
            raise StartupBenchmarkError(
                f"the packaged process failed after readiness ({exit_code})"
            )
        return {
            "ready_ms": round(ready_ms, 3),
            "api_ms": round(api_ms, 3),
            "spa_ms": round(spa_ms, 3),
        }
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        ready.unlink(missing_ok=True)


def run_benchmark(
    executable: Path,
    *,
    root: Path,
    samples: int,
    warmups: int,
) -> dict[str, Any]:
    if samples < 20:
        raise StartupBenchmarkError("release evidence requires at least 20 samples per mode")
    if warmups < 0:
        raise StartupBenchmarkError("warmups cannot be negative")
    identity = _validate_distribution(executable)
    root.mkdir(parents=True, exist_ok=True)

    cold_samples: list[dict[str, float]] = []
    for index in range(samples):
        profile = root / "cold" / f"sample-{index:03d}"
        cold_samples.append(
            _sample_startup(executable.resolve(), profile=profile, sample_id=f"cold-{index}")
        )

    warm_profile = root / "warm" / "profile"
    for index in range(warmups):
        _sample_startup(executable.resolve(), profile=warm_profile, sample_id=f"warmup-{index}")
    warm_samples = [
        _sample_startup(executable.resolve(), profile=warm_profile, sample_id=f"warm-{index}")
        for index in range(samples)
    ]
    cold_summary = _summary(cold_samples)
    warm_summary = _summary(warm_samples)
    return {
        "schema_version": "paper-reader-startup-benchmark-v1",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "bundle": identity,
        "samples_per_mode": samples,
        "warmups": warmups,
        "cold_profile": cold_summary,
        "warm_profile": warm_summary,
        "within_budget": bool(
            cold_summary["within_budget"] and warm_summary["within_budget"]
        ),
        "notes": [
            "cold_profile uses a fresh app state/cache/library root for every sample",
            "warm_profile reuses one initialized profile",
            "operating-system file caches are not forcibly flushed",
        ],
    }


def _execute(arguments: argparse.Namespace, root: Path) -> dict[str, Any]:
    return run_benchmark(
        arguments.executable,
        root=root,
        samples=arguments.samples,
        warmups=arguments.warmups,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=2)
    arguments = parser.parse_args()

    if arguments.work_root is None:
        with tempfile.TemporaryDirectory(prefix="paper-reader-startup-") as directory:
            result = _execute(arguments, Path(directory))
    else:
        root = arguments.work_root.resolve()
        if root.exists() and any(root.iterdir()):
            raise StartupBenchmarkError("work root must be absent or empty")
        result = _execute(arguments, root)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if arguments.output is not None:
        arguments.output.resolve().write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result["within_budget"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
