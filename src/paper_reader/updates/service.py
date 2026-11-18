"""Strict GitHub Release metadata checks without artifact downloads."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import total_ordering
from typing import Any, Literal, Self, cast
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.updates.models import UpdateSettingsRecord

RELEASE_REPOSITORY = "Lisalsj/folioskiff"
LATEST_RELEASE_ENDPOINT = f"https://api.github.com/repos/{RELEASE_REPOSITORY}/releases/latest"
GITHUB_API_VERSION = "2026-03-10"
CHECK_INTERVAL = timedelta(hours=24)
MAX_RESPONSE_BYTES = 512 * 1024
MAX_HEADER_BYTES = 64 * 1024
_MANIFEST_START = "<!-- paper-reader-update-manifest:v1\n"
_MANIFEST_END = "\n-->"
_SEMVER = re.compile(
    r"^(?:v)?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_CAPABILITY_KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

PersistedStatus = Literal[
    "never_checked",
    "up_to_date",
    "update_available",
    "server_older",
    "prerelease",
    "draft",
    "rate_limited",
    "offline",
    "invalid_response",
    "service_unavailable",
]
UpdateStatus = Literal[
    "disabled",
    "never_checked",
    "up_to_date",
    "update_available",
    "server_older",
    "prerelease",
    "draft",
    "rate_limited",
    "offline",
    "invalid_response",
    "service_unavailable",
]


@total_ordering
@dataclass(frozen=True, slots=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[int | str, ...] = ()
    build: tuple[str, ...] = field(default=(), compare=False)

    @classmethod
    def parse(cls, value: str) -> Self:
        if not isinstance(value, str):
            raise ValueError("version must be a string")
        match = _SEMVER.fullmatch(value)
        if match is None:
            raise ValueError("version is not strict SemVer")
        prerelease: tuple[int | str, ...] = tuple(
            int(part) if part.isdigit() else part
            for part in (match.group("prerelease") or "").split(".")
            if part
        )
        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=prerelease,
            build=tuple((match.group("build") or "").split(".")),
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if core != other_core:
            return core < other_core
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            if isinstance(left, int) and isinstance(right, str):
                return True
            if isinstance(left, str) and isinstance(right, int):
                return False
            if isinstance(left, int) and isinstance(right, int):
                return left < right
            return cast(str, left) < cast(str, right)
        return len(self.prerelease) < len(other.prerelease)


class ReleaseAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    size_bytes: int = Field(ge=0, le=20 * 1024 * 1024 * 1024)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ReleaseMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    tag_name: str
    published_at: datetime
    html_url: str
    changelog: str
    migration_notes: str
    capability_matrix: dict[str, str]
    assets: tuple[ReleaseAsset, ...]
    unsigned: bool


class UpdateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_version: str
    automatic_checks_enabled: bool
    status: UpdateStatus
    checked: bool
    last_checked_at: datetime | None
    next_automatic_check_at: datetime | None
    error_code: str | None
    release: ReleaseMetadata | None


class _ReleaseSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["paper-reader-update-v1"]
    version: str = Field(min_length=5, max_length=64)
    changelog: str = Field(min_length=1, max_length=20_000)
    migration_notes: str = Field(min_length=1, max_length=10_000)
    capability_matrix: dict[str, str]
    required_assets: tuple[str, ...] = Field(min_length=1, max_length=32)
    signed: bool

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if value.startswith("v"):
            raise ValueError("manifest version must not have a prefix")
        SemanticVersion.parse(value)
        return value

    @field_validator("changelog", "migration_notes")
    @classmethod
    def validate_plain_text(cls, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized or _CONTROL.search(normalized) or "<" in normalized or ">" in normalized:
            raise ValueError("release text must be plain text")
        return normalized

    @field_validator("capability_matrix")
    @classmethod
    def validate_capability_matrix(cls, value: dict[str, str]) -> dict[str, str]:
        if not 1 <= len(value) <= 40:
            raise ValueError("capability matrix size is invalid")
        normalized: dict[str, str] = {}
        for key, status in value.items():
            if _CAPABILITY_KEY.fullmatch(key) is None:
                raise ValueError("capability key is invalid")
            if not isinstance(status, str) or not 1 <= len(status) <= 80:
                raise ValueError("capability status is invalid")
            if _CONTROL.search(status) or "<" in status or ">" in status:
                raise ValueError("capability status must be plain text")
            normalized[key] = status
        return normalized

    @field_validator("required_assets")
    @classmethod
    def validate_required_assets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("required asset names must be unique")
        if "release-manifest.json" not in value:
            raise ValueError("release manifest asset is required")
        if any(_SAFE_NAME.fullmatch(name) is None for name in value):
            raise ValueError("required asset name is invalid")
        return value


@dataclass(frozen=True, slots=True)
class _StoredState:
    automatic_checks_enabled: bool = True
    last_checked_at: datetime | None = None
    last_status: PersistedStatus = "never_checked"
    last_error_code: str | None = None
    release: ReleaseMetadata | None = None


class _UpdateRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get(self) -> _StoredState:
        with self._sessions() as session:
            return self._domain(session.get(UpdateSettingsRecord, "default"))

    def set_enabled(self, enabled: bool) -> _StoredState:
        with self._sessions() as session:
            record = self._required(session)
            record.automatic_checks_enabled = enabled
            session.commit()
            session.refresh(record)
            return self._domain(record)

    def record_check(
        self,
        *,
        checked_at: datetime,
        status: PersistedStatus,
        error_code: str | None,
        release: ReleaseMetadata | None,
    ) -> _StoredState:
        with self._sessions() as session:
            record = self._required(session)
            record.last_checked_at = checked_at
            record.last_status = status
            record.last_error_code = error_code
            if release is not None:
                record.cached_release_json = release.model_dump(mode="json")
            session.commit()
            session.refresh(record)
            return self._domain(record)

    def clear(self) -> _StoredState:
        with self._sessions() as session:
            record = self._required(session)
            record.last_checked_at = None
            record.last_status = "never_checked"
            record.last_error_code = None
            record.cached_release_json = None
            session.commit()
            session.refresh(record)
            return self._domain(record)

    @staticmethod
    def _required(session: Session) -> UpdateSettingsRecord:
        record = session.get(UpdateSettingsRecord, "default")
        if record is None:
            record = UpdateSettingsRecord(id="default")
            session.add(record)
            session.flush()
        return record

    @staticmethod
    def _domain(record: UpdateSettingsRecord | None) -> _StoredState:
        if record is None:
            return _StoredState()
        release = None
        if record.cached_release_json is not None:
            try:
                release = ReleaseMetadata.model_validate(record.cached_release_json)
            except ValidationError:
                release = None
        return _StoredState(
            automatic_checks_enabled=record.automatic_checks_enabled,
            last_checked_at=_aware_utc(record.last_checked_at),
            last_status=cast(PersistedStatus, record.last_status),
            last_error_code=record.last_error_code,
            release=release,
        )


class _UpdateFailure(Exception):
    def __init__(self, status: PersistedStatus, error_code: str) -> None:
        super().__init__(error_code)
        self.status = status
        self.error_code = error_code


class UpdateService:
    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        current_version: str,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        SemanticVersion.parse(current_version)
        self._repository = _UpdateRepository(sessions)
        self._current_version = current_version.removeprefix("v")
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    def snapshot(self) -> UpdateSnapshot:
        return self._snapshot(self._repository.get(), checked=False)

    def set_automatic_checks(self, enabled: bool) -> UpdateSnapshot:
        return self._snapshot(self._repository.set_enabled(enabled), checked=False)

    def clear_cache(self) -> UpdateSnapshot:
        return self._snapshot(self._repository.clear(), checked=False)

    async def check_if_due(self) -> UpdateSnapshot:
        return await self.check(manual=False)

    async def check(self, *, manual: bool) -> UpdateSnapshot:
        async with self._lock:
            state = self._repository.get()
            now = _aware_utc(self._clock())
            if now is None:  # pragma: no cover - clock contract
                raise RuntimeError("update clock returned no value")
            if not manual:
                if not state.automatic_checks_enabled:
                    return self._snapshot(state, checked=False)
                if (
                    state.last_checked_at is not None
                    and now - state.last_checked_at < CHECK_INTERVAL
                ):
                    return self._snapshot(state, checked=False)
            try:
                release, status = await self._fetch_release()
                state = self._repository.record_check(
                    checked_at=now,
                    status=status,
                    error_code=None,
                    release=release,
                )
                return self._snapshot(
                    state, checked=True, status_override=status, error_override=None
                )
            except _UpdateFailure as error:
                state = self._repository.record_check(
                    checked_at=now,
                    status=error.status,
                    error_code=error.error_code,
                    release=None,
                )
                return self._snapshot(
                    state,
                    checked=True,
                    status_override=error.status,
                    error_override=error.error_code,
                )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _fetch_release(self) -> tuple[ReleaseMetadata, PersistedStatus]:
        client = self._http_client()
        # Even if a public endpoint unexpectedly sets a cookie, never echo it
        # on a later update check.
        client.cookies.clear()
        try:
            async with client.stream(
                "GET",
                LATEST_RELEASE_ENDPOINT,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": GITHUB_API_VERSION,
                    "User-Agent": f"paper-reader/{self._current_version}",
                    "Accept-Encoding": "identity",
                },
            ) as response:
                chunks = await _bounded_response_body(response)
        except _UpdateFailure:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise _UpdateFailure("offline", "offline") from error
        except httpx.HTTPError as error:
            raise _UpdateFailure("invalid_response", "transport_error") from error
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise _UpdateFailure("invalid_response", "invalid_json") from error
        try:
            release, draft, prerelease = _parse_release(payload)
        except (TypeError, ValueError, ValidationError) as error:
            raise _UpdateFailure("invalid_response", "invalid_release_metadata") from error
        if draft:
            return release, "draft"
        if prerelease:
            return release, "prerelease"
        remote = SemanticVersion.parse(release.version)
        current = SemanticVersion.parse(self._current_version)
        if remote > current:
            return release, "update_available"
        if remote < current:
            return release, "server_older"
        return release, "up_to_date"

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(8, connect=4, read=5, write=5, pool=4),
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    def _snapshot(
        self,
        state: _StoredState,
        *,
        checked: bool,
        status_override: PersistedStatus | None = None,
        error_override: str | None = None,
    ) -> UpdateSnapshot:
        status: UpdateStatus
        if status_override is not None:
            status = status_override
        elif not state.automatic_checks_enabled:
            status = "disabled"
        elif state.release is not None and state.last_status in {
            "up_to_date",
            "update_available",
            "server_older",
        }:
            status = self._version_relationship(state.release)
        else:
            status = state.last_status
        next_check = (
            state.last_checked_at + CHECK_INTERVAL
            if state.automatic_checks_enabled and state.last_checked_at is not None
            else None
        )
        return UpdateSnapshot(
            current_version=self._current_version,
            automatic_checks_enabled=state.automatic_checks_enabled,
            status=status,
            checked=checked,
            last_checked_at=state.last_checked_at,
            next_automatic_check_at=next_check,
            error_code=error_override if status_override is not None else state.last_error_code,
            release=state.release,
        )

    def _version_relationship(self, release: ReleaseMetadata) -> PersistedStatus:
        remote = SemanticVersion.parse(release.version)
        current = SemanticVersion.parse(self._current_version)
        if remote > current:
            return "update_available"
        if remote < current:
            return "server_older"
        return "up_to_date"


async def _bounded_response_body(response: httpx.Response) -> list[bytes]:
    if sum(len(key) + len(value) for key, value in response.headers.items()) > MAX_HEADER_BYTES:
        raise _UpdateFailure("invalid_response", "headers_too_large")
    if response.status_code in {403, 429} and (
        response.status_code == 429 or response.headers.get("X-RateLimit-Remaining") == "0"
    ):
        raise _UpdateFailure("rate_limited", "rate_limited")
    if 500 <= response.status_code <= 599:
        raise _UpdateFailure("service_unavailable", "github_unavailable")
    if response.status_code != 200:
        code = "redirect_not_allowed" if 300 <= response.status_code <= 399 else "unexpected_status"
        raise _UpdateFailure("invalid_response", code)
    content_type = response.headers.get("Content-Type", "").partition(";")[0].strip()
    if content_type not in {"application/json", "application/vnd.github+json"}:
        raise _UpdateFailure("invalid_response", "invalid_content_type")
    declared_size = response.headers.get("Content-Length")
    if declared_size is not None:
        try:
            size = int(declared_size)
        except ValueError as error:
            raise _UpdateFailure("invalid_response", "invalid_content_length") from error
        if size < 0 or size > MAX_RESPONSE_BYTES:
            raise _UpdateFailure("invalid_response", "response_too_large")
    chunks: list[bytes] = []
    received = 0
    async for chunk in response.aiter_bytes():
        received += len(chunk)
        if received > MAX_RESPONSE_BYTES:
            raise _UpdateFailure("invalid_response", "response_too_large")
        chunks.append(chunk)
    return chunks


def _parse_release(payload: Any) -> tuple[ReleaseMetadata, bool, bool]:
    if not isinstance(payload, dict):
        raise ValueError("release response must be an object")
    tag = _required_string(payload, "tag_name", 64)
    draft = _required_bool(payload, "draft")
    prerelease = _required_bool(payload, "prerelease")
    tag_version = SemanticVersion.parse(tag)
    summary = _parse_summary(_required_string(payload, "body", MAX_RESPONSE_BYTES))
    if tag.removeprefix("v") != summary.version:
        raise ValueError("release tag and manifest version differ")
    manifest_version = SemanticVersion.parse(summary.version)
    if tag_version != manifest_version or bool(manifest_version.prerelease) != prerelease:
        raise ValueError("release version metadata is inconsistent")
    html_url = _required_string(payload, "html_url", 500)
    _validate_release_url(html_url, tag)
    published_at = _parse_datetime(_required_string(payload, "published_at", 64))
    assets_payload = payload.get("assets")
    if not isinstance(assets_payload, list) or not 1 <= len(assets_payload) <= 100:
        raise ValueError("release assets are invalid")
    assets: list[ReleaseAsset] = []
    names: set[str] = set()
    for item in assets_payload:
        if not isinstance(item, dict):
            raise ValueError("release asset must be an object")
        name = _required_string(item, "name", 200)
        if _SAFE_NAME.fullmatch(name) is None or name in names:
            raise ValueError("release asset name is invalid")
        names.add(name)
        size = item.get("size")
        if not isinstance(size, int) or isinstance(size, bool):
            raise ValueError("release asset size is invalid")
        _validate_asset_url(_required_string(item, "browser_download_url", 1000), tag, name)
        digest = item.get("digest")
        sha256 = None
        if digest is not None:
            if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
                raise ValueError("release asset digest is invalid")
            sha256 = digest.removeprefix("sha256:")
        assets.append(ReleaseAsset(name=name, size_bytes=size, sha256=sha256))
    if not set(summary.required_assets).issubset(names):
        raise ValueError("required release assets are missing")
    return (
        ReleaseMetadata(
            version=summary.version,
            tag_name=tag,
            published_at=published_at,
            html_url=html_url,
            changelog=summary.changelog,
            migration_notes=summary.migration_notes,
            capability_matrix=dict(sorted(summary.capability_matrix.items())),
            assets=tuple(sorted(assets, key=lambda item: item.name)),
            unsigned=not summary.signed,
        ),
        draft,
        prerelease,
    )


def _parse_summary(body: str) -> _ReleaseSummary:
    if body.count(_MANIFEST_START) != 1:
        raise ValueError("release update manifest is missing or duplicated")
    start = body.index(_MANIFEST_START) + len(_MANIFEST_START)
    end = body.find(_MANIFEST_END, start)
    if end < 0 or body.find(_MANIFEST_END, end + len(_MANIFEST_END)) >= 0:
        raise ValueError("release update manifest terminator is invalid")
    raw = body[start:end]
    if not raw or len(raw.encode("utf-8")) > 128 * 1024:
        raise ValueError("release update manifest size is invalid")
    return _ReleaseSummary.model_validate(json.loads(raw))


def _required_string(payload: dict[str, Any], key: str, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or _CONTROL.search(value):
        raise ValueError(f"{key} is invalid")
    return value


def _required_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} is invalid")
    return value


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("published date must include a timezone")
    return parsed.astimezone(UTC)


def _validate_release_url(value: str, tag: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != f"/{RELEASE_REPOSITORY}/releases/tag/{quote(tag, safe='')}"
    ):
        raise ValueError("release URL is outside the fixed repository")


def _validate_asset_url(value: str, tag: str, name: str) -> None:
    parsed = urlsplit(value)
    expected = (
        f"/{RELEASE_REPOSITORY}/releases/download/{quote(tag, safe='')}/{quote(name, safe='')}"
    )
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != expected
    ):
        raise ValueError("asset URL is outside the fixed repository")


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
