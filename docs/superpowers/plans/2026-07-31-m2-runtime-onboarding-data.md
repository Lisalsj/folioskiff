# Runtime Configuration, Data Reset, and Capability Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe runtime path configuration, a consent-based pre-release data rebuild, durable onboarding state, and accurate capability diagnostics for Codex, Ollama, sources, and optional local models.

**Architecture:** Store path choices in a small validated JSON file under the fixed application state root, outside the database it controls. Stage destructive database rebuilds as an authenticated marker, apply them only during a controlled process restart before SQLite opens, and preserve the object store. Aggregate redaction-safe capability probes behind `/api/v1/onboarding` without letting the application modify external Agent configuration.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite, pytest, uvicorn.

## Global Constraints

- Work directly on local `main`; use TDD and Conventional Commits; do not push.
- Do not read, modify, stage, or delete `.rc/`, `.w/`, `AGENTS.md`, credentials, private papers, or personal generated artifacts.
- Runtime services listen only on `127.0.0.1` and retain existing session, Origin, and CSRF protections.
- The application may diagnose Codex but must never edit `~/.codex/config.toml` or copy Codex credentials.
- Database rebuild requires an authenticated local mutation, a typed confirmation, a snapshot, and a controlled restart.
- A rebuild may replace SQLite metadata but must never delete the content-addressed PDF object store.
- Data directory is the only required onboarding choice; every model and API-key step is skippable.
- New API schemas remain the OpenAPI source of truth and regenerate TypeScript types.

---

## File Map

**Create**

- `src/paper_reader/runtime_config.py`: validated, atomic path configuration.
- `src/paper_reader/runtime_control.py`: restart request shared by the API and launcher.
- `src/paper_reader/onboarding/__init__.py`: onboarding package boundary.
- `src/paper_reader/onboarding/contracts.py`: API and domain enums/snapshots.
- `src/paper_reader/onboarding/models.py`: durable singleton onboarding state.
- `src/paper_reader/onboarding/repository.py`: transactional state persistence.
- `src/paper_reader/onboarding/probes.py`: source, retrieval, Agent, and storage aggregation.
- `src/paper_reader/onboarding/reset.py`: staged reset marker and startup application.
- `src/paper_reader/onboarding/service.py`: onboarding orchestration.
- `src/paper_reader/onboarding/api.py`: `/api/v1/onboarding` routes.
- `src/paper_reader/migrations/versions/0033_onboarding_state.py`: onboarding table.
- `tests/foundation/test_runtime_config.py`: path validation and atomic storage.
- `tests/foundation/test_runtime_restart.py`: controlled launcher restart.
- `tests/onboarding/test_data_reset.py`: consent, snapshot, and object preservation.
- `tests/onboarding/test_onboarding_repository.py`: state transitions.
- `tests/onboarding/test_onboarding_api.py`: authenticated API and capability snapshot.

**Modify**

- `src/paper_reader/settings.py`: load runtime config into `AppPaths.default`.
- `src/paper_reader/cli.py`: controlled restart loop.
- `src/paper_reader/app.py`: onboarding service/router and restart controller.
- `src/paper_reader/agents/contracts.py`: structured Agent probe code.
- `src/paper_reader/agents/codex_cli.py`: distinguish invalid config from authentication.
- `src/paper_reader/agents/ollama.py`: structured local/cloud diagnostics.
- `src/paper_reader/settings_api.py`: expose diagnostic code and safe action.
- `src/paper_reader/migrations/versions/__init__.py`: frozen head revision.
- `tests/agents/test_codex_cli.py`: invalid config and version cases.
- `tests/agents/test_ollama.py`: stopped service and authentication cases.
- `tests/agents/test_agent_settings_api.py`: response diagnostic fields.
- `tests/foundation/test_cli.py`: restart behavior.
- `tests/foundation/test_migrations.py`: new migration head.
- `openapi/openapi.json`: generated onboarding API.
- `web/src/api/schema.d.ts`: generated TypeScript types.

## Task 1: Persist Validated Runtime Paths Outside SQLite

**Files:**

