"""Configure shared JSON logging for APIs and workers.

``configure_logging`` sets the standard library level and registers
``structlog`` processors for level, timestamp, and context. It relates to the
``main.py`` modules and consumers/relays in each service, which call it during
startup. Its purpose is to produce consistent logs in local development,
Docker, and CI without per-process configuration.
"""

import logging

import structlog

from libs.common.log_redaction import redact_log_record


def configure_logging(level: str = "INFO") -> None:
    """Configure one-line JSON logs; ``request_id`` comes from bound contextvars."""

    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_log_record,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
