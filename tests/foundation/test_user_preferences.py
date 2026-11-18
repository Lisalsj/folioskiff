from __future__ import annotations

import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.reader.test_extraction_api import bootstrap, local_client


def _paths(tmp_path: Path):  # type: ignore[no-untyped-def]
    from paper_reader.settings import AppPaths

    return AppPaths.from_overrides(
        state_root=tmp_path / "state",
        cache_root=tmp_path / "cache",
        library_root=tmp_path / "library",
    )


def _service(tmp_path: Path, provider=lambda: "America/New_York"):  # type: ignore[no-untyped-def]
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.migrations import upgrade_database
    from paper_reader.user_preferences import UserPreferencesService

    paths = _paths(tmp_path)
    paths.ensure()
    upgrade_database(paths.database)
    engine = create_sqlite_engine(paths.database)
    return engine, UserPreferencesService(
        create_session_factory(engine),
        system_timezone_provider=provider,
    )


def test_system_timezone_defaults_validates_iana_and_falls_back_explicitly(tmp_path: Path) -> None:
    from paper_reader.user_preferences import (
        FALLBACK_TIMEZONE,
        UserPreferencesUpdate,
        resolve_system_timezone,
    )

    engine, service = _service(tmp_path)
    try:
        initial = service.snapshot()
        assert initial.timezone_mode == "system"
        assert initial.timezone_name is None
        assert initial.system_timezone == "America/New_York"
        assert initial.resolved_timezone == "America/New_York"
        assert initial.system_timezone_fallback is False
        assert initial.theme == "system"
        assert initial.default_agent_backend == "codex_cli"
        assert initial.default_agent_model_id is None
        assert initial.default_reasoning_effort == "medium"
        assert initial.report_language == "zh-CN"
        assert initial.translation_source_language == "en"
        assert initial.translation_target_language == "zh-CN"

        invalid = resolve_system_timezone(lambda: "Not/A-Timezone")
        assert invalid.name == FALLBACK_TIMEZONE == "Asia/Shanghai"
        assert invalid.fallback is True

        failed = resolve_system_timezone(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert failed.name == FALLBACK_TIMEZONE
        assert failed.fallback is True

        with pytest.raises(ValidationError, match="IANA"):
            UserPreferencesUpdate(
                timezone_mode="custom",
                timezone_name="C:/private/timezone",
                theme="light",
            )
        with pytest.raises(ValidationError):
            UserPreferencesUpdate(
                timezone_mode="custom",
                timezone_name="Europe/Berlin",
                theme="sepia",  # type: ignore[arg-type]
            )
        with pytest.raises(ValidationError, match="must be empty"):
            UserPreferencesUpdate(
                timezone_mode="system",
                timezone_name="Europe/Berlin",
                theme="system",
            )
        with pytest.raises(ValidationError, match="must be different"):
            UserPreferencesUpdate(
                timezone_mode="system",
                timezone_name=None,
                theme="system",
                translation_source_language="en",
                translation_target_language="en",
            )
        with pytest.raises(ValidationError, match="control characters"):
            UserPreferencesUpdate(
                timezone_mode="system",
                timezone_name=None,
                theme="system",
                default_agent_model_id="model\nignore",
            )
    finally:
        engine.dispose()


def test_preferences_are_durable_and_custom_timezone_wins_over_system(tmp_path: Path) -> None:
    from paper_reader.user_preferences import UserPreferencesUpdate

    engine, service = _service(tmp_path, lambda: "America/Los_Angeles")
    try:
        saved = service.save(
            UserPreferencesUpdate(
                timezone_mode="custom",
                timezone_name="Europe/Berlin",
                theme="dark",
            )
        )
        assert saved.resolved_timezone == "Europe/Berlin"
        assert saved.system_timezone == "America/Los_Angeles"
        assert saved.theme == "dark"
    finally:
        engine.dispose()

    restarted_engine, restarted = _service(tmp_path, lambda: "Asia/Tokyo")
    try:
        restored = restarted.snapshot()
        assert restored.timezone_mode == "custom"
        assert restored.timezone_name == "Europe/Berlin"
        assert restored.resolved_timezone == "Europe/Berlin"
        assert restored.system_timezone == "Asia/Tokyo"
        assert restored.theme == "dark"
    finally:
        restarted_engine.dispose()


def test_agent_and_translation_defaults_round_trip(tmp_path: Path) -> None:
    from paper_reader.user_preferences import UserPreferencesUpdate

    engine, service = _service(tmp_path)
    try:
        saved = service.save(
            UserPreferencesUpdate(
                timezone_mode="system",
                timezone_name=None,
                theme="system",
                default_agent_backend="ollama_local",
                default_agent_model_id="qwen3:14b",
                default_reasoning_effort="high",
                report_language="bilingual",
                translation_source_language="en",
                translation_target_language="zh-CN",
            )
        )
        assert saved.default_agent_backend == "ollama_local"
        assert saved.default_agent_model_id == "qwen3:14b"
        assert saved.default_reasoning_effort == "high"
        assert saved.report_language == "bilingual"
        assert saved.translation_source_language == "en"
        assert saved.translation_target_language == "zh-CN"
    finally:
        engine.dispose()

    restarted_engine, restarted = _service(tmp_path)
    try:
        restored = restarted.snapshot()
        assert restored.default_agent_backend == "ollama_local"
        assert restored.default_agent_model_id == "qwen3:14b"
        assert restored.translation_target_language == "zh-CN"
    finally:
        restarted_engine.dispose()


@pytest.mark.asyncio
async def test_preferences_api_is_closed_local_and_freezes_plan_timezone(tmp_path: Path) -> None:
    from paper_reader.app import create_app
    from paper_reader.discovery.keywords import (
        ConceptGroup,
        KeywordRepository,
        KeywordSetDefinition,
    )

    app = create_app(
        paths=_paths(tmp_path),
        bootstrap_token="secret",
        system_timezone_provider=lambda: "America/New_York",
    )
    async with local_client(app) as client:
        assert (await client.get("/api/v1/settings/preferences")).status_code == 401
        headers = await bootstrap(client)

        initial = await client.get("/api/v1/settings/preferences")
        assert initial.status_code == 200
        assert initial.json()["resolved_timezone"] == "America/New_York"
        assert (await client.put(
            "/api/v1/settings/preferences",
            json={
                "timezone_mode": "custom",
                "timezone_name": "Europe/Berlin",
                "theme": "dark",
                "default_agent_backend": "ollama_local",
                "default_agent_model_id": "qwen3:14b",
                "default_reasoning_effort": "high",
                "report_language": "bilingual",
                "translation_source_language": "en",
                "translation_target_language": "zh-CN",
            },
        )).status_code == 403
        saved = await client.put(
            "/api/v1/settings/preferences",
            headers=headers,
            json={
                "timezone_mode": "custom",
                "timezone_name": "Europe/Berlin",
                "theme": "dark",
                "default_agent_backend": "ollama_local",
                "default_agent_model_id": "qwen3:14b",
                "default_reasoning_effort": "high",
                "report_language": "bilingual",
                "translation_source_language": "en",
                "translation_target_language": "zh-CN",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["resolved_timezone"] == "Europe/Berlin"
        assert saved.json()["default_agent_backend"] == "ollama_local"
        assert saved.json()["default_agent_model_id"] == "qwen3:14b"
        assert saved.json()["report_language"] == "bilingual"

        invalid = await client.put(
            "/api/v1/settings/preferences",
            headers=headers,
            json={
                "timezone_mode": "custom",
                "timezone_name": "Europe/Berlin",
                "theme": "dark",
                "default_agent_backend": "ollama_local",
                "default_agent_model_id": "qwen3:14b",
                "default_reasoning_effort": "high",
                "report_language": "bilingual",
                "translation_source_language": "en",
                "translation_target_language": "zh-CN",
                "custom_css": "body { display: none }",
            },
        )
        assert invalid.status_code == 422

        keywords = KeywordRepository(app.state.sessions)
        version = keywords.create_version(
            research_intent="agent research",
            definition=KeywordSetDefinition(
                required=(ConceptGroup(key="agent", label="智能体", terms=("AI agent",)),)
            ),
        )
        keywords.confirm(version.id)
        created = await client.post(
            "/api/v1/plans",
            headers=headers,
            json={
                "name": "冻结时区",
                "research_intent": "agent research",
                "keyword_version_id": version.id,
                "date_range": {"mode": "rolling", "rolling_months": 1},
                "sources": ["arxiv"],
            },
        )
        assert created.status_code == 201
        assert created.json()["date_range"]["timezone"] == "Europe/Berlin"

        await client.put(
            "/api/v1/settings/preferences",
            headers=headers,
            json={
                "timezone_mode": "custom",
                "timezone_name": "Asia/Tokyo",
                "theme": "light",
                "default_agent_backend": "ollama_local",
                "default_agent_model_id": "qwen3:14b",
                "default_reasoning_effort": "high",
                "report_language": "bilingual",
                "translation_source_language": "en",
                "translation_target_language": "zh-CN",
            },
        )
        plans = await client.get("/api/v1/plans")
        assert plans.json()[0]["date_range"]["timezone"] == "Europe/Berlin"


def test_preferences_are_in_the_portable_database_backup(tmp_path: Path) -> None:
    from paper_reader.backups.archive import BackupArchiveBuilder
    from paper_reader.user_preferences import UserPreferencesUpdate

    paths = _paths(tmp_path)
    engine, service = _service(tmp_path)
    try:
        service.save(
            UserPreferencesUpdate(
                timezone_mode="custom",
                timezone_name="Pacific/Auckland",
                theme="light",
                default_agent_backend="ollama_cloud",
                default_agent_model_id="qwen3.5:cloud",
                default_reasoning_effort="xhigh",
                report_language="bilingual",
                translation_source_language="en",
                translation_target_language="zh-CN",
            )
        )
    finally:
        engine.dispose()

    destination = paths.backups / "preferences.paper-reader-backup"
    BackupArchiveBuilder(
        paths=paths,
        clock=lambda: datetime(2026, 7, 15, 8, tzinfo=UTC),
    ).create(destination=destination)
    with zipfile.ZipFile(destination) as archive:
        restored = tmp_path / "restored.sqlite3"
        restored.write_bytes(archive.read("database/paper-reader.sqlite3"))
    connection = sqlite3.connect(restored)
    try:
        row = connection.execute(
            "SELECT timezone_mode, timezone_name, theme, default_agent_backend, "
            "default_agent_model_id, default_reasoning_effort, report_language, "
            "translation_source_language, translation_target_language "
            "FROM user_preferences WHERE id = 'default'"
        ).fetchone()
    finally:
        connection.close()
    assert row == (
        "custom",
        "Pacific/Auckland",
        "light",
        "ollama_cloud",
        "qwen3.5:cloud",
        "xhigh",
        "bilingual",
        "en",
        "zh-CN",
    )


def test_month_resolution_handles_utc_cross_month_and_dst_boundaries() -> None:
    from paper_reader.discovery.plans import DateRangeSpec

    utc_cross_month = datetime(2026, 4, 1, 2, 30, tzinfo=UTC)
    new_york = DateRangeSpec(
        mode="rolling", rolling_months=1, timezone="America/New_York"
    ).resolve(utc_cross_month)
    tokyo = DateRangeSpec(
        mode="rolling", rolling_months=1, timezone="Asia/Tokyo"
    ).resolve(utc_cross_month)
    assert new_york.months == ("2026-03",)
    assert tokyo.months == ("2026-04",)

    march = DateRangeSpec(
        mode="fixed",
        start_month="2026-03",
        end_month="2026-03",
        timezone="America/New_York",
    ).resolve(datetime(2026, 3, 1, tzinfo=UTC))
    assert march.contains_updated_at(datetime(2026, 3, 8, 6, 59, tzinfo=UTC))
    assert march.contains_updated_at(datetime(2026, 3, 8, 7, 1, tzinfo=UTC))