- Create: `src/paper_reader/runtime_config.py`
- Create: `tests/foundation/test_runtime_config.py`
- Modify: `src/paper_reader/settings.py`

**Interfaces:**

- Produces: `RuntimeConfigStore.load() -> RuntimeConfig` and `RuntimeConfigStore.save(RuntimeConfigUpdate) -> RuntimeConfig`.
- Consumes: fixed `%LOCALAPPDATA%\paper-reader` state root and optional absolute `cache_root`/`library_root`.

- [ ] **Step 1: Write failing path and atomic-write tests**

```python
from pathlib import Path

import pytest

from paper_reader.runtime_config import RuntimeConfigStore, RuntimeConfigUpdate


def test_runtime_config_persists_only_absolute_cache_and_library_roots(tmp_path: Path) -> None:
    store = RuntimeConfigStore(tmp_path / "state")
    saved = store.save(
        RuntimeConfigUpdate(
            cache_root=tmp_path / "cache",
            library_root=tmp_path / "papers",
        )
    )

    assert saved.version == 1
    assert store.load() == saved
    assert not (tmp_path / "state" / "runtime-config.json.part").exists()


def test_runtime_config_rejects_relative_or_state_nested_library(tmp_path: Path) -> None:
    store = RuntimeConfigStore(tmp_path / "state")

    with pytest.raises(ValueError, match="absolute"):
        store.save(RuntimeConfigUpdate(library_root=Path("papers")))
    with pytest.raises(ValueError, match="outside the state root"):
        store.save(RuntimeConfigUpdate(library_root=tmp_path / "state" / "papers"))
```

- [ ] **Step 2: Run the tests and verify the module is absent**

Run:

```powershell
uv run pytest tests/foundation/test_runtime_config.py -v
```

Expected: import failure for `paper_reader.runtime_config`.

- [ ] **Step 3: Implement the closed runtime-config schema and store**

```python
class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    cache_root: Path | None = None
    library_root: Path | None = None


class RuntimeConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cache_root: Path | None = None
    library_root: Path | None = None


class RuntimeConfigStore:
    def __init__(self, state_root: Path) -> None: ...
    def load(self) -> RuntimeConfig: ...
    def save(self, update: RuntimeConfigUpdate) -> RuntimeConfig: ...
```

Implementation requirements:

- Resolve and validate every configured path.
- Reject relative paths, the fixed state root, and descendants of the state root for `library_root`.
- Write UTF-8 canonical JSON to `.part`, flush, then publish with `os.replace`.
- Reject files larger than 64 KiB and unknown fields.
- Never store credentials.

Update `AppPaths.default()` to read this file after resolving the fixed state root; environment variables retain highest priority.

- [ ] **Step 4: Verify paths, malformed JSON, and environment precedence**

Run:

```powershell
uv run pytest tests/foundation/test_runtime_config.py tests/foundation/test_paths.py -v
```

Expected: all tests pass, including malformed/oversized config and environment overrides.

- [ ] **Step 5: Commit runtime configuration**

```powershell
git add -- src/paper_reader/runtime_config.py src/paper_reader/settings.py tests/foundation/test_runtime_config.py
git commit -m "feat(settings): persist validated runtime paths"
```

## Task 2: Stage and Apply a Consent-Based Database Rebuild

**Files:**

- Create: `src/paper_reader/runtime_control.py`
- Create: `src/paper_reader/onboarding/reset.py`
- Create: `tests/onboarding/test_data_reset.py`
- Create: `tests/foundation/test_runtime_restart.py`
- Modify: `src/paper_reader/cli.py`
- Modify: `src/paper_reader/app.py`
- Modify: `tests/foundation/test_cli.py`

**Interfaces:**

- Produces: `DataResetService.stage(confirmation: str) -> PendingDataReset`.
- Produces: `apply_pending_data_reset(paths: AppPaths) -> DataResetOutcome | None`.
- Produces: `RuntimeControl.request_restart(reason: str) -> None`.
- Confirmation literal: `REBUILD PAPER-READER DATA`.

- [ ] **Step 1: Write failing reset-safety tests**

