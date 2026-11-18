# Foundation, Dependency Security, and Open-Source Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a reproducible local quality gate, remove known JavaScript dependency vulnerabilities, complete the public repository contract, and make every pull request run the full verified Windows build without duplicate quality work.

**Architecture:** Keep the existing Python and Vue application untouched while hardening the repository around it. Encode open-source, dependency, and workflow expectations as executable tests before changing metadata or CI; consolidate Windows packaging behind one reusable workflow so PR, manual, and tag paths share identical build bytes.

**Tech Stack:** Python 3.12, pytest, uv, Node.js 24, npm, Vue 3, GitHub Actions, PyInstaller, Inno Setup, PowerShell 7.

## Global Constraints

- Work directly on the local `main` branch and create Conventional Commits.
- Do not push, create a remote pull request, rewrite history, rebase, or force-reset.
- Never stage `.rc/`, `.w/`, `AGENTS.md`, user papers, local reports, credentials, or generated personal data.
- Preserve Apache-2.0 licensing and the project names `paper-reader` and `paper_reader`.
- Windows 11 x64 is the only product platform for this delivery.
- Every pull request must run the complete test suite and build/verify the Windows installer and portable archive.
- Prefer direct dependency upgrades; use exact npm overrides only where upstream direct dependencies have not moved.
- Never run `npm audit fix --force`.
- Keep the application runnable after every task.

---

## File Map

**Create**

- `tests/foundation/test_dependency_policy.py`: executable policy for vulnerable Node packages.
- `tests/foundation/test_open_source_contract.py`: public repository metadata and community-file contract.
- `tests/foundation/test_local_quality_gate.py`: quality script command and safety contract.
- `scripts/quality.ps1`: one local entry point matching the remote quality gate.
- `README.zh-CN.md`: full Simplified Chinese repository entry.
- `CONTRIBUTING.md`: contributor setup, TDD, commit, and review rules.
- `CODE_OF_CONDUCT.md`: Contributor Covenant conduct rules and reporting path.
- `SUPPORT.md`: support boundaries and issue routing.
- `GOVERNANCE.md`: maintainer and decision process for a personal open-source project.
- `CITATION.cff`: citation metadata.
- `.github/ISSUE_TEMPLATE/bug_report.yml`: structured bug report.
- `.github/ISSUE_TEMPLATE/feature_request.yml`: structured feature proposal.
- `.github/ISSUE_TEMPLATE/config.yml`: private security and support links.
- `.github/PULL_REQUEST_TEMPLATE.md`: verification and privacy checklist.
- `.github/workflows/_windows-package.yml`: reusable audited build and clean-smoke workflow.

**Modify**

- `package.json`: project URLs, license metadata, audit script, and exact dependency overrides.
- `package-lock.json`: resolved safe dependency graph.
- `pyproject.toml`: maintainers, classifiers, keywords, and project URLs.
- `README.md`: concise English entry and Chinese link.
- `SECURITY.md`: actual supported release status and private reporting contract.
- `docs/README.md`: stable documentation index and encoding cleanup.
- `.github/workflows/ci.yml`: full PR quality plus reusable Windows build.
- `.github/workflows/package-windows.yml`: manual/tag caller for the reusable build.
- `.github/dependabot.yml`: grouped monthly dependency updates.
- `tests/packaging/test_windows_release_workflow.py`: new workflow topology.

## Task 1: Lock a Vulnerability-Free Node Dependency Graph

**Files:**

- Create: `tests/foundation/test_dependency_policy.py`
- Modify: `package.json`
- Modify: `package-lock.json`

**Interfaces:**

- Consumes: npm lockfile version 3 in `package-lock.json`.
- Produces: `npm run audit` and a lockfile with no `brace-expansion <=5.0.7` or `js-yaml <4.3.0`.

- [ ] **Step 1: Write the failing dependency-policy test**

```python
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _locked_versions(package_name: str) -> tuple[tuple[int, ...], ...]:
    payload = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    suffix = f"node_modules/{package_name}"
    return tuple(
        _version_tuple(str(item["version"]))
        for path, item in payload["packages"].items()
        if path == suffix or path.endswith(f"/{suffix}")
    )


def test_node_lock_excludes_known_denial_of_service_versions() -> None:
    brace_versions = _locked_versions("brace-expansion")
    yaml_versions = _locked_versions("js-yaml")

    assert brace_versions
    assert yaml_versions
    assert all(version >= (5, 0, 8) for version in brace_versions)
    assert all(version >= (4, 3, 0) for version in yaml_versions)
```

