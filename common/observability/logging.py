"""Structured logging: JSON in production, readable console output in development.

Context such as ``request_id``, ``organisation_id`` and ``task_id`` is bound with
``structlog.contextvars`` and appears on every log line, including lines from
standard-library loggers (Django, Celery).
"""

from typing import Any

import structlog

from .redaction import redact_sensitive


def _shared_processors() -> list[Any]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive,
    ]


def json_tracebacks() -> structlog.processors.ExceptionRenderer:
    """Structured tracebacks WITHOUT local variables.

    structlog captures each frame's locals by default; those can hold passwords,
    tokens or personal data, so they must never reach production logs.
    """
    return structlog.processors.ExceptionRenderer(
        structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
    )


def build_logging_config(*, json_logs: bool, level: str) -> dict[str, Any]:
    """Configure structlog and return a ``LOGGING`` dict for Django."""
    shared = _shared_processors()
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    final: list[Any] = [structlog.stdlib.ProcessorFormatter.remove_processors_meta]
    if json_logs:
        final += [json_tracebacks(), structlog.processors.JSONRenderer()]
    else:
        final += [structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback)]

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structlog": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": final,
                "foreign_pre_chain": shared,
            }
        },
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "structlog"}},
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            # Our request middleware logs every request; avoid duplicate 4xx lines.
            "django.request": {"level": "ERROR"},
            "django.server": {"level": "WARNING"},
            # Internet scanners send bogus Host headers constantly. The 400 is still
            # visible in request logs; the traceback would only flood error monitoring.
            "django.security.DisallowedHost": {"level": "CRITICAL"},
        },
    }