```python
def test_pending_reset_snapshots_database_rebuilds_metadata_and_preserves_objects(
    tmp_path: Path,
) -> None:
    paths = seeded_paths(tmp_path)
    original_pdf = paths.objects / "aa" / "paper.pdf"
    original_pdf.parent.mkdir(parents=True)
    original_pdf.write_bytes(b"%PDF-1.7\nprivate fixture\n%%EOF")
    service = DataResetService(paths)

    pending = service.stage("REBUILD PAPER-READER DATA")
    outcome = apply_pending_data_reset(paths)

    assert outcome is not None
    assert outcome.request_id == pending.request_id
    assert outcome.snapshot_database.is_file()
    assert current_schema_revision(paths.database) == migration_head_revision()
    assert original_pdf.read_bytes().startswith(b"%PDF-1.7")
    assert not service.pending_path.exists()


def test_reset_rejects_wrong_confirmation_and_out_of_root_marker(tmp_path: Path) -> None:
    service = DataResetService(test_paths(tmp_path))
    with pytest.raises(DataResetError, match="confirmation"):
        service.stage("yes")
```

- [ ] **Step 2: Run tests and verify the reset service is absent**

Run:

```powershell
uv run pytest tests/onboarding/test_data_reset.py tests/foundation/test_runtime_restart.py -v
```

Expected: import failures for reset and runtime-control modules.

- [ ] **Step 3: Implement a signed-shape pending marker and startup rebuild**

Use a Pydantic payload:

```python
class PendingDataReset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["paper-reader-data-reset-v1"]
    request_id: UUID
    requested_at: datetime
    database_path_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preserve_objects: Literal[True] = True
```

`stage()` must:

- Require the exact confirmation literal.
- Hash the resolved database path instead of writing the path into diagnostics.
- Atomically write under `state_root/pending-data-reset.json`.
- Refuse to overwrite a different pending request.

`apply_pending_data_reset()` must run before SQLite opens:

1. Validate marker size, schema, path hash, and containment.
2. Create a migration snapshot and manifest.
3. Move the old database, WAL, and SHM into a request-specific rollback directory.
4. Create and migrate a fresh SQLite database.
5. Write a data-epoch receipt.
6. Remove the marker only after the new database is valid.
7. Never touch `library_root/objects`.

- [ ] **Step 4: Add a controlled launcher restart loop**

Implement:

```python
class RuntimeControl:
    def bind_shutdown(self, shutdown: Callable[[], None]) -> None: ...
    def request_restart(self, reason: str) -> None: ...
    @property
    def restart_requested(self) -> bool: ...
```

Refactor `_run_owned_server()` so one process can:

1. Create `RuntimeControl`.
2. Build the app with it.
3. Bind a callback that sets `server.should_exit = True`.
4. Run uvicorn.
5. Recreate `AppPaths` and the app only when `restart_requested` is true.
6. Limit restart chaining to two consecutive restarts and fail closed after that.

- [ ] **Step 5: Verify crash recovery and preserved objects**

Run:

```powershell
uv run pytest tests/onboarding/test_data_reset.py tests/foundation/test_runtime_restart.py tests/foundation/test_cli.py tests/platform/test_migration_snapshots.py -v
```

Expected: wrong confirmation, malformed marker, interrupted rebuild, restart loop, and PDF preservation tests all pass.

- [ ] **Step 6: Commit the reset boundary**

```powershell
git add -- src/paper_reader/runtime_control.py src/paper_reader/onboarding/reset.py src/paper_reader/cli.py src/paper_reader/app.py tests/onboarding/test_data_reset.py tests/foundation/test_runtime_restart.py tests/foundation/test_cli.py
git commit -m "feat(onboarding): add consent based data rebuild"
```

## Task 3: Persist the Four-Step Onboarding State

**Files:**

- Create: `src/paper_reader/onboarding/__init__.py`
- Create: `src/paper_reader/onboarding/contracts.py`
- Create: `src/paper_reader/onboarding/models.py`
- Create: `src/paper_reader/onboarding/repository.py`
- Create: `src/paper_reader/migrations/versions/0033_onboarding_state.py`
- Create: `tests/onboarding/test_onboarding_repository.py`
- Modify: `src/paper_reader/migrations/versions/__init__.py`
- Modify: `tests/foundation/test_migrations.py`

