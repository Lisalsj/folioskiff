from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUDGET_SCRIPT = ROOT / "scripts" / "check_vite_budget.mjs"


def _write_build(
    root: Path,
    manifest: dict[str, dict[str, Any]],
    assets: dict[str, bytes],
) -> Path:
    dist = root / "dist"
    manifest_path = dist / ".vite" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    for relative_path, content in assets.items():
        asset_path = dist / relative_path
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(content)
    return dist


def _run_budget(dist: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(BUDGET_SCRIPT), "--dist", str(dist)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_budget_accepts_small_static_entry_and_reports_deterministic_total(
    tmp_path: Path,
) -> None:
    dist = _write_build(
        tmp_path,
        {
            "index.html": {
                "file": "assets/index.js",
                "imports": ["vendor.js"],
                "isEntry": True,
                "src": "index.html",
            },
            "vendor.js": {"file": "assets/vue-core.js"},
        },
        {
            "assets/index.js": b"entry",
            "assets/vue-core.js": b"vendor",
        },
    )

    result = _run_budget(dist)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Initial JavaScript closure (raw bytes)",
        "       5  assets/index.js",
        "       6  assets/vue-core.js",
        "      11  TOTAL (limit 350000)",
    ]


def test_budget_rejects_static_entry_closure_above_350000_bytes(tmp_path: Path) -> None:
    dist = _write_build(
        tmp_path,
        {
            "index.html": {
                "file": "assets/index.js",
                "isEntry": True,
                "src": "index.html",
            },
        },
        {"assets/index.js": b"x" * 350_001},
    )

    result = _run_budget(dist)

    assert result.returncode != 0
    assert "initial JavaScript closure is 350001 bytes; limit is 350000" in result.stderr


@pytest.mark.parametrize(
    ("label", "source", "file"),
    [
        ("pdfjs", "node_modules/pdfjs-dist/build/pdf.mjs", "assets/pdfjs.js"),
        ("ocr", "web/src/features/ocr/runtime.ts", "assets/ocr.js"),
        ("reader", "web/src/pages/ReaderWorkspacePage.vue", "assets/reader.js"),
        ("comparison", "web/src/pages/ComparisonsPage.vue", "assets/comparisons.js"),
        ("publishing", "web/src/pages/PublishingPage.vue", "assets/publishing.js"),
        ("settings", "web/src/pages/settings/SettingsLayoutPage.vue", "assets/settings.js"),
    ],
)
def test_budget_rejects_lazy_feature_chunks_in_the_static_entry_closure(
    tmp_path: Path,
    label: str,
    source: str,
    file: str,
) -> None:
    dist = _write_build(
        tmp_path,
        {
            "index.html": {
                "file": "assets/index.js",
                "imports": ["feature"],
                "isEntry": True,
                "src": "index.html",
            },
            "feature": {"file": file, "src": source},
        },
        {"assets/index.js": b"entry", file: b"feature"},
    )

    result = _run_budget(dist)

    assert result.returncode != 0
    assert f"forbidden initial chunk: {label}" in result.stderr


def test_budget_counts_each_physical_javascript_asset_once(tmp_path: Path) -> None:
    dist = _write_build(
        tmp_path,
        {
            "index.html": {
                "file": "assets/index.js",
                "imports": ["vendor-a", "vendor-b"],
                "isEntry": True,
                "src": "index.html",
            },
            "vendor-a": {"file": "assets/shared.js"},
            "vendor-b": {"file": "assets/shared.js"},
        },
        {
            "assets/index.js": b"entry",
            "assets/shared.js": b"shared",
        },
    )

    result = _run_budget(dist)

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("assets/shared.js") == 1
    assert "      11  TOTAL (limit 350000)" in result.stdout


def test_budget_rejects_assets_outside_the_distribution(tmp_path: Path) -> None:
    dist = _write_build(
        tmp_path,
        {
            "index.html": {
                "file": "../outside.js",
                "isEntry": True,
                "src": "index.html",
            },
        },
        {},
    )
    (tmp_path / "outside.js").write_bytes(b"outside")

    result = _run_budget(dist)

    assert result.returncode != 0
    assert "asset path escapes distribution: ../outside.js" in result.stderr
