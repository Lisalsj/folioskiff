"""Audit a paper-reader Windows onedir without executing bundled code."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paper_reader.packaging.distribution import (  # noqa: E402
    DistributionError,
    verify_distribution,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("distribution", type=Path)
    parser.add_argument("--forbid-build-root", action="append", type=Path, default=[])
    args = parser.parse_args()
    try:
        report = verify_distribution(
            args.distribution,
            layout_path=ROOT / "packaging/release-layout.json",
            project_root=ROOT,
            forbidden_build_roots=tuple(args.forbid_build_root),
        )
    except DistributionError as error:
        print(json.dumps({"ok": False, "error": error.code}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "report": asdict(report)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