- [ ] **Step 2: Run the test and current audit to verify the unsafe graph is detected**

Run:

```powershell
uv run pytest tests/foundation/test_dependency_policy.py -v
npm audit --audit-level=high
```

Expected: the pytest assertion fails on the old transitive versions and npm reports the `brace-expansion` and `js-yaml` advisories.

- [ ] **Step 3: Add exact upstream-compatible overrides and the audit script**

Add to `package.json`:

```json
{
  "scripts": {
    "audit": "npm audit --audit-level=high"
  },
  "overrides": {
    "@vue/test-utils": {
      "js-beautify": "2.0.3"
    },
    "openapi-typescript": {
      "@redocly/openapi-core": "2.43.1"
    }
  }
}
```

Regenerate only the npm dependency graph:

```powershell
npm install --package-lock-only
```

- [ ] **Step 4: Verify the safe graph and generator compatibility**

Run:

```powershell
npm audit --audit-level=high
npm test
npm run typecheck
npm run api:types
git diff --exit-code -- openapi/openapi.json web/src/api/schema.d.ts
npm run build
uv run pytest tests/foundation/test_dependency_policy.py -v
```

Expected: audit reports zero vulnerabilities, all frontend tests pass, generated API files remain stable, and the production build succeeds.

- [ ] **Step 5: Commit the dependency fix**

```powershell
git add -- package.json package-lock.json tests/foundation/test_dependency_policy.py
git commit -m "fix(deps): remove vulnerable node transitive packages"
```

## Task 2: Encode and Complete the Public Repository Contract

**Files:**

- Create: `tests/foundation/test_open_source_contract.py`
- Create: `CONTRIBUTING.md`
- Create: `CODE_OF_CONDUCT.md`
- Create: `SUPPORT.md`
- Create: `GOVERNANCE.md`
- Create: `CITATION.cff`
- Create: `.github/ISSUE_TEMPLATE/bug_report.yml`
- Create: `.github/ISSUE_TEMPLATE/feature_request.yml`
- Create: `.github/ISSUE_TEMPLATE/config.yml`
- Create: `.github/PULL_REQUEST_TEMPLATE.md`
- Modify: `pyproject.toml`
- Modify: `package.json`

**Interfaces:**

- Consumes: repository owner `Lisalsj` and canonical URL `https://github.com/Lisalsj/folioskiff`.
- Produces: machine-readable package metadata and the standard GitHub contribution surface.

- [ ] **Step 1: Write the failing repository-contract tests**

```python
from __future__ import annotations

import json
from pathlib import Path

import tomllib

ROOT = Path(__file__).parents[2]
REQUIRED = (
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SUPPORT.md",
    "GOVERNANCE.md",
    "CITATION.cff",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
)


def test_public_repository_files_exist_and_are_nonempty() -> None:
    for relative in REQUIRED:
        path = ROOT / relative
        assert path.is_file(), relative
        assert len(path.read_text(encoding="utf-8").strip()) >= 80, relative


def test_python_and_node_metadata_point_to_the_canonical_repository() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    project = pyproject["project"]
    assert project["license"] == "Apache-2.0"
    assert project["urls"]["Repository"] == "https://github.com/Lisalsj/folioskiff"
    assert project["urls"]["Issues"].endswith("/issues")
    assert any("Microsoft :: Windows" in item for item in project["classifiers"])
    assert package["license"] == "Apache-2.0"
    assert package["repository"]["url"].endswith("Lisalsj/folioskiff.git")
    assert package["bugs"]["url"].endswith("/issues")
```

- [ ] **Step 2: Run the contract tests and verify the missing surface**

Run:

```powershell
uv run pytest tests/foundation/test_open_source_contract.py -v
```

Expected: failure lists the missing community files and metadata keys.

- [ ] **Step 3: Add exact Python and npm project metadata**

Add to `[project]` in `pyproject.toml`:

```toml
authors = [{ name = "Lisalsj" }]
keywords = ["academic-papers", "research-agent", "paper-recommendation", "pdf-reader"]
classifiers = [
  "Development Status :: 5 - Production/Stable",
  "Environment :: Win32 (MS Windows)",
  "Framework :: FastAPI",
  "License :: OSI Approved :: Apache Software License",
  "Operating System :: Microsoft :: Windows",
  "Programming Language :: Python :: 3.12",
  "Topic :: Scientific/Engineering :: Information Analysis",
  "Typing :: Typed",
]

[project.urls]
Homepage = "https://github.com/Lisalsj/folioskiff"
Repository = "https://github.com/Lisalsj/folioskiff"
Issues = "https://github.com/Lisalsj/folioskiff/issues"
Documentation = "https://github.com/Lisalsj/folioskiff/tree/main/docs"
Changelog = "https://github.com/Lisalsj/folioskiff/blob/main/CHANGELOG.md"
```

Add to `package.json`:

```json
{
  "license": "Apache-2.0",
  "author": "Lisalsj",
  "homepage": "https://github.com/Lisalsj/folioskiff#readme",
  "repository": {
    "type": "git",
    "url": "git+https://github.com/Lisalsj/folioskiff.git"
  },
  "bugs": {
    "url": "https://github.com/Lisalsj/folioskiff/issues"
  },
  "keywords": [
    "academic-papers",
    "research-agent",
    "paper-recommendation",
    "pdf-reader"
  ]
}
```

- [ ] **Step 4: Add complete community files and templates**

Use these required headings and decisions:

```markdown
# Contributing to paper-reader
## Supported environment
## Development setup
## Test-driven workflow
## Commit and pull-request rules
## Privacy-safe fixtures
## Reporting security issues
```

```markdown
# Governance
## Scope
## Maintainer
## Decision process
## Releases
## Security decisions
## Code of conduct enforcement
```

The bug template must request the app version, Windows version, reproduction, expected/actual behavior, redacted logs, and whether private papers or credentials were excluded. The PR template must require tests, docs, generated OpenAPI/types, privacy review, and confirmation that no personal paper data was committed.

- [ ] **Step 5: Run metadata and package validation**

Run:

```powershell
uv run pytest tests/foundation/test_open_source_contract.py -v
uv build
npm pkg get name version license repository homepage bugs
```

Expected: tests pass, Python source and wheel build successfully, and npm prints the canonical public metadata.

- [ ] **Step 6: Commit the repository contract**

```powershell
git add -- pyproject.toml package.json CONTRIBUTING.md CODE_OF_CONDUCT.md SUPPORT.md GOVERNANCE.md CITATION.cff .github/ISSUE_TEMPLATE .github/PULL_REQUEST_TEMPLATE.md tests/foundation/test_open_source_contract.py
git commit -m "docs: complete open source repository contract"
```

## Task 3: Publish Consistent English and Chinese Entry Documentation

**Files:**

- Modify: `README.md`
- Create: `README.zh-CN.md`
- Modify: `SECURITY.md`
- Modify: `docs/README.md`
- Modify: `tests/foundation/test_open_source_contract.py`

**Interfaces:**

- Consumes: the confirmed product boundary and Apache-2.0 repository contract.
- Produces: English default README, full Chinese companion, accurate release/security status, and a UTF-8 documentation index.

- [ ] **Step 1: Extend the failing contract with bilingual and status assertions**

```python
def test_readme_language_entries_and_security_status_are_consistent() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    assert "[简体中文](README.zh-CN.md)" in english
    assert "[English](README.md)" in chinese
    for heading in ("Features", "Quick start", "AI and network boundary", "Contributing"):
        assert f"## {heading}" in english
    for heading in ("功能", "快速开始", "AI 与联网边界", "参与贡献"):
        assert f"## {heading}" in chinese
    assert "1.0.0" in security
    assert "has not yet published a stable release" not in security
```

- [ ] **Step 2: Run the focused contract test**

Run:

```powershell
uv run pytest tests/foundation/test_open_source_contract.py -v
```

Expected: failure because `README.zh-CN.md` is absent and `SECURITY.md` still denies a stable local release.

- [ ] **Step 3: Rewrite both README entry points with the same structure**

Both files must contain:

- Product statement: research-intent Agent, recommendation, reader, and AI analysis.
- Current capability matrix and explicit Windows-only support.
- Installer/portable quick start.
- Source and Agent configuration summary.
- Explicit “no model call until user action” and networking boundary.
- Privacy statement and zero telemetry.
- Development setup and quality commands.
- Documentation, contribution, security, license, and citation links.

Keep command blocks identical except explanatory language:

```powershell
uv sync --locked --extra dev
npm ci
npm run build
uv run paper-reader serve
```

- [ ] **Step 4: Correct security and documentation-index facts**

