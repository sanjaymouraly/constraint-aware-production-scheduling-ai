"""Structured logging + redaction helpers."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

from app.config import settings

_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"(sk-ant-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"(?i)(api[_-]?key\"?\s*[:=]\s*\"?)([^\"\s,}]+)"),
    re.compile(r"(?i)(authorization\"?\s*[:=]\s*\"?bearer\s+)([^\"\s,}]+)"),
    re.compile(r"(?i)(password\"?\s*[:=]\s*\"?)([^\"\s,}]+)"),
]


def redact(value: Any) -> Any:
    """Best-effort removal of credential-shaped substrings from log payloads."""
    if isinstance(value, str):
        out = value
        for pattern in _SECRET_PATTERNS:
            if pattern.groups >= 2:
                out = pattern.sub(lambda m: m.group(1) + "***REDACTED***", out)
            else:
                out = pattern.sub("***REDACTED***", out)
        return out
    if isinstance(value, dict):
        return {k: ("***" if _is_secret_key(k) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(v) for v in value)
    return value


def _is_secret_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(token in lowered for token in ("password", "secret", "api_key", "apikey", "token"))


def _processor(
    _: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any] | str | bytes | bytearray | tuple[Any, ...]:
    return redact(dict(event_dict))


def configure_logging() -> None:
    level = getattr(logging, settings.log_level, logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    logging.getLogger("uvicorn.access").setLevel(level)

    renderer: Any
    if settings.log_json:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger("scheduling")
