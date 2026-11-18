"""Durable, closed user preferences for timezone and visual theme."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Self, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import CheckConstraint, DateTime, String
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from paper_reader.database import Base

FALLBACK_TIMEZONE = "Asia/Shanghai"
TimezoneMode = Literal["system", "custom"]
ThemeMode = Literal["system", "light", "dark"]
DefaultAgentBackend = Literal["codex_cli", "ollama_local", "ollama_cloud"]
DefaultReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh"]
ReportLanguage = Literal["zh-CN", "en", "bilingual"]
TranslationLanguage = Literal["en", "zh-CN"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class UserPreferencesRecord(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (
        CheckConstraint("id = 'default'", name="ck_user_preferences_singleton"),
        CheckConstraint(
            "timezone_mode IN ('system', 'custom')",
            name="ck_user_preferences_timezone_mode",
        ),
        CheckConstraint(
            "theme IN ('system', 'light', 'dark')",
            name="ck_user_preferences_theme",
        ),
        CheckConstraint(
            "default_agent_backend IN ('codex_cli', 'ollama_local', 'ollama_cloud')",
            name="ck_user_preferences_agent_backend",
        ),
        CheckConstraint(
            "default_agent_model_id IS NULL OR "
            "(length(default_agent_model_id) > 0 AND length(default_agent_model_id) <= 200)",
            name="ck_user_preferences_agent_model_id",
        ),
        CheckConstraint(
            "default_reasoning_effort IN ('minimal', 'low', 'medium', 'high', 'xhigh')",
            name="ck_user_preferences_reasoning_effort",
        ),
        CheckConstraint(
            "report_language IN ('zh-CN', 'en', 'bilingual')",
            name="ck_user_preferences_report_language",
        ),
        CheckConstraint(
            "translation_source_language IN ('en', 'zh-CN')",
            name="ck_user_preferences_translation_source_language",
        ),
        CheckConstraint(
            "translation_target_language IN ('en', 'zh-CN')",
            name="ck_user_preferences_translation_target_language",
        ),
        CheckConstraint(
            "translation_source_language <> translation_target_language",
            name="ck_user_preferences_translation_pair",
        ),
        CheckConstraint(
            "(timezone_mode = 'system' AND timezone_name IS NULL) OR "
            "(timezone_mode = 'custom' AND timezone_name IS NOT NULL "
            "AND length(timezone_name) > 0)",
            name="ck_user_preferences_timezone_value",
        ),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="default")
    timezone_mode: Mapped[str] = mapped_column(String(16), default="system")
    timezone_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    theme: Mapped[str] = mapped_column(String(16), default="system")
    default_agent_backend: Mapped[str] = mapped_column(
        String(24), default="codex_cli", server_default="codex_cli"
    )
    default_agent_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    default_reasoning_effort: Mapped[str] = mapped_column(
        String(16), default="medium", server_default="medium"
    )
    report_language: Mapped[str] = mapped_column(
        String(16), default="zh-CN", server_default="zh-CN"
    )
    translation_source_language: Mapped[str] = mapped_column(
        String(16), default="en", server_default="en"
    )
    translation_target_language: Mapped[str] = mapped_column(
        String(16), default="zh-CN", server_default="zh-CN"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )


@dataclass(frozen=True, slots=True)
class ResolvedSystemTimezone:
    name: str
    fallback: bool


def _local_timezone_name() -> str:
    from tzlocal import get_localzone_name

    return get_localzone_name()


def resolve_system_timezone(
    provider: Callable[[], str] | None = None,
) -> ResolvedSystemTimezone:
    try:
        value = (provider or _local_timezone_name)()
        if not isinstance(value, str) or not value or len(value) > 80:
            raise ValueError("system timezone name is invalid")
        ZoneInfo(value)
        return ResolvedSystemTimezone(name=value, fallback=False)
    except (OSError, RuntimeError, TypeError, ValueError, ZoneInfoNotFoundError):
        return ResolvedSystemTimezone(name=FALLBACK_TIMEZONE, fallback=True)


def _validate_iana_timezone(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 80:
        raise ValueError("timezone_name must be a valid IANA timezone")
    try:
        ZoneInfo(normalized)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("timezone_name must be a valid IANA timezone") from error
    return normalized


class UserPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timezone_mode: TimezoneMode
    timezone_name: str | None = Field(default=None, max_length=80)
    theme: ThemeMode
    default_agent_backend: DefaultAgentBackend = "codex_cli"
    default_agent_model_id: str | None = Field(default=None, max_length=200)
    default_reasoning_effort: DefaultReasoningEffort = "medium"
    report_language: ReportLanguage = "zh-CN"
    translation_source_language: TranslationLanguage = "en"
    translation_target_language: TranslationLanguage = "zh-CN"

    @field_validator("default_agent_model_id", mode="before")
    @classmethod
    def normalize_model_id(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            return None
        if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
            raise ValueError("default_agent_model_id must not contain control characters")
        return normalized

    @model_validator(mode="after")
    def validate_timezone(self) -> Self:
        if self.translation_source_language == self.translation_target_language:
            raise ValueError("translation source and target languages must be different")
        if self.timezone_mode == "system":
            if self.timezone_name is not None:
                raise ValueError("timezone_name must be empty in system mode")
            return self
        if self.timezone_name is None:
            raise ValueError("custom timezone mode requires an IANA timezone_name")
        object.__setattr__(self, "timezone_name", _validate_iana_timezone(self.timezone_name))
        return self


class UserPreferencesSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timezone_mode: TimezoneMode
    timezone_name: str | None
    system_timezone: str
    system_timezone_fallback: bool
    resolved_timezone: str
    theme: ThemeMode
    default_agent_backend: DefaultAgentBackend
    default_agent_model_id: str | None
    default_reasoning_effort: DefaultReasoningEffort
    report_language: ReportLanguage
    translation_source_language: TranslationLanguage
    translation_target_language: TranslationLanguage
    updated_at: datetime


class UserPreferencesService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        system_timezone_provider: Callable[[], str] | None = None,
    ) -> None:
        self._sessions = sessions
        self._system_timezone_provider = system_timezone_provider

    def snapshot(self) -> UserPreferencesSnapshot:
        with self._sessions() as session:
            record = self._required(session)
            session.commit()
            session.refresh(record)
            return self._snapshot(record)

    def save(self, payload: UserPreferencesUpdate) -> UserPreferencesSnapshot:
        with self._sessions() as session:
            record = self._required(session)
            record.timezone_mode = payload.timezone_mode
            record.timezone_name = payload.timezone_name
            record.theme = payload.theme
            record.default_agent_backend = payload.default_agent_backend
            record.default_agent_model_id = payload.default_agent_model_id
            record.default_reasoning_effort = payload.default_reasoning_effort
            record.report_language = payload.report_language
            record.translation_source_language = payload.translation_source_language
            record.translation_target_language = payload.translation_target_language
            record.updated_at = _utc_now()
            session.commit()
            session.refresh(record)
            return self._snapshot(record)

    def resolved_timezone(self) -> str:
        return self.snapshot().resolved_timezone

    @staticmethod
    def _required(session: Session) -> UserPreferencesRecord:
        record = session.get(UserPreferencesRecord, "default")
        if record is None:
            record = UserPreferencesRecord(id="default")
            session.add(record)
            session.flush()
        return record

    def _snapshot(self, record: UserPreferencesRecord) -> UserPreferencesSnapshot:
        system = resolve_system_timezone(self._system_timezone_provider)
        mode = cast(TimezoneMode, record.timezone_mode)
        theme = cast(ThemeMode, record.theme)
        default_agent_backend = cast(DefaultAgentBackend, record.default_agent_backend)
        default_reasoning_effort = cast(
            DefaultReasoningEffort, record.default_reasoning_effort
        )
        report_language = cast(ReportLanguage, record.report_language)
        translation_source_language = cast(
            TranslationLanguage, record.translation_source_language
        )
        translation_target_language = cast(
            TranslationLanguage, record.translation_target_language
        )
        resolved = record.timezone_name if mode == "custom" else system.name
        if resolved is None:  # pragma: no cover - protected by database constraint
            resolved = system.name
        return UserPreferencesSnapshot(
            timezone_mode=mode,
            timezone_name=record.timezone_name,
            system_timezone=system.name,
            system_timezone_fallback=system.fallback,
            resolved_timezone=resolved,
            theme=theme,
            default_agent_backend=default_agent_backend,
            default_agent_model_id=record.default_agent_model_id,
            default_reasoning_effort=default_reasoning_effort,
            report_language=report_language,
            translation_source_language=translation_source_language,
            translation_target_language=translation_target_language,
            updated_at=_aware_utc(record.updated_at),
        )
