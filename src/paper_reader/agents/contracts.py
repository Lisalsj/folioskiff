"""Small, capability-limited contracts shared by Agent backends."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Protocol

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_SAFE_ATTACHMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_MAX_PROMPT_CHARACTERS = 2_000_000
_MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024
_MAX_TOTAL_ATTACHMENT_BYTES = 200 * 1024 * 1024
_MAX_VISUAL_ATTACHMENT_BYTES = 3 * 1024 * 1024
_MAX_TOTAL_VISUAL_BYTES = 12 * 1024 * 1024
_MAX_VISUAL_ATTACHMENTS = 6
_VISUAL_MEDIA_SUFFIXES = {
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "image/png": frozenset({".png"}),
    "image/webp": frozenset({".webp"}),
}


class AgentTaskKind(StrEnum):
    KEYWORDS = "keywords"
    REPORT = "report"
    CHAT = "chat"
    COMPARISON = "comparison"
    ASSISTANT_ASK = "assistant_ask"
    ASSISTANT_EXPLAIN = "assistant_explain"
    ASSISTANT_TRANSLATE = "assistant_translate"
    RELATED_INTENT = "related_intent"
    RELATED_EXTERNAL = "related_external"


class ReasoningEffort(StrEnum):
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class ServiceTier(StrEnum):
    FLEX = "flex"
    FAST = "fast"


class AgentLocation(StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"


class AgentProbeCode(StrEnum):
    READY = "ready"
    NOT_INSTALLED = "not_installed"
    NOT_AUTHENTICATED = "not_authenticated"
    CONFIGURATION_INVALID = "configuration_invalid"
    VERSION_UNSUPPORTED = "version_unsupported"
    SERVICE_NOT_RUNNING = "service_not_running"
    PROBE_TIMEOUT = "probe_timeout"
    UNAVAILABLE = "unavailable"


class AgentErrorCode(StrEnum):
    UNAVAILABLE = "unavailable"
    AUTHENTICATION = "authentication"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    OUTPUT_LIMIT = "output_limit"
    INVALID_OUTPUT = "invalid_output"
    PROTOCOL = "protocol"
    PROCESS = "process"
    TRANSPORT = "transport"


class AgentBackendError(RuntimeError):
    """A redaction-safe error category suitable for durable Job diagnostics."""

    def __init__(
        self,
        code: AgentErrorCode,
        message: str,
        *,
        retryable: bool = False,
        diagnostic: str | None = None,
        usage: AgentUsage | None = None,
    ) -> None:
        super().__init__(message)
        self.code = AgentErrorCode(code)
        self.retryable = retryable
        self.diagnostic = diagnostic
        self.usage = usage


@dataclass(frozen=True, slots=True)
class AgentCapability:
    backend_id: str
    display_name: str
    location: AgentLocation
    probe_code: AgentProbeCode
    installed: bool
    authenticated: bool
    available: bool
    supports_schema: bool
    supports_streaming: bool
    supports_vision: bool
    supports_external_research: bool
    degradation_reasons: tuple[str, ...] = ()
    desktop_app_running: bool | None = None

    def __post_init__(self) -> None:
        _validate_identifier("backend_id", self.backend_id)
        if not self.display_name.strip():
            raise ValueError("display_name cannot be blank")
        if self.available != (self.probe_code is AgentProbeCode.READY):
            raise ValueError("available Agent capability must use the ready probe code")
        if self.probe_code is AgentProbeCode.NOT_AUTHENTICATED and self.authenticated:
            raise ValueError("not_authenticated capability cannot be authenticated")


@dataclass(frozen=True, slots=True)
class AgentModel:
    model_id: str
    display_name: str
    supports_vision: bool | None = None
    context_window: int | None = None

    def __post_init__(self) -> None:
        _validate_model_id(self.model_id)
        if not self.display_name.strip():
            raise ValueError("display_name cannot be blank")
        if self.context_window is not None and self.context_window < 1:
            raise ValueError("context_window must be positive")


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    models: tuple[AgentModel, ...] = ()
    supports_manual_model_id: bool = True
    supports_default_model: bool = False
    degradation_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.models, key=lambda item: item.model_id.casefold()))
        identifiers = [item.model_id for item in ordered]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Duplicate model ID in Agent model catalog")
        object.__setattr__(self, "models", ordered)


@dataclass(frozen=True, slots=True)
class EvidenceAttachment:
    """In-memory evidence only; a backend never receives an arbitrary local path."""

    name: str
    media_type: str
    content: bytes = field(repr=False)

    def __post_init__(self) -> None:
        path = PurePosixPath(self.name)
        if (
            not _SAFE_ATTACHMENT.fullmatch(self.name)
            or path.is_absolute()
            or len(path.parts) != 1
            or "\\" in self.name
        ):
            raise ValueError("Attachment name must be a safe relative file name")
        if not self.media_type.strip() or _CONTROL_CHARACTER.search(self.media_type):
            raise ValueError("Attachment media type cannot be blank or contain controls")
        if len(self.content) > _MAX_ATTACHMENT_BYTES:
            raise ValueError("Attachment exceeds the maximum evidence size")
        normalized_media_type = self.media_type.casefold()
        if normalized_media_type.startswith("image/"):
            suffixes = _VISUAL_MEDIA_SUFFIXES.get(normalized_media_type)
            if suffixes is None:
                raise ValueError("Attachment visual media type is not supported")
            if path.suffix.casefold() not in suffixes:
                raise ValueError("Visual attachment extension does not match its media type")
            if not _has_visual_signature(normalized_media_type, self.content):
                raise ValueError("Visual attachment content signature is invalid")
            if len(self.content) > _MAX_VISUAL_ATTACHMENT_BYTES:
                raise ValueError("Visual attachment exceeds the maximum page size")


@dataclass(frozen=True, slots=True)
class AgentRequest:
    task_id: str
    task_kind: AgentTaskKind
    prompt: str
    output_schema: Mapping[str, Any]
    reasoning_effort: ReasoningEffort
    service_tier: ServiceTier
    model_id: str | None = None
    external_research: bool = False
    attachments: tuple[EvidenceAttachment, ...] = ()
    timeout_seconds: float = 900
    context_window: int | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        _validate_identifier("task_id", self.task_id)
        if not self.prompt.strip():
            raise ValueError("prompt cannot be blank")
        if len(self.prompt) > _MAX_PROMPT_CHARACTERS:
            raise ValueError("prompt exceeds the maximum size")
        if self.model_id is not None:
            _validate_model_id(self.model_id)
        if self.timeout_seconds < 30 or self.timeout_seconds > 7200:
            raise ValueError("timeout_seconds must be between 30 and 7200")
        if self.context_window is not None and not 1024 <= self.context_window <= 1_000_000:
            raise ValueError("context_window must be between 1024 and 1000000")
        if self.max_output_tokens is not None and not 1 <= self.max_output_tokens <= 100_000:
            raise ValueError("max_output_tokens must be between 1 and 100000")
        names = [attachment.name for attachment in self.attachments]
        if len(names) != len(set(names)):
            raise ValueError("Attachment names must be unique")
        if sum(len(attachment.content) for attachment in self.attachments) > (
            _MAX_TOTAL_ATTACHMENT_BYTES
        ):
            raise ValueError("Combined Agent evidence exceeds the maximum size")
        visuals = tuple(
            attachment for attachment in self.attachments if is_visual_attachment(attachment)
        )
        if len(visuals) > _MAX_VISUAL_ATTACHMENTS:
            raise ValueError("Agent requests may contain at most six visual attachments")
        if sum(len(attachment.content) for attachment in visuals) > _MAX_TOTAL_VISUAL_BYTES:
            raise ValueError("Combined visual evidence exceeds the maximum size")


@dataclass(frozen=True, slots=True)
class AgentFragment:
    index: int
    kind: str
    text: str

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("Fragment index cannot be negative")
        if self.kind not in {"text", "reasoning", "diagnostic"}:
            raise ValueError("Unknown Agent fragment kind")


@dataclass(frozen=True, slots=True)
class AgentUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        for value in (self.input_tokens, self.output_tokens):
            if value is not None and value < 0:
                raise ValueError("Token usage cannot be negative")


@dataclass(frozen=True, slots=True)
class AgentResult:
    raw_output: str
    structured_output: Mapping[str, Any]
    resolved_model_id: str | None
    usage: AgentUsage = AgentUsage()


FragmentCallback = Callable[[AgentFragment], Awaitable[None]]
CancellationCheck = Callable[[], bool]


class AgentBackend(Protocol):
    @property
    def capability(self) -> AgentCapability: ...

    async def discover_models(self) -> ModelCatalog: ...

    async def generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: FragmentCallback,
        cancelled: CancellationCheck,
    ) -> AgentResult: ...


def is_visual_attachment(attachment: EvidenceAttachment) -> bool:
    return attachment.media_type.casefold() in _VISUAL_MEDIA_SUFFIXES


def _validate_identifier(name: str, value: str) -> None:
    if not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} must be a safe identifier")


def _validate_model_id(value: str) -> None:
    if not value.strip() or len(value) > 200 or _CONTROL_CHARACTER.search(value):
        raise ValueError("model_id must be non-blank, bounded, and contain no controls")


def _has_visual_signature(media_type: str, content: bytes) -> bool:
    if media_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if media_type == "image/webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False