`SECURITY.md` must state:

- `1.0.x` is the currently supported stable line until the repository optimization release.
- Reports use GitHub Private Vulnerability Reporting.
- Public issues must not contain credentials, private papers, full prompts, logs, or backups.
- Unsupported environments are clearly named.

Rewrite `docs/README.md` as valid UTF-8 Chinese and link every stable document plus the new design and plan directories.

- [ ] **Step 5: Verify links, encoding, and contracts**

Run:

```powershell
uv run pytest tests/foundation/test_open_source_contract.py tests/rc/test_rc_evidence_docs.py -v
git diff --check
```

Expected: all checks pass and no mojibake or trailing whitespace remains.

- [ ] **Step 6: Commit the bilingual documentation**

```powershell
git add -- README.md README.zh-CN.md SECURITY.md docs/README.md tests/foundation/test_open_source_contract.py
git commit -m "docs: add consistent bilingual project entry"
```

## Task 4: Create One Reproducible Local Quality Command

**Files:**

- Create: `scripts/quality.ps1`
- Create: `tests/foundation/test_local_quality_gate.py`
- Modify: `package.json`
- Modify: `CONTRIBUTING.md`
- Modify: `docs/testing-release.md`

**Interfaces:**

- Consumes: locked uv/npm environments and existing test suites.
- Produces: `pwsh -File scripts/quality.ps1 -Scope fast|full|release`.

- [ ] **Step 1: Write the failing script-contract test**

```python
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_local_quality_gate_has_closed_scopes_and_required_commands() -> None:
    source = (ROOT / "scripts" / "quality.ps1").read_text(encoding="utf-8")

    assert "ValidateSet('fast', 'full', 'release')" in source
    for command in (
        "uv lock --check",
        "uv pip check",
        "uv run ruff check .",
        "uv run mypy src scripts",
        "npm audit --audit-level=high",
        "npm test",
        "npm run typecheck",
        "npm run build",
    ):
        assert command in source
    assert "npm audit fix" not in source
    assert "git reset" not in source
```

- [ ] **Step 2: Run the contract to verify the script is missing**

Run:

```powershell
uv run pytest tests/foundation/test_local_quality_gate.py -v
```

Expected: failure because `scripts/quality.ps1` does not exist.

- [ ] **Step 3: Implement the closed-scope PowerShell runner**

The script must begin:

```powershell
param(
  [ValidateSet('fast', 'full', 'release')]
  [string]$Scope = 'full'
)

$ErrorActionPreference = 'Stop'
$env:PAPER_READER_DISABLE_UPDATE_CHECKS = '1'

uv lock --check
uv pip check
uv run ruff check .
uv run mypy src scripts
npm audit --audit-level=high
npm test
npm run typecheck
npm run build

if ($Scope -ne 'fast') {
  uv run pytest --cov=paper_reader --cov-branch --cov-report=term-missing --cov-report=json:.coverage-core.json --durations=25 --basetemp=.tmp/pytest-quality
  uv run python scripts/check_core_coverage.py .coverage-core.json --minimum 90
  npm run test:e2e
}

if ($Scope -eq 'release') {
  uv run --frozen --extra packaging python scripts/build_windows.py --output dist/windows/paper-reader --work-root .tmp/windows-build
  uv run --frozen --extra packaging python scripts/verify_distribution.py dist/windows/paper-reader
}
```

Add npm aliases:

```json
{
  "scripts": {
    "quality:fast": "pwsh -File scripts/quality.ps1 -Scope fast",
    "quality": "pwsh -File scripts/quality.ps1 -Scope full",
    "quality:release": "pwsh -File scripts/quality.ps1 -Scope release"
  }
}
```

- [ ] **Step 4: Run the contract and fast gate**

Run:

```powershell
uv run pytest tests/foundation/test_local_quality_gate.py -v
pwsh -File scripts/quality.ps1 -Scope fast
```

Expected: the contract and all fast checks pass.

- [ ] **Step 5: Document the exact contributor commands**

Update `CONTRIBUTING.md` and `docs/testing-release.md` so contributors use:

```powershell
npm run quality:fast
npm run quality
npm run quality:release
```

State that the full and release scopes can take substantial time on Windows and never repair dependencies automatically.

- [ ] **Step 6: Commit the quality entry point**

```powershell
git add -- scripts/quality.ps1 tests/foundation/test_local_quality_gate.py package.json CONTRIBUTING.md docs/testing-release.md
git commit -m "build: add reproducible local quality gate"
```

