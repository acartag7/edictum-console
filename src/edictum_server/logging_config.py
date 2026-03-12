"""Structured logging configuration using structlog.

Call ``configure_logging()`` once at startup — before any logger is
instantiated — to set up the structlog processor pipeline and route
stdlib logging (uvicorn, SQLAlchemy, httpx) through the same pipeline.

Dev mode:  colorized, human-readable console output.
Prod mode: JSON lines, machine-parseable, one object per line.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(
    *,
    log_level: str = "INFO",
    json_output: bool = False,
) -> None:
    """Configure structlog + stdlib logging integration.

    Args:
        log_level: Root log level (DEBUG, INFO, WARNING, ERROR).
        json_output: If True, emit JSON lines. If False, emit pretty console output.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Shared processors used by both structlog-native and stdlib loggers.
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # Configure structlog for its own loggers.
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Route ALL stdlib logging through structlog's ProcessorFormatter.
    # This captures uvicorn, SQLAlchemy, httpx, redis, etc.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet down noisy third-party loggers — but keep uvicorn.access
    # for HTTP audit trail (every inbound request logged with path/status).
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