**Interfaces:**

- Produces: `OnboardingRepository.snapshot() -> OnboardingSnapshot`.
- Produces: `OnboardingRepository.save_step(OnboardingStepUpdate) -> OnboardingSnapshot`.
- Steps: `storage_privacy`, `agents`, `sources`, `reading_preferences`, `complete`.

- [ ] **Step 1: Write failing state-transition and migration tests**

```python
def test_onboarding_advances_in_order_and_allows_optional_skips(sessions) -> None:
    repository = OnboardingRepository(sessions)

    storage = repository.save_step(
        OnboardingStepUpdate(
            step=OnboardingStep.STORAGE_PRIVACY,
            status=OnboardingStepStatus.COMPLETED,
        )
    )
    agents = repository.save_step(
        OnboardingStepUpdate(
            step=OnboardingStep.AGENTS,
            status=OnboardingStepStatus.SKIPPED,
        )
    )

    assert storage.current_step is OnboardingStep.AGENTS
    assert agents.current_step is OnboardingStep.SOURCES
    assert agents.steps[OnboardingStep.AGENTS] is OnboardingStepStatus.SKIPPED


def test_storage_step_cannot_be_skipped(sessions) -> None:
    with pytest.raises(OnboardingTransitionError, match="storage"):
        OnboardingRepository(sessions).save_step(
            OnboardingStepUpdate(
                step=OnboardingStep.STORAGE_PRIVACY,
                status=OnboardingStepStatus.SKIPPED,
            )
        )
```

- [ ] **Step 2: Run the focused tests**

Run:

```powershell
uv run pytest tests/onboarding/test_onboarding_repository.py tests/foundation/test_migrations.py -v
```

Expected: missing onboarding package and migration-head failures.

- [ ] **Step 3: Implement immutable contracts and one singleton record**

```python
class OnboardingStep(StrEnum):
    STORAGE_PRIVACY = "storage_privacy"
    AGENTS = "agents"
    SOURCES = "sources"
    READING_PREFERENCES = "reading_preferences"
    COMPLETE = "complete"


class OnboardingStepStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class OnboardingSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_step: OnboardingStep
    completed: bool
    steps: dict[OnboardingStep, OnboardingStepStatus]
    privacy_acknowledged_at: datetime | None
    updated_at: datetime
```

Migration `0033_onboarding_state` creates one constrained `onboarding_state` record with JSON step statuses, current step, privacy acknowledgement, completion time, and timestamps.

- [ ] **Step 4: Implement transactional transition rules**

Rules:

- Storage/privacy must complete first and cannot be skipped.
- Optional steps may complete or skip only when they are current.
- Reopening a completed optional step is allowed without resetting later steps.
- `complete` is derived only when all four steps are completed/skipped as permitted.
- Repeated identical updates are idempotent.

- [ ] **Step 5: Verify migration and repository**

Run:

```powershell
uv run pytest tests/onboarding/test_onboarding_repository.py tests/foundation/test_migrations.py -v
uv run python -m paper_reader.openapi --output openapi/openapi.json
```

Expected: repository tests pass and Alembic has exactly one head, `0033_onboarding_state`.

- [ ] **Step 6: Commit onboarding persistence**

```powershell
git add -- src/paper_reader/onboarding src/paper_reader/migrations/versions/0033_onboarding_state.py src/paper_reader/migrations/versions/__init__.py tests/onboarding/test_onboarding_repository.py tests/foundation/test_migrations.py
git commit -m "feat(onboarding): persist guided setup progress"
```

## Task 4: Classify Agent Probe Failures Accurately

**Files:**

- Modify: `src/paper_reader/agents/contracts.py`
- Modify: `src/paper_reader/agents/codex_cli.py`
- Modify: `src/paper_reader/agents/ollama.py`
- Modify: `src/paper_reader/settings_api.py`
- Modify: `tests/agents/test_codex_cli.py`
- Modify: `tests/agents/test_ollama.py`
- Modify: `tests/agents/test_agent_settings_api.py`

**Interfaces:**

