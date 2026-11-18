from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "package-windows.yml"
REUSABLE = ROOT / ".github" / "workflows" / "_windows-package.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SETUP_UV_ACTION = "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9"  # v9.0.0


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _job(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [a-z][a-z0-9_-]*:\n|\Z)",
        source,
    )
    assert match is not None, f"missing workflow job: {name}"
    return match.group("body")


def test_cloud_workflows_can_only_start_manually() -> None:
    enabled_events = {
        path.name: set(yaml.load(_source(path), Loader=yaml.BaseLoader)["on"])
        for path in WORKFLOW.parent.iterdir()
        if path.suffix in {".yaml", ".yml"}
    }
    assert enabled_events == {
        CI_WORKFLOW.name: {"workflow_dispatch"},
        WORKFLOW.name: {"workflow_dispatch"},
        REUSABLE.name: {"workflow_call"},
    }


def test_manual_ci_and_release_have_separate_windows_packaging() -> None:
    ci = _source(CI_WORKFLOW)
    reusable = _source(REUSABLE)
    release = _source(WORKFLOW)
    release_package = _job(release, "windows-package")

    assert "windows-package" not in ci
    assert "uses: ./.github/workflows/_windows-package.yml" not in ci
    assert "workflow_call:" in reusable.split("\npermissions:", 1)[0]
    assert "database_revision:" in reusable
    assert "required: true" in reusable
    assert "artifact_name:" in reusable
    assert "release_manifest_sha256:" in reusable
    assert "needs: quality" in release_package
    assert "uses: ./.github/workflows/_windows-package.yml" in release_package


def test_reusable_workflow_pins_toolchains_and_action_releases() -> None:
    source = _source(REUSABLE)

    assert 'PYTHON_VERSION: "3.12.10"' in source
    assert 'NODE_VERSION: "24.12.0"' in source
    assert 'UV_VERSION: "0.11.8"' in source
    assert 'INNO_VERSION: "6.7.1"' in source
    assert "actions/checkout@v7" in source
    assert "actions/setup-python@v7" in source
    assert "actions/setup-node@v7" in source
    assert source.count(SETUP_UV_ACTION) == 1
    assert "astral-sh/setup-uv@v9" not in source
    assert "actions/upload-artifact@v7" in source
    assert "actions/download-artifact@v8" in source
    uses = re.findall(r"(?m)^\s*- uses:\s*([^\s]+)", source)
    assert uses
    assert all("@" in item and not item.endswith("@main") for item in uses)


def test_reusable_build_uses_explicit_revision_and_verified_bundle() -> None:
    source = _source(REUSABLE)
    build = _job(source, "build")

    assert "npm run build" in build
    assert build.index("npm run build") < build.index("scripts/build_windows.py")
    assert "scripts/build_windows.py" in build
    assert "--skip-web-build" in build
    assert "scripts/package_release.py" in build
    assert "scripts/build_release_metadata.py verify" in build
    assert "DATABASE_REVISION: ${{ inputs.database_revision }}" in build
    assert "--database-revision $env:DATABASE_REVISION" in build
    assert "^[0-9]{4}[a-z]?_[a-z0-9_]+$" in build
    assert "choco install innosetup --version $env:INNO_VERSION" in build
    assert "Inno Setup 6_is1" in build
    assert "DisplayVersion" in build
    assert "$registeredVersions.Count -ne 1" in build
    assert "$registeredVersions[0] -ne $env:INNO_VERSION" in build
    assert "packaging/windows/clean-smoke.ps1" in build
    assert "verification-bundle" in build
    assert "release-manifest-sha256" in build
    assert "compression-level: 0" in build
    assert build.count("if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }") >= 7
    assert "--enable-signing" not in source
    assert "secrets." not in source


