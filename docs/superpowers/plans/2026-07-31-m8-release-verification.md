# Documentation, Security, Windows Packaging, and Release Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze and verify the completed repository optimization as a documented, vulnerability-free, accessible Windows 11 x64 `2.0.0` release candidate without pushing, tagging, or publishing it.

**Architecture:** Treat documentation, API/schema hashes, security scans, browser journeys, and packaged Windows bytes as one closed release contract. Run all gates locally from locked dependencies, update frozen API/database receipts only from verified generated output, build one installer/portable release set, and verify it source-free before recording a local receipt.

**Tech Stack:** Python 3.12, uv, pytest, Ruff, Mypy, pip-audit, Bandit, Node.js 24, npm audit, Vitest, Playwright/axe, Vite, PyInstaller, Inno Setup 6.7.1, CycloneDX, PowerShell.

## Global Constraints

- Work on local `main`, use TDD and Conventional Commits, and do not push.
- Do not create or push a Git tag or GitHub Release.
- Release candidate version is `2.0.0` because the approved data rebuild is a breaking local-data boundary.
- Windows 11 x64 is the only supported product platform.
- Installer remains unsigned; SHA-256 and SBOM prove artifact identity, not publisher identity.
- All dependencies and build tools are lock- or version-pinned.
- A known vulnerability, failed test, stale generated contract, unverifiable artifact, privacy leak, or high-priority accessibility issue blocks completion.
- Private papers, annotations, chats, prompts, behavior history, credentials, and local paths never enter release artifacts or evidence.
- Existing `/api/v1` contracts remain available; legacy conversation endpoints may be compatibility adapters but cannot use legacy storage.
- The final worktree must contain only intended source/document changes plus pre-existing untracked `.rc/` and `AGENTS.md`.

---

## File Map

**Create**

- `docs/getting-started.md`: five-minute Windows start.
- `docs/first-run-setup.md`: four-step setup guide.
- `docs/ai-reading-assistant.md`: ask, explain, translate, related, report boundaries.
- `docs/codex-ollama-setup.md`: Agent installation and diagnostics.
- `docs/troubleshooting.md`: stable error code remedies.
- `docs/threat-model-v2.md`: data flow and abuse cases.
- `scripts/security_audit.ps1`: closed local dependency/static audit.
- `tests/foundation/test_security_audit_script.py`: audit-command contract.
- `tests/rc/test_v2_documentation_contract.py`: user/developer documentation contract.
- `tests/rc/test_v2_frozen_contracts.py`: version/API/database/plugin freeze contract.
- `docs/v2-local-release-receipt.md`: final local artifact and gate receipt.

**Modify**

- `docs/README.md`
- `docs/product-requirements.md`
- `docs/architecture.md`
- `docs/data-model.md`
- `docs/ui-ux.md`
- `docs/agent-analysis.md`
- `docs/security-privacy.md`
- `docs/testing-release.md`
- `docs/windows-packaging.md`
- `README.md`
- `README.zh-CN.md`
- `SECURITY.md`
- `CHANGELOG.md`
- `pyproject.toml`
- `uv.lock`
- `package.json`
- `package-lock.json`
- `src/paper_reader/__init__.py`
- `src/paper_reader/openapi.py`
- `openapi/openapi.json`
- `web/src/api/schema.d.ts`
- `src/paper_reader/migrations/versions/__init__.py`
- `.github/workflows/ci.yml`
- `.github/workflows/package-windows.yml`
- `.github/workflows/_windows-package.yml`
- `scripts/build_release_metadata.py`
- `packaging/release-layout.json`
- Existing RC, packaging, security, performance, and e2e tests.

## Task 1: Write Complete User and Developer Documentation

**Files:**

- Create: `docs/getting-started.md`
- Create: `docs/first-run-setup.md`
- Create: `docs/ai-reading-assistant.md`
- Create: `docs/codex-ollama-setup.md`
- Create: `docs/troubleshooting.md`
- Create: `docs/threat-model-v2.md`
- Create: `tests/rc/test_v2_documentation_contract.py`
- Modify: all stable docs listed in the file map.

**Interfaces:**

- Produces one consistent statement of installation, configuration, Agent/network behavior, data reset, API, and verification.
- Stable diagnostic codes from onboarding/Agent APIs appear verbatim in troubleshooting.

