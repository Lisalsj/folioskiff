# AGENTS.md

## Commands

Install and run the Windows local application with:

```powershell
uv sync --locked --extra dev
npm ci
npm run build
uv run paper-reader serve
```

Use `uv run paper-reader serve --no-browser` for a server-only smoke. The
service binds to `127.0.0.1`; it serves the Vue SPA and the `/api/v1` API.

Run checks with:

```powershell
uv run pytest --basetemp=.tmp/pytest-basetemp
npm run test
npm run typecheck
npm run api:types
python scripts/check_version.py
```

If the global `uv run` wrapper is locked by a running Windows executable, use
the already installed `.venv\Scripts\python.exe` and tool executables for a
read-only check, then rerun the normal command after the process exits.

## Architecture

- `src/paper_reader/` is the FastAPI service, SQLite data model, jobs, reader,
  retrieval, backups, and Agent backends.
- `web/` is the Vue 3 + Pinia frontend. OpenAPI types are generated into
  `web/src/api/schema.d.ts`; do not hand-edit that generated file.
- `src/paper_reader/agents/` exposes Codex CLI, Ollama Local, and Ollama Cloud
  behind `AgentBackendRegistry`. User actions map to an `AgentTaskKind`; page
  loads and capability/model discovery never call `generate()`.
- Keyword suggestions use the configured default Agent with
  `AgentTaskKind.KEYWORDS` and strict `KeywordSetDefinition` validation.
- Runtime storage uses one `data_root` (default `Documents/paper-reader`) and
  derives `state`, `cache`, `library`, and `library/backups`. Legacy
  `cache_root`/`library_root` settings remain readable while the journaled
  migration runs before SQLite opens.

## Source of truth and generated files

Edit Python under `src/`, frontend source under `web/`, configuration under
`config/`, scripts under `scripts/`, and documentation under `docs/`. Rebuild
`openapi/openapi.json` and `web/src/api/schema.d.ts` with `npm run api:types`.
Do not edit generated build output, runtime archives, `site/public/data/`, or
local data roots by hand.

## Agent and credential boundaries

Codex detection ignores broken PATH `.ps1` shims and verifies the bundled CLI
with `--version` and `login status`. A running desktop process is only status;
generation is available only when the CLI is installed and authenticated.
Ollama Local uses its loopback `/api/chat`; when no model is configured the
first discovered model is selected. Credentials are never returned to the
browser or written to logs, backups, prompts, or generated artifacts.

All model invocations log only task kind, backend, requested/resolved model,
and safe usage counts. Reports, chat, translation, comparison, related
resources, and keyword generation must remain explicit user actions.

## Version and release

Keep `pyproject.toml`, `package.json`, `package-lock.json`, `uv.lock`,
`src/paper_reader/__init__.py`, `openapi/openapi.json`,
`packaging/windows/version-info.txt`, and `CITATION.cff` synchronized. The
current release is `3.2.0`. Preserve existing user changes, run the complete
quality gates, commit the reviewed release, and push `main` when the user has
explicitly requested publication.