## Task 5: Make Full PR Packaging Reusable and Non-Duplicative

**Files:**

- Create: `.github/workflows/_windows-package.yml`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/package-windows.yml`
- Modify: `.github/dependabot.yml`
- Modify: `tests/packaging/test_windows_release_workflow.py`

**Interfaces:**

- Consumes: passing backend, frontend, and browser jobs from `ci.yml`.
- Produces: one reusable `windows-package` workflow that builds and verifies identical bytes for PR, manual, and tag callers.

- [ ] **Step 1: Rewrite the workflow contract first**

Add assertions equivalent to:

```python
REUSABLE = ROOT / ".github" / "workflows" / "_windows-package.yml"


def test_every_pull_request_runs_quality_then_reusable_windows_packaging() -> None:
    ci = CI_WORKFLOW.read_text(encoding="utf-8")
    reusable = REUSABLE.read_text(encoding="utf-8")
    release = WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request:" in ci
    assert "needs: [backend, frontend, e2e]" in ci
    assert "uses: ./.github/workflows/_windows-package.yml" in ci
    assert "workflow_call:" in reusable
    assert "Source-free install and portable verification" in reusable
    assert "pull_request:" not in release.split("\npermissions:", 1)[0]
    assert "uses: ./.github/workflows/_windows-package.yml" in release
```

Retain the existing assertions for pinned toolchains, immutable actions, Inno Setup validation, release hashes, source-free verification, and draft-only tag releases.

- [ ] **Step 2: Run the workflow contract and observe the topology failure**

Run:

```powershell
uv run pytest tests/packaging/test_windows_release_workflow.py -v
```

Expected: failure because the reusable workflow does not exist and PR packaging is still an independent path-filtered workflow.

- [ ] **Step 3: Extract build and verify jobs into the reusable workflow**

`_windows-package.yml` must:

- Trigger only with `workflow_call`.
- Accept `database_revision` as a required string input.
- Expose artifact name and manifest SHA-256 as workflow outputs.
- Build with pinned Python 3.12.10, Node 24.12.0, uv 0.11.8, and Inno Setup 6.7.1.
- Upload an immutable verification bundle.
- Verify in a second job without source checkout, Python, Node, npm, or uv.
- Use read-only permissions.

Replace the hard-coded database revision in the packaging command with:

```yaml
--database-revision ${{ inputs.database_revision }}
```

- [ ] **Step 4: Call the reusable workflow from CI and release workflows**

In `ci.yml`, add after the browser job:

```yaml
  windows-package:
    name: Windows installer and portable verification
    needs: [backend, frontend, e2e]
    uses: ./.github/workflows/_windows-package.yml
    with:
      database_revision: 0032_identity_candidate_indexes
```

In `package-windows.yml`:

- Keep only `workflow_dispatch` and `v*` tag triggers.
- Run the full quality job.
- Call `_windows-package.yml` after quality.
- Create a draft release only for `v*` tags and only from verified outputs.

Keep `cancel-in-progress: true` for non-tag runs and false for tags.

- [ ] **Step 5: Group monthly Dependabot updates**

Configure three monthly groups:

```yaml
groups:
  python-runtime:
    patterns: ["*"]
```

Use equivalent `node-toolchain` and `github-actions` groups in their respective ecosystem entries. Keep a maximum of one open PR per ecosystem to prevent three full Windows builds from being queued at once.

- [ ] **Step 6: Verify workflow, YAML, and local quality contracts**

Run:

```powershell
uv run pytest tests/packaging/test_windows_release_workflow.py tests/foundation/test_local_quality_gate.py -v
git diff --check
```

Expected: all workflow contracts pass; GitHub parses the same workflow files on the next explicitly authorized remote run.

- [ ] **Step 7: Commit the workflow consolidation**

```powershell
git add -- .github/workflows/ci.yml .github/workflows/package-windows.yml .github/workflows/_windows-package.yml .github/dependabot.yml tests/packaging/test_windows_release_workflow.py
git commit -m "ci: run verified windows packaging for every pull request"
```

## Plan Completion Gate

Run:

```powershell
pwsh -File scripts/quality.ps1 -Scope full
git diff --check
git status --short
```

Expected:

- All local quality checks pass.
- npm audit reports zero vulnerabilities.
- The only untracked user-owned entries remain `.rc/` and `AGENTS.md`.
- No push has occurred.