- [x] **Step 1: Write the failing documentation-contract test**

```python
from pathlib import Path

ROOT = Path(__file__).parents[2]

REQUIRED_GUIDES = {
    "docs/getting-started.md": (
        "Windows 11 x64",
        "安装器",
        "便携",
        "首次配置",
    ),
    "docs/ai-reading-assistant.md": (
        "提问",
        "解释",
        "翻译",
        "相关推荐",
        "普通提问、解释和翻译不会联网",
    ),
    "docs/codex-ollama-setup.md": (
        "Codex CLI",
        "Ollama Local",
        "configuration_invalid",
        "service_not_running",
    ),
    "docs/threat-model-v2.md": (
        "PDF 提示注入",
        "SSRF",
        "凭据",
        "回环",
        "插件",
    ),
}


def test_v2_guides_cover_exact_product_boundaries() -> None:
    for relative, phrases in REQUIRED_GUIDES.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert len(text.strip()) >= 400, relative
        for phrase in phrases:
            assert phrase in text, f"{relative}: {phrase}"
```

- [x] **Step 2: Run the documentation contract**

Run:

```powershell
uv run pytest tests/rc/test_v2_documentation_contract.py -v
```

Expected: missing-guide failures.

- [x] **Step 3: Write the six focused guides**

Required content:

- Getting started: installer/portable, first launch, no Python/Node, free-source path.
- First-run setup: storage/privacy, Agent, source, preference steps, skip behavior, data reset.
- AI Assistant: context scope, no-auto-call rule, networking, citations, reports vs chat, translation, related results.
- Codex/Ollama: installation links, login, model probing, local/cloud explanation, stable diagnostic actions, no automatic config edits.
- Troubleshooting: every public error code, safe diagnostic export, no-secret issue reports.
- Threat model: assets, trust boundaries, attacker models, controls, residual risks, test references.

- [x] **Step 4: Synchronize stable design and operations docs**

Update architecture, data model, UI/UX, Agent, security, testing/release, and Windows packaging docs to reflect:

- Onboarding and runtime config.
- Assistant/translation/related tables and APIs.
- Explicit network policy.
- Data reset and snapshot.
- Full PR Windows workflow.
- Bundle and performance budgets.
- v2 migration/API freeze.

- [x] **Step 5: Verify links, UTF-8, and content**

Run:

```powershell
uv run pytest tests/rc/test_v2_documentation_contract.py tests/rc/test_rc_evidence_docs.py tests/foundation/test_open_source_contract.py -v
git diff --check
```

Expected: all docs are linked, UTF-8, consistent, and placeholder-free.

- [x] **Step 6: Commit the documentation set**

```powershell
git add -- docs README.md README.zh-CN.md SECURITY.md tests/rc/test_v2_documentation_contract.py
git commit -m "docs: document complete ai reading workflow"
```

## Task 2: Add a Closed Security and Dependency Audit

**Files:**

- Create: `scripts/security_audit.ps1`
- Create: `tests/foundation/test_security_audit_script.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `package.json`
- Modify: `docs/testing-release.md`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**

- Produces: `npm run audit:security`.
- Audits locked Python base plus `dev`, `models`, `ocr`, and `packaging` extras.

- [x] **Step 1: Write the failing audit-script contract**

```python
def test_security_audit_is_closed_and_non_mutating() -> None:
    source = (ROOT / "scripts/security_audit.ps1").read_text(encoding="utf-8")
    for command in (
        "uv lock --check",
        "uv pip check",
        "uv run pip-audit",
        "npm audit --audit-level=high",
        "uv run bandit -r src scripts",
    ):
        assert command in source
    for forbidden in ("--fix", "audit fix", "pip install", "npm install"):
        assert forbidden not in source
