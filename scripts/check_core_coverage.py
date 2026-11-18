"""Fail CI when the paper-reader core domain branch coverage drops below its gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CORE_MODULES = (
    "src/paper_reader/discovery/identity.py",
    "src/paper_reader/discovery/keywords.py",
    "src/paper_reader/discovery/plans.py",
    "src/paper_reader/discovery/runs.py",
    "src/paper_reader/discovery/suggestions.py",
)


def branch_coverage(report: dict[str, object]) -> tuple[int, int, float]:
    raw_files = report.get("files")
    if not isinstance(raw_files, dict):
        raise ValueError("coverage report has no files map")
    files = {str(name).replace("\\", "/"): value for name, value in raw_files.items()}
    covered = 0
    total = 0
    for module in CORE_MODULES:
        value = files.get(module)
        if not isinstance(value, dict):
            raise ValueError(f"coverage report is missing core module: {module}")
        summary = value.get("summary")
        if not isinstance(summary, dict):
            raise ValueError(f"coverage report has no summary for: {module}")
        covered += int(summary["covered_branches"])
        total += int(summary["num_branches"])
    percent = 100.0 if total == 0 else (covered * 100.0) / total
    return covered, total, percent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--minimum", type=float, default=90.0)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    covered, total, percent = branch_coverage(report)
    print(f"Core domain branch coverage: {percent:.2f}% ({covered}/{total})")
    return 0 if percent >= args.minimum else 1


if __name__ == "__main__":
    raise SystemExit(main())