def test_verify_job_is_a_fresh_source_free_runtime() -> None:
    source = _source(REUSABLE)
    verify = _job(source, "verify")

    assert "needs: build" in verify
    assert "actions/download-artifact@v8" in verify
    assert "clean-smoke.ps1" in verify
    assert "ExpectedManifestSha256" in verify
    assert "-StartupBudgetMs 40000" in verify
    assert "actions/upload-artifact@v7" in verify
    for forbidden in (
        "actions/checkout",
        "actions/setup-python",
        "actions/setup-node",
        "astral-sh/setup-uv",
        "python ",
        "npm ",
        "uv ",
    ):
        assert forbidden not in verify


def test_release_reuses_exact_successful_main_ci_before_packaging() -> None:
    source = _source(WORKFLOW)
    quality = _job(source, "quality")
    package = _job(source, "windows-package")

    assert re.search(r"(?ms)^permissions:\n  actions: read\n  contents: read\s*$", source)
    assert "runs-on: ubuntu-latest" in quality
    assert "GITHUB_TOKEN: ${{ github.token }}" in quality
    assert "/actions/workflows/ci.yml/runs?" in quality
    for token in (
        "branch=main",
        "event=workflow_dispatch",
        "status=success",
        "head_sha=$headSha",
        "exclude_pull_requests=true",
        "headSha -cnotmatch '^[0-9a-f]{40}$'",
        'GITHUB_API_URL -cne "https://api.github.com"',
        'head_branch -ceq "main"',
        'head_sha -ceq $headSha',
        'event -ceq "workflow_dispatch"',
        'status -ceq "completed"',
        'conclusion -ceq "success"',
        'path -ceq ".github/workflows/ci.yml"',
    ):
        assert token in quality
    assert "npm run quality" not in quality
    assert "npx playwright" not in quality
    assert "actions/checkout" not in quality
    assert "Invoke-RestMethod -Method Get" in quality
    assert "Invoke-RestMethod -Method Post" not in quality
    assert "needs: quality" in package
    assert "uses: ./.github/workflows/_windows-package.yml" in package


def test_manual_ci_runs_all_quality_jobs() -> None:
    source = _source(CI_WORKFLOW)
    python_tests = _job(source, "python-tests")
    backend = _job(source, "backend")
    frontend = _job(source, "frontend")
    browser = _job(source, "e2e")

    assert source.count("actions/checkout@v7") == 4
    assert source.count("actions/setup-python@v7") == 3
    assert source.count("actions/setup-node@v7") == 3
    assert source.count(SETUP_UV_ACTION) == 3
    assert "uv run ruff check src tests scripts examples" in python_tests
    assert "uv run python -m mypy src scripts" in python_tests
    assert "--cov=paper_reader --cov-branch" in python_tests
    assert "test_windows_release_workflow.py" in python_tests
    assert "uv run coverage report" in backend
    assert "scripts/check_core_coverage.py" in backend
    assert "npm run audit" in frontend
    assert "npm test" in frontend
    assert "npm run typecheck" in frontend
    assert "npm run build" in frontend
    assert "npm run test:e2e" in browser


def test_main_ci_runs_browser_gate_in_parallel_with_backend() -> None:
    source = _source(CI_WORKFLOW)
    browser = _job(source, "e2e")

    assert "needs:" not in browser
    assert "runs-on: windows-latest" in browser


def test_main_ci_balances_two_windows_python_shards_and_combines_coverage() -> None:
    source = _source(CI_WORKFLOW)
    tests = _job(source, "python-tests")
    aggregate = _job(source, "backend")

    assert "fail-fast: false" in tests
    assert "shard: domain" in tests
    assert "shard: product" in tests
    assert (
        "tests/platform tests/foundation tests/discovery tests/translations tests/assistant"
    ) in tests
    assert (
        "tests/agents tests/onboarding tests/packaging tests/performance "
        "tests/rc tests/reader tests/recommendation tests/related_resources tests/security"
    ) in tests
    assert "uv run pytest -n 2 --dist=loadscope" in tests
    assert "-n 4" not in tests
    assert tests.count("--cov-fail-under=0") == 2
    assert "if: matrix.resource_serial" in tests
    assert "actions/upload-artifact@v7" in tests
    assert "include-hidden-files: true" in tests
    assert "needs: python-tests" in aggregate
    assert "actions/download-artifact@v8" in aggregate
    assert "uv run coverage combine coverage-data" in aggregate
    assert "uv run coverage report" in aggregate
    assert "scripts/check_core_coverage.py" in aggregate