```

- [x] **Step 2: Run the contract**

Run:

```powershell
uv run pytest tests/foundation/test_security_audit_script.py -v
```

Expected: missing script.

- [x] **Step 3: Lock audit tools**

Add to the `dev` extra:

```toml
"bandit>=1.8,<2",
"pip-audit>=2.9,<3",
```

Regenerate:

```powershell
uv lock
```

- [x] **Step 4: Implement the non-mutating audit**

```powershell
$ErrorActionPreference = 'Stop'
uv lock --check
uv sync --locked --extra dev --extra models --extra ocr --extra packaging
uv pip check
uv export --locked --all-extras --no-emit-project --format requirements.txt
uv run pip-audit --strict --requirement <temporary-lock-export> --require-hashes --disable-pip
npm audit --audit-level=high
uv run bandit -r src scripts -ll
```

`pip-audit --local --strict` cannot be used because `pip-audit 2.10.1` treats the repository's own editable distribution as a strict collection failure even with `--skip-editable`. The executed implementation therefore audits a temporary, hash-locked, all-extras export that excludes only the project itself; the temporary file is always removed. The script must fail on skipped/unparseable audit output and print no environment variables, credentials, paper paths, or file contents.

Add:

```json
{
  "scripts": {
    "audit:security": "powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts/security_audit.ps1"
  }
}
```

Windows 11 does not include PowerShell 7 (`pwsh`) by default, so the entry point deliberately uses the built-in Windows PowerShell 5.1 runtime and is parsed/tested under that boundary.

- [x] **Step 5: Run the security audit and focused security tests**

Run:

```powershell
npm run audit:security
uv run pytest tests/security tests/assistant/test_prompt_injection.py tests/related_resources/test_web_policy.py tests/platform/test_plugin_permissions.py -v
```

Expected: zero known dependency vulnerabilities, zero medium/high Bandit findings, and all security tests pass.

- [x] **Step 6: Add the same audit to CI**

Run the security script in the backend job after locked dependency installation and before packaging. Reuse the audited environment for tests; do not install audit tools a second time.

- [x] **Step 7: Commit the audit gate**

```powershell
git add -- scripts/security_audit.ps1 tests/foundation/test_security_audit_script.py pyproject.toml uv.lock package.json docs/testing-release.md .github/workflows/ci.yml
git commit -m "security: enforce locked dependency audit"
```

## Task 3: Freeze v2 API, Database, and Version Contracts

**Files:**

- Create: `tests/rc/test_v2_frozen_contracts.py`
- Modify: `src/paper_reader/openapi.py`
- Modify: `openapi/openapi.json`
- Modify: `web/src/api/schema.d.ts`
- Modify: `src/paper_reader/migrations/versions/__init__.py`
- Modify: `scripts/build_release_metadata.py`
- Modify: `tests/rc/test_frozen_contracts.py`
- Modify: `tests/rc/test_data_integrity_matrix.py`

**Interfaces:**

- Database head: `0038_remove_legacy_conversations`.
- Public route prefix remains `/api/v1`.
- Plugin protocol remains `1.x`.
- Frozen schema and OpenAPI hashes are generated, not handwritten.

- [x] **Step 1: Write failing frozen-contract assertions**

```python
def test_v2_database_and_api_contracts_are_closed() -> None:
    assert FROZEN_DATABASE_HEAD == "0038_remove_legacy_conversations"
    assert FROZEN_DATABASE_REVISION_CHAIN[-5:] == (
        "0034_agent_language_preferences",
        "0035_assistant_core",
        "0036_translations",
        "0037_related_resources",
        "0038_remove_legacy_conversations",
    )
    assert API_CONTRACT_VERSION == "v1"
    assert openapi_contract_sha256(load_openapi_document()) == OPENAPI_CONTRACT_SHA256


def test_legacy_conversation_route_is_a_storage_free_compatibility_adapter() -> None:
    document = load_openapi_document()
    assert "/api/v1/conversations/works/{work_id}" in document["paths"]
    assert "paper_conversations" not in database_table_names()
