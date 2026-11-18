from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _emit(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
    if mode == "never-read":
        time.sleep(5)
        return 9
    request = json.loads(sys.stdin.buffer.read())
    operation = request["operation"]
    if operation == "hang":
        _emit({"type": "progress", "completed": 1, "total": 2})
        time.sleep(60)
        return 9
    if mode == "status-flood":
        for _ in range(4_100):
            _emit({"type": "progress", "completed": 0, "total": 1})

    child_started: bool | None = None
    if mode == "spawn-child":
        child_started = False
        try:
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.1)
            child_started = child.poll() is None
            if child_started:
                child.kill()
            child.wait(timeout=2)
        except BaseException:
            child_started = False

    memory_limited: bool | None = None
    pressure: list[bytearray] = []
    if mode == "memory-pressure":
        memory_limited = False
        try:
            for _ in range(12):
                pressure.append(bytearray(16 * 1024 * 1024))
        except MemoryError:
            memory_limited = True

    result = {
        "ok": True,
        "result": {
            "openai_key_present": bool(os.environ.get("OPENAI_API_KEY")),
            "path_contains_attacker": "attacker-bin" in os.environ.get("PATH", ""),
            "operation": operation,
        },
    }
    if child_started is not None:
        result["result"]["child_started"] = child_started
    if memory_limited is not None:
        result["result"]["memory_limited"] = memory_limited
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    result_path = Path.cwd() / request["result_file"]
    result_path.write_bytes(encoded)
    _emit(
        {
            "type": "result",
            "byte_size": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
    )
    if mode == "result-then-hang":
        time.sleep(5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
