from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.benchmark_startup import (
    STARTUP_BUDGET_MS,
    StartupBenchmarkError,
    _summary,
    _validate_distribution,
    run_benchmark,
)


def test_startup_budget_is_decided_by_spa_readiness() -> None:
    samples = [
        {
            "ready_ms": STARTUP_BUDGET_MS - 2,
            "api_ms": STARTUP_BUDGET_MS - 1,
            "spa_ms": STARTUP_BUDGET_MS + 1,
        }
        for _ in range(20)
    ]

    result = _summary(samples)

    assert result["api_ms"]["p95"] < STARTUP_BUDGET_MS
    assert result["spa_ms"]["p95"] > STARTUP_BUDGET_MS
    assert result["within_budget"] is False


def test_distribution_validation_requires_source_free_windows_identity(tmp_path: Path) -> None:
    executable = tmp_path / "paper-reader.exe"
    executable.write_bytes(b"fixture")
    (tmp_path / "_internal").mkdir()
    (tmp_path / "release-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "paper-reader-bundle-manifest-v1",
                "platform": "windows",
                "version": "0.8.0",
            }
        ),
        encoding="utf-8",
    )

    identity = _validate_distribution(executable)

    assert identity == {
        "version": "0.8.0",
        "executable": "paper-reader.exe",
    }
    assert str(tmp_path) not in repr(identity)

    (tmp_path / "_internal").rmdir()
    with pytest.raises(StartupBenchmarkError, match="source-free onedir"):
        _validate_distribution(executable)


def test_release_evidence_rejects_fewer_than_twenty_samples(tmp_path: Path) -> None:
    with pytest.raises(StartupBenchmarkError, match="at least 20 samples"):
        run_benchmark(
            tmp_path / "paper-reader.exe",
            root=tmp_path / "profiles",
            samples=19,
            warmups=0,
        )