```

- [x] **Step 2: Run frozen-contract tests**

Run:

```powershell
uv run pytest tests/rc/test_v2_frozen_contracts.py tests/rc/test_frozen_contracts.py tests/rc/test_data_integrity_matrix.py -v
```

Observed red: database head/hash and compatibility assertions already passed because M3–M6 had advanced them; the release capability matrix alone failed until the four v2 declarations were added.

- [x] **Step 3: Regenerate OpenAPI and TypeScript**

Run:

```powershell
uv run python -m paper_reader.openapi --output openapi/openapi.json
npm run api:types:from-schema
```

Review the diff for:

- Onboarding.
- Agent diagnostic codes.
- Assistant.
- Translation.
- Related resources.
- Compatibility conversation routes.
- No credential or local-path response fields.

- [x] **Step 4: Recompute frozen hashes and revision inventory**

Use the repository’s deterministic hash helpers to print the current OpenAPI and database schema hashes. Copy those exact generated 64-character values into `openapi.py` and migration constants, then rerun the generator to prove stability.

Update release capability matrix with:

```python
"assistant": "ask_explain_translate_related_explicit_context",
"external_research": "related_resources_only_explicit_consent",
"onboarding": "four_step_skippable_except_storage",
"translation": "versioned_bilingual_english_to_simplified_chinese_default",
```

The 2026-08-01 deterministic recomputation retained database head `0038_remove_legacy_conversations`, schema SHA-256 `4d2261f6d12a971a0acf495585f6302d349fa71cdcd028fb1929ac6c7a4579a4`, and OpenAPI SHA-256 `99a65f26f8227bbdcbcc90ba29b9b1a08762575d2fe16e082be32ebf6fa40ddd`; no handwritten hash change was required.

- [x] **Step 5: Verify frozen contracts and generated files**

Run:

```powershell
uv run pytest tests/rc/test_v2_frozen_contracts.py tests/rc/test_frozen_contracts.py tests/rc/test_data_integrity_matrix.py -v
uv run python -m paper_reader.openapi --output openapi/openapi.json
npm run api:types:from-schema
git diff --exit-code -- openapi/openapi.json web/src/api/schema.d.ts
```

Expected: every hash, revision, route, and generated type is stable.

- [x] **Step 6: Commit frozen contracts**

```powershell
git add -- src/paper_reader/openapi.py openapi/openapi.json web/src/api/schema.d.ts src/paper_reader/migrations/versions/__init__.py scripts/build_release_metadata.py tests/rc/test_v2_frozen_contracts.py tests/rc/test_frozen_contracts.py tests/rc/test_data_integrity_matrix.py
git commit -m "chore(release): freeze v2 data and api contracts"
```

## Task 4: Run Complete Functional, Performance, and Accessibility Gates

**Files:**

- Modify only failing implementation/tests discovered by the gate.
- Update: `docs/performance-baselines.md` with measured local results.
- Update: `docs/privacy-accessibility-audit-v1.md` or rename to `docs/privacy-accessibility-audit-v2.md`.

**Interfaces:**

- Python overall line coverage ≥80%; core domain branch coverage ≥90%.
- Cold start ≤8 seconds.
- Common list/search p95 ≤800 ms at 200,000 metadata rows.
- PDF first page ≤2.5 seconds.
- Cancel feedback ≤2 seconds.

- [x] **Step 1: Run static and dependency gates**

```powershell
npm run audit:security
uv run ruff check .
uv run mypy src scripts
npm run typecheck
npm run build:budget
```

Expected: all commands exit zero.

- [x] **Step 2: Run the complete Python suite with coverage**

```powershell
uv run pytest --cov=paper_reader --cov-branch --cov-report=term-missing --cov-report=json:.coverage-core.json --durations=40 --basetemp=.tmp/pytest-v2
uv run python scripts/check_core_coverage.py .coverage-core.json --minimum 90
```

Expected: all tests pass, overall threshold is met, core branch coverage is at least 90%.

- [x] **Step 3: Run all frontend unit and browser suites**

```powershell
npm test
npx playwright test
```

Expected: all desktop, medium, and mobile projects pass, including axe and visual snapshots.

- [x] **Step 4: Run deterministic performance suites**

```powershell
uv run pytest tests/performance -v --basetemp=.tmp/pytest-performance
uv run python scripts/benchmark_startup.py
uv run python scripts/benchmark_library.py
uv run python scripts/benchmark_identity.py
npx playwright test web/e2e/performance.spec.ts
```

Expected: every agreed performance threshold passes.

`benchmark_startup.py` requires `--executable`; no current v2 source-free binary exists before Task 5. The executed Task 4 gate therefore covers Python performance, 200,000-row library/identity scripts, cancellation, browser briefing/PDF/CLS and bundle budgets, while the exact current-binary 20-sample startup gate moves to Task 6. A stale v1 executable is never substituted.

- [x] **Step 5: Record measured values and accessibility evidence**

Update docs with:

- Hardware/OS/runtime.
- Dataset seed and row count.
- Median/p95 or applicable measure.
- Browser viewport.
- Axe result.
- Manual keyboard path checklist.
- Reduced-motion and high-contrast checks.

- [x] **Step 6: Commit verified evidence**

```powershell
git add -- docs/performance-baselines.md docs/privacy-accessibility-audit-v2.md
git commit -m "test: record v2 performance and accessibility evidence"
```

## Task 5: Bump to 2.0.0 and Build Closed Windows Artifacts

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `src/paper_reader/__init__.py`
- Modify: `packaging/windows/version-info.txt`
- Modify: `CITATION.cff`
- Modify: `.github/ISSUE_TEMPLATE/bug_report.yml`
- Modify: `SECURITY.md`
- Modify: `CHANGELOG.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/package-windows.yml`
- Modify: `.github/workflows/_windows-package.yml`
- Modify: packaging/release metadata tests.

**Interfaces:**

- Version string: `2.0.0` in Python, npm, package lock, release metadata, and changelog.
- Database revision: `0038_remove_legacy_conversations`.

- [x] **Step 1: Write failing version-consistency assertions**

```python
def test_v2_release_version_is_consistent() -> None:
    assert paper_reader.__version__ == "2.0.0"
    assert pyproject_version() == "2.0.0"
    assert package_json_version() == "2.0.0"
    assert package_lock_root_version() == "2.0.0"
    assert "## [2.0.0] - 2026-" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