- Produces: `AgentProbeCode` and `AgentCapability.probe_code`.
- Public codes: `ready`, `not_installed`, `not_authenticated`, `configuration_invalid`, `version_unsupported`, `service_not_running`, `probe_timeout`, `unavailable`.
- Public response includes a safe Chinese `recommended_action`, never raw stderr or a home-directory path.

- [ ] **Step 1: Write failing Codex and Ollama diagnostic tests**

```python
@pytest.mark.asyncio
async def test_codex_invalid_config_is_not_reported_as_logged_out(tmp_path: Path) -> None:
    runner = ResultQueueRunner(
        version=process_result(stdout="codex-cli 0.115.0", exit_code=0),
        login=process_result(
            stderr="config.toml:3:26: unknown variant 'max'",
            exit_code=1,
        ),
    )
    backend = codex_backend(tmp_path, runner)

    capability = await backend.probe()

    assert capability.probe_code is AgentProbeCode.CONFIGURATION_INVALID
    assert capability.authenticated is False
    assert "codex_not_authenticated" not in capability.degradation_reasons


@pytest.mark.asyncio
async def test_stopped_ollama_service_has_specific_probe_code(tmp_path: Path) -> None:
    backend = ollama_backend_with_connection_refused(tmp_path)
    capability = await backend.probe()
    assert capability.probe_code is AgentProbeCode.SERVICE_NOT_RUNNING
```

- [ ] **Step 2: Run the diagnostic tests**

Run:

```powershell
uv run pytest tests/agents/test_codex_cli.py tests/agents/test_ollama.py tests/agents/test_agent_settings_api.py -v
```

Expected: failures because `probe_code` does not exist and Codex still adds `codex_not_authenticated`.

- [ ] **Step 3: Add the closed diagnostic enum**

```python
class AgentProbeCode(StrEnum):
    READY = "ready"
    NOT_INSTALLED = "not_installed"
    NOT_AUTHENTICATED = "not_authenticated"
    CONFIGURATION_INVALID = "configuration_invalid"
    VERSION_UNSUPPORTED = "version_unsupported"
    SERVICE_NOT_RUNNING = "service_not_running"
    PROBE_TIMEOUT = "probe_timeout"
    UNAVAILABLE = "unavailable"
```

Add `probe_code: AgentProbeCode` to `AgentCapability` and update every built-in and test backend explicitly.

- [ ] **Step 4: Implement fail-closed classification**

Codex precedence:

1. No executable → `not_installed`.
2. Version below minimum → `version_unsupported`.
3. Process timeout → `probe_timeout`.
4. Login command stderr matching TOML parse/config errors → `configuration_invalid`.
5. Valid command with logged-out result → `not_authenticated`.
6. Authenticated supported CLI → `ready`.
7. Other safe failure → `unavailable`.

Only persist stable codes. Apply `redact_diagnostic()` before any diagnostic enters an API response.

Ollama Local maps connection refused to `service_not_running`; Ollama Cloud maps missing credentials to `not_authenticated`.

- [ ] **Step 5: Expose safe recommended actions**

Extend `AgentCapabilityResponse`:

```python
class AgentCapabilityResponse(BaseModel):
    # existing fields
    probe_code: AgentProbeCode
    recommended_action: str | None
```

Map each code to fixed Chinese UI copy in `settings_api.py`; do not return raw process output.

- [ ] **Step 6: Verify all Agent tests**

Run:

```powershell
uv run pytest tests/agents -v
uv run mypy src scripts
uv run ruff check src/paper_reader/agents src/paper_reader/settings_api.py tests/agents
```

Expected: all Agent tests, strict typing, and lint pass.

- [ ] **Step 7: Commit diagnostic classification**

```powershell
git add -- src/paper_reader/agents/contracts.py src/paper_reader/agents/codex_cli.py src/paper_reader/agents/ollama.py src/paper_reader/settings_api.py tests/agents
git commit -m "fix(agents): classify capability probe failures"
```

## Task 5: Aggregate Onboarding Capabilities Behind One API

**Files:**