def test_main_ci_isolates_resource_sensitive_windows_python_tests() -> None:
    source = _source(CI_WORKFLOW)
    python_tests = _job(source, "python-tests")
    pyproject = _source(ROOT / "pyproject.toml")
    prepare_tmp = "New-Item -ItemType Directory -Force -Path .tmp"
    parallel_suite = 'uv run pytest -n 2 --dist=loadscope -m "not resource_serial"'
    serial_suite = "uv run pytest -m resource_serial"

    assert "timeout-minutes: 90" in python_tests
    assert prepare_tmp in python_tests
    assert python_tests.index(prepare_tmp) < python_tests.index(parallel_suite)
    assert parallel_suite in python_tests
    assert "--basetemp=.tmp/p" in python_tests
    assert serial_suite in python_tests
    assert python_tests.index(parallel_suite) < python_tests.index(serial_suite)
    assert "--basetemp=.tmp/s" in python_tests
    assert "--cov-append" in python_tests
    assert "if: matrix.resource_serial" in python_tests
    assert python_tests.count("if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }") >= 2
    assert '"pytest-xdist>=3.8,<4"' in pyproject
    assert "resource_serial: resource-intensive or subprocess timing-sensitive" in pyproject


def test_known_runner_sensitive_tests_are_marked_for_serial_execution() -> None:
    expected = (
        (
            ROOT / "tests" / "packaging" / "test_release_metadata.py",
            "test_real_python_and_npm_sboms_use_the_actual_locked_environments",
        ),
        (
            ROOT / "tests" / "packaging" / "test_frozen_launcher.py",
            "test_real_dual_process_launcher_activates_then_cleans_normal_shutdown",
        ),
        (
            ROOT / "tests" / "reader" / "test_ocr_api.py",
            "test_native_extraction_marks_scanned_pages_without_starting_ocr",
        ),
    )

    for path, test_name in expected:
        assert re.search(
            rf"@pytest\.mark\.resource_serial\r?\n(?:async )?def {re.escape(test_name)}",
            _source(path),
        )


def test_tag_release_publishes_only_after_verification_and_has_narrow_write_permission() -> None:
    source = _source(WORKFLOW)
    draft = _job(source, "draft_release")

    assert re.search(
        r"(?ms)^permissions:\n  actions: read\n  contents: read\s*$", source
    )
    assert "needs: windows-package" in draft
    assert "startsWith(github.ref, 'refs/tags/v')" in draft
    assert "contents: write" in draft
    assert "attestations: write" in draft
    assert "id-token: write" in draft
    assert "actions/attest-build-provenance@v2" in draft
    assert "gh release create" in draft
    assert "GH_REPO: ${{ github.repository }}" in draft
    assert "--verify-tag --draft" in draft
    assert "--notes-file" in draft
    assert "sha256:" in draft
    assert "Unexpected or duplicate release asset." in draft
    assert "Published release asset mismatch." in draft
    assert 'gh api "repos/$env:GH_REPO/commits/$tag"' in draft
    assert draft.index("Release asset digest mismatch.") < draft.index("--draft=false")
    assert draft.index("Attest verified release bytes") < draft.index("Verify the draft assets")
    assert "softprops/" not in source
    assert "Publish GitHub Release" in draft


def test_browser_failures_upload_retained_playwright_traces() -> None:
    source = _source(CI_WORKFLOW)

    assert "if: failure()" in source
    assert "actions/upload-artifact@v7" in source
    assert "path: test-results" in source
    assert "if-no-files-found: warn" in source


def test_dependency_updates_are_manually_initiated() -> None:
    for filename in ("dependabot.yml", "dependabot.yaml"):
        assert not (ROOT / ".github" / filename).exists()