```

- [x] **Step 2: Run version and packaging contracts**

Run:

```powershell
uv run pytest tests/rc/test_release_candidate_version.py tests/packaging/test_release_metadata.py tests/packaging/test_windows_release_workflow.py -v
```

Expected: version assertions fail on `1.0.0`. The database revision was already correctly frozen as `0038_remove_legacy_conversations` by Task 3 and must not be regressed merely to manufacture a second failure.

- [x] **Step 3: Update all version and revision inputs**

Set `2.0.0` in:

- `pyproject.toml`
- `package.json`
- `src/paper_reader/__init__.py`

Regenerate:

```powershell
uv lock
npm install --package-lock-only
```

Update workflow `database_revision` inputs to `0038_remove_legacy_conversations`.

Move user-visible changes from `[Unreleased]` into:

```markdown
## [2.0.0] - 2026-08-01
### Added
### Changed
### Fixed
### Security
```

- [x] **Step 4: Commit the version boundary before building**

Run:

```powershell
uv run pytest tests/rc/test_release_candidate_version.py tests/packaging/test_release_metadata.py tests/packaging/test_windows_release_workflow.py -v
```

Expected: version and packaging contracts pass.

Commit:

```powershell
git add -- pyproject.toml uv.lock package.json package-lock.json src/paper_reader/__init__.py CHANGELOG.md .github/workflows/ci.yml .github/workflows/package-windows.yml .github/workflows/_windows-package.yml tests/rc/test_release_candidate_version.py tests/packaging
git commit -m "chore(release): prepare paper-reader 2.0.0"
```

- [x] **Step 5: Run the release quality gate from the committed tree**

```powershell
npm run quality:release
```

The npm entry point intentionally invokes inbox Windows PowerShell 5.1, which is the supported clean-Windows baseline; PowerShell 7 (`pwsh`) is not assumed to exist.

Expected: all release checks pass and `git status --short` contains only ignored/generated release output plus pre-existing untracked user entries.

- [x] **Step 6: Build installer, portable archive, SBOM, and hashes**

```powershell
$iscc = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
uv run --frozen --extra packaging python scripts/build_windows.py --output dist/windows/paper-reader --work-root .tmp/windows-build
uv run --frozen --extra packaging python scripts/package_release.py dist/windows/paper-reader --output-dir dist/release --iscc $iscc --python .venv/Scripts/python.exe --npm (Get-Command npm.cmd).Source --git-commit (git rev-parse HEAD) --database-revision 0038_remove_legacy_conversations
uv run --frozen --extra packaging python scripts/build_release_metadata.py verify dist/release
```

Expected: the closed release directory contains installer, portable ZIP, Python/npm SBOMs, SHA-256 inventory, capability matrix, changelog, release notes, unsigned notice, and manifest with no extra files.

## Task 6: Verify Source-Free Windows Operation and Record the Local Receipt

**Files:**

- Create: `docs/v2-local-release-receipt.md`
- Modify: `docs/windows-packaging.md`
- Modify: `tests/rc/test_v2_documentation_contract.py`

**Interfaces:**

- Consumes: immutable `dist/release`.
- Produces: redacted evidence for portable, install, upgrade/reset, activation, shutdown, and uninstall flows.

- [x] **Step 1: Write the failing receipt contract**

```python
def test_v2_release_receipt_records_closed_artifact_evidence() -> None:
    text = (ROOT / "docs/v2-local-release-receipt.md").read_text(encoding="utf-8")
    for phrase in (
        "paper-reader 2.0.0",
        "Git commit",
        "release-manifest SHA-256",
        "installer",
        "portable",
        "SBOM",
        "source-free",
        "unsigned",
        "未推送",
    ):
        assert phrase in text
