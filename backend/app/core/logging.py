"""Structured JSON logging with automatic secret redaction."""

import json
import logging
import re
import sys
from datetime import datetime, timezone
from threading import Lock
from typing import Any

REDACTED = "***REDACTED***"

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Google API Keys (AIza...)
    (re.compile(r"AIza[0-9A-Za-z\-_]{16,}"), REDACTED),
    # Bearer tokens
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9\-._~+/]+=*"), rf"\1{REDACTED}"),
    # Credentials inside URLs (e.g. redis://:password@host, postgresql://user:pass@host)
    (re.compile(r"(://[^:/@\s]*:)[^@\s]+(@)"), rf"\1{REDACTED}\2"),
    # Generic key=value / "key": "value" secrets
    (
        re.compile(
            r'(?i)(["\']?(?:api_?key|secret|token|password|passwd|authorization|hmac_secret|access_token)["\']?\s*[:=]\s*["\']?)([^\s"\',}]+)(["\']?)'
        ),
        rf"\1{REDACTED}\3",
    ),
)

_registered_secrets_lock = Lock()
_registered_secrets: set[str] = set()


def register_secret(secret_value: str | None) -> None:
    """Register an exact secret string to always redact from logs."""
    if not secret_value or len(secret_value.strip()) < 6:
        return
    with _registered_secrets_lock:
        _registered_secrets.add(secret_value)


def clear_registered_secrets() -> None:
    """Clear dynamically registered secrets (primarily for test isolation)."""
    with _registered_secrets_lock:
        _registered_secrets.clear()


def mask_secrets(value: Any) -> Any:
    """Recursively mask sensitive strings inside logs and structured payloads."""
    if isinstance(value, str):
        masked = value
        with _registered_secrets_lock:
            secrets_snapshot = sorted(_registered_secrets, key=len, reverse=True)
        for secret in secrets_snapshot:
            if secret in masked:
                masked = masked.replace(secret, REDACTED)
        for pattern, replacement in _SECRET_PATTERNS:
            masked = pattern.sub(replacement, masked)
        return masked
    if isinstance(value, dict):
        return {str(k): mask_secrets(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        masked_items = [mask_secrets(item) for item in value]
        return tuple(masked_items) if isinstance(value, tuple) else masked_items
    return value


class SecretMaskingFilter(logging.Filter):
    """Logging filter that scrubs secrets from log messages, args, and extras."""

    _STANDARD_RECORD_KEYS = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "taskName",
        }
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_secrets(record.msg)
        else:
            record.msg = mask_secrets(str(record.msg))

        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: mask_secrets(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(mask_secrets(a) for a in record.args)
            else:
                record.args = mask_secrets(record.args)

        for key, val in list(record.__dict__.items()):
            if key not in self._STANDARD_RECORD_KEYS:
                setattr(record, key, mask_secrets(val))

        return True


class JsonFormatter(logging.Formatter):
    """Format log records as single-line structured JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": mask_secrets(record.getMessage()),
        }

        extra_fields = {
            k: mask_secrets(v)
            for k, v in record.__dict__.items()
            if k not in SecretMaskingFilter._STANDARD_RECORD_KEYS and not k.startswith("_")
        }
        if extra_fields:
            payload["context"] = extra_fields

        if record.exc_info:
            payload["exception"] = mask_secrets(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Configure root logger with JSON formatter and secret masking."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SecretMaskingFilter())

    root.handlers.clear()
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger configured with secret masking."""
    logger = logging.getLogger(name)
    has_mask = any(isinstance(f, SecretMaskingFilter) for f in logger.filters)
    if not has_mask:
        logger.addFilter(SecretMaskingFilter())
    return logger

