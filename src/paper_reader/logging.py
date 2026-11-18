"""Logging helpers that remove common credential forms."""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import Any

from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter, DefaultFormatter

REDACTED = "[REDACTED]"

_AUTHORIZATION = re.compile(
    r"(?i)((?:proxy-)?authorization\s*:\s*(?:bearer|basic|digest)\s+)[^\s,;]+"
)
_COOKIE_HEADER = re.compile(r"(?i)((?:set-)?cookie\s*:\s*)[^\r\n]+")
_NAMED_SECRET = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:[A-Za-z0-9]+[_-])*(?:"
    r"secret[_-]?access[_-]?key|private[_-]?key|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|session[_-]?token|csrf[_-]?token|client[_-]?secret|"
    r"token|secret|password|credential))\b"
    r"(\"?)(\s*[:=]\s*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&]+)"
)
_BOOTSTRAP_FRAGMENT = re.compile(r"(?i)([#?&]bootstrap=)[^&#\s]+")
_OPENAI_STYLE_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")


def redact_secrets(value: str) -> str:
    redacted = _AUTHORIZATION.sub(lambda match: f"{match.group(1)}{REDACTED}", value)
    redacted = _COOKIE_HEADER.sub(
        lambda match: f"{match.group(1)}{REDACTED}",
        redacted,
    )
    redacted = _NAMED_SECRET.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}{REDACTED}",
        redacted,
    )
    redacted = _BOOTSTRAP_FRAGMENT.sub(
        lambda match: f"{match.group(1)}{REDACTED}",
        redacted,
    )
    redacted = _OPENAI_STYLE_KEY.sub(REDACTED, redacted)
    return _CONTROL_CHARACTER.sub(" ", redacted)


class RedactingFilter(logging.Filter):
    """Render a record once and replace common secrets before handlers emit it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple):
            record.msg = redact_secrets(str(record.msg))
            record.args = tuple(
                redact_secrets(value) if isinstance(value, str) else value for value in record.args
            )
            return True

        record.msg = redact_secrets(record.getMessage())
        record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    """Provide a final redaction pass, including formatted exception text."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


class RedactingDefaultFormatter(DefaultFormatter):
    """Apply the final redaction pass to Uvicorn's default records."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


class RedactingAccessFormatter(AccessFormatter):
    """Apply the final redaction pass to Uvicorn access records."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


def build_uvicorn_log_config() -> dict[str, Any]:
    """Return Uvicorn's standard logging setup with redaction on every handler."""
    configuration: dict[str, Any] = deepcopy(LOGGING_CONFIG)
    filters: dict[str, Any] = configuration.setdefault("filters", {})
    filters["redact"] = {"()": "paper_reader.logging.RedactingFilter"}
    configuration["formatters"]["default"]["()"] = "paper_reader.logging.RedactingDefaultFormatter"
    configuration["formatters"]["access"]["()"] = "paper_reader.logging.RedactingAccessFormatter"
    configuration["loggers"]["paper_reader"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }

    handlers: dict[str, dict[str, Any]] = configuration["handlers"]
    for handler in handlers.values():
        configured_filters = list(handler.get("filters", []))
        if "redact" not in configured_filters:
            configured_filters.append("redact")
        handler["filters"] = configured_filters
    return configuration
