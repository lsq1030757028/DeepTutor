"""Formatters for DeepTutor's stdlib logging pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any

from .context import LOG_CONTEXT_FIELDS, current_log_context


class ContextFilter(logging.Filter):
    """Attach contextvars and explicit record fields to each LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = current_log_context()
        for key in LOG_CONTEXT_FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                context[key] = value
        # Structured per-call detail (``diag(...)`` and anything else passing
        # ``extra={"log_extra": {...}}``). Merged rather than promoted to top
        # level so the entry shape stays timestamp/level/logger/message/context
        # — and merged *after* the bound identifiers so a stray field cannot
        # overwrite the session or turn a line belongs to.
        extra = getattr(record, "log_extra", None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                if key not in LOG_CONTEXT_FIELDS:
                    context[key] = value
        record.log_context = context
        return True


class JsonlFormatter(logging.Formatter):
    """One structured JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "context": getattr(record, "log_context", {}) or {},
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Small human-readable formatter for local development."""

    def format(self, record: logging.LogRecord) -> str:
        context = getattr(record, "log_context", {}) or {}
        stage = f" @{context['stage']}" if context.get("stage") else ""
        task = f" #{context['task_id']}" if context.get("task_id") else ""
        return f"{record.levelname:<7} {record.name}{stage}{task} - {record.getMessage()}"
