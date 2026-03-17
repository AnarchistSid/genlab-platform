"""Structured logging configuration for Gen Lab.

Configures structlog as a drop-in wrapper around stdlib ``logging``.
Existing ``logging.getLogger(__name__)`` calls continue to work and their
output is routed through structlog's processor chain — so all log lines
become structured JSON (or pretty console output in dev mode).

Usage::

    from genlab_core.observability.logging import configure_logging

    # At application startup (once):
    configure_logging(json_output=True)   # production
    configure_logging(json_output=False)  # development (coloured console)

New code should prefer ``structlog.get_logger()`` over
``logging.getLogger()`` to attach key-value context natively.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(
    *,
    json_output: bool = True,
    level: int = logging.INFO,
) -> None:
    """Configure structlog to wrap stdlib logging.

    Parameters
    ----------
    json_output:
        When *True* (default / production), all log lines are rendered as
        single-line JSON objects.  When *False*, a coloured, human-readable
        console renderer is used instead.
    level:
        Minimum log level.  Defaults to ``logging.INFO``.
    """

    # Shared processors applied to every log event.
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # --- Configure stdlib logging to go through structlog ---
    # ProcessorFormatter turns stdlib LogRecords into structlog events.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # --- Configure structlog itself ---
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
