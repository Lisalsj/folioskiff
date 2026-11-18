# Contributing to FolioSkiff

Thank you for helping improve FolioSkiff. This is a local-first, single-maintainer open-source project, so focused changes with reproducible evidence are the easiest to review.

## Supported environment

Development and release verification target Windows 11 x64. Use Python 3.12 through `uv`, Node.js 24, npm, and Git. The packaged application does not require end users to install Python or Node.js.

## Development setup

```powershell
git clone https://github.com/Lisalsj/folioskiff.git
cd folioskiff
uv sync --locked --extra dev
npm ci
npm run build
uv run paper-reader serve
```

Copy configuration examples instead of committing live API keys. Keep personal PDFs, annotations, reports, prompts, backups, credentials, and application state outside the repository.

## Test-driven workflow

For every behavior change:

1. Add a focused test that fails for the intended reason.
2. Implement the smallest change that makes it pass.
3. Run neighboring tests, type checks, and static checks.
4. Update user or developer documentation when the contract changes.

The canonical local quality commands are documented in [docs/testing-release.md](docs/testing-release.md). Connector live-smoke tests are read-only, opt-in, and never part of the default test run.

```powershell
npm run quality:fast
npm run quality
npm run quality:release
```

`quality:fast` runs locked-environment, static, audit, frontend-test, type, and build checks. `quality` adds the complete Python coverage gate and browser tests. `quality:release` additionally builds and verifies the Windows distribution. The full and release scopes can take substantial time and disk space; none of these commands automatically repairs or force-upgrades dependencies.

After local test or build work, remove only disposable generated outputs with:

```powershell
npm run clean:artifacts
```

Preview cleanup with `npm run clean:artifacts -- -WhatIf`. Cleanup removes test,
coverage, packaging, and Python bytecode caches inside the checkout. It skips
tracked files, symbolic links, junctions, and locked targets. Dependencies,
runtime data roots, and the built SPA used by `serve` are retained.

## Commit and pull-request rules

- Use Conventional Commits such as `feat(reader): add page bookmark`.
- Keep a pull request scoped to one coherent change.
- Regenerate OpenAPI and TypeScript types whenever an API schema changes.
- Explain the observed failing test and the final verification commands.
- Do not rewrite unrelated user changes or generated personal data.

## Privacy-safe fixtures

Tests must use synthetic or redistributable fixtures. Remove author email addresses, access tokens, local paths, cookies, private paper text, and personally identifying reading history. Do not submit copyrighted PDFs unless their license explicitly permits redistribution.

## Reporting security issues

Do not open public issues for vulnerabilities or exposed secrets. Follow [SECURITY.md](SECURITY.md) and use GitHub Private Vulnerability Reporting for this repository.