```

- [x] **Step 2: Run offline release verification**

```powershell
uv run --frozen --extra packaging python scripts/build_release_metadata.py verify dist/release
uv run --frozen --extra packaging python scripts/verify_distribution.py dist/windows/paper-reader
```

Expected: both verifiers exit zero.

- [x] **Step 3: Run source-free clean smoke**

Use the packaged harness:

```powershell
& packaging/windows/clean-smoke.ps1 -ReleaseDirectory (Resolve-Path dist/release) -ExpectedManifestSha256 ((Get-FileHash -Algorithm SHA256 dist/release/release-manifest.json).Hash.ToLowerInvariant())
```

If Windows Sandbox is enabled, also launch `packaging/windows/paper-reader-smoke.wsb` and collect only the redacted evidence JSON. The harness must verify:

- Portable start without source/Python/Node/Git.
- Installer start.
- First-run wizard.
- No-model/free-source path.
- Data reset restart.
- Single-instance activation.
- Clean shutdown.
- Upgrade and uninstall.
- No non-loopback traffic during offline smoke.

Executed boundary: the harness validated the current v2 and historical v1 closed releases, then failed closed with `firewall_requires_administrator` before application startup because this terminal is not elevated and Windows Sandbox is unavailable. A separate real installer/portable lifecycle completed locally. The receipt explicitly leaves administrator-offline startup and v1→v2 installation upgrade unverified; neither is converted into a synthetic success.

- [x] **Step 4: Inspect artifacts and evidence for privacy**

Search release filenames, manifests, SBOMs, logs, and evidence for:

- Workspace paths.
- Windows user name.
- API-key patterns.
- Paper titles/authors from personal data.
- Prompt fragments.
- `.codex` paths.

Any match must be classified and removed before continuing.

- [x] **Step 5: Write the exact local release receipt**

Record:

- Full commit hash.
- Version and database head.
- Release-manifest and SHA-256 inventory hashes.
- Artifact names and sizes.
- SBOM component counts.
- Full quality/security/browser results.
- Clean-smoke result and environment.
- Unsigned status.
- GitHub Actions quota limitation.
- Explicit statement that nothing was pushed, tagged, or published.

- [x] **Step 6: Verify and commit the receipt**

Run:

```powershell
uv run pytest tests/rc/test_v2_documentation_contract.py tests/rc/test_v2_frozen_contracts.py -v
git diff --check
```

Commit:

```powershell
git add -- docs/v2-local-release-receipt.md docs/windows-packaging.md tests/rc/test_v2_documentation_contract.py
git commit -m "docs: record verified local v2 release"
```

## Final Completion Gate

Run from a clean shell:

```powershell
npm run audit:security
npm run quality
npm run build:budget
npx playwright test
uv run --frozen --extra packaging python scripts/build_release_metadata.py verify dist/release
git diff --check
git status --short --branch
git log -12 --oneline
```

Expected:

- Zero known dependency vulnerabilities.
- All Python, frontend, browser, security, performance, accessibility, packaging, and contract gates pass.
- `dist/release` verifies as a closed unsigned release.
- Local `main` contains reviewable Conventional Commits.
- Only pre-existing `.rc/` and `AGENTS.md` remain untracked.
- No push, tag, PR, or release has occurred.