- Create: `src/paper_reader/onboarding/probes.py`
- Create: `src/paper_reader/onboarding/service.py`
- Create: `src/paper_reader/onboarding/api.py`
- Create: `tests/onboarding/test_onboarding_api.py`
- Modify: `src/paper_reader/app.py`
- Modify: `openapi/openapi.json`
- Modify: `web/src/api/schema.d.ts`

**Interfaces:**

- Produces: `GET /api/v1/onboarding`.
- Produces: `PUT /api/v1/onboarding/steps/{step}`.
- Produces: `POST /api/v1/onboarding/probe`.
- Produces: `POST /api/v1/onboarding/data-reset`.

- [ ] **Step 1: Write the failing authenticated API test**

```python
@pytest.mark.asyncio
async def test_onboarding_snapshot_is_usable_without_agents_or_keys(tmp_path: Path) -> None:
    app = create_app(
        paths=test_paths(tmp_path),
        bootstrap_token="secret",
        agent_registry=AgentBackendRegistry((unavailable_codex(), unavailable_ollama())),
    )
    async with bootstrapped_client(app, "secret") as client:
        response = await client.get("/api/v1/onboarding")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"]["current_step"] == "storage_privacy"
    assert payload["storage"]["writable"] is True
    assert payload["agents"][0]["probe_code"] == "not_installed"
    assert any(source["connector_id"] == "arxiv" and source["enabled"] for source in payload["sources"])
    assert payload["blocking_issues"] == []
```

Also test:

- Unauthenticated reads fail.
- Mutations require Origin and CSRF.
- Storage is the only blocking step.
- Probe does not write credentials or call generation.
- Reset requires the exact confirmation and returns `restart_required: true`.

- [ ] **Step 2: Run the onboarding API tests**

Run:

```powershell
uv run pytest tests/onboarding/test_onboarding_api.py -v
```

Expected: failure because the routes are missing.

- [ ] **Step 3: Implement the aggregate snapshot**

```python
class OnboardingOverview(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: OnboardingSnapshot
    storage: StorageCapability
    agents: tuple[AgentSetupCapability, ...]
    sources: tuple[SourceSetupCapability, ...]
    retrieval: RetrievalSetupCapability
    blocking_issues: tuple[str, ...]
```

Probe rules:

- Storage checks use a temporary file inside each configured root and remove it.
- Agent probes call only `probe()` and `discover_models()`, never `generate()`.
- Source status reuses connector capabilities and never makes a paper query.
- Retrieval reports lexical availability and optional model state.
- Results use stable codes and safe actions.

- [ ] **Step 4: Wire service, router, and restart request**

All routes depend on `require_local_session`; mutations additionally depend on `require_local_mutation`.

`POST /data-reset` must:

1. Stage the marker.
2. Return HTTP 202 with request ID and `restart_required: true`.
3. Request controlled restart only after the response body is committed through a background callback.

- [ ] **Step 5: Generate and verify API contracts**

Run:

```powershell
uv run pytest tests/onboarding/test_onboarding_api.py tests/foundation/test_api.py tests/foundation/test_security.py -v
uv run python -m paper_reader.openapi --output openapi/openapi.json
npm run api:types:from-schema
npm run typecheck
git diff --check
```

Expected: tests and typecheck pass, and generated schemas contain onboarding and diagnostic types.

- [ ] **Step 6: Commit the onboarding API**

```powershell
git add -- src/paper_reader/onboarding/probes.py src/paper_reader/onboarding/service.py src/paper_reader/onboarding/api.py src/paper_reader/app.py tests/onboarding/test_onboarding_api.py openapi/openapi.json web/src/api/schema.d.ts
git commit -m "feat(onboarding): expose setup and capability overview"
```

## Plan Completion Gate

Run:

```powershell
uv run pytest tests/onboarding tests/agents tests/foundation -v --basetemp=.tmp/pytest-m2
uv run ruff check .
uv run mypy src scripts
npm run typecheck
git diff --check
git status --short
```

Expected:

- Data reset always snapshots and preserves `library_root/objects`.
- Codex invalid configuration is distinct from logged-out state.
- No Agent generation occurs during probing.
- OpenAPI and TypeScript schemas are current.
- No push has occurred.
