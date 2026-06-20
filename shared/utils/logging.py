"""Structured logging utilities.

Services call:
    from shared.utils.logging import get_logger, setup_logging
    log = get_logger("my-service.module")
    log.info("thing happened", job_id=job_id, symbol=symbol)

Keyword arguments are passed as ``extra`` fields and appear as top-level
JSON keys in the log output (via pythonjsonlogger).
"""
from __future__ import annotations

import logging
import os
import sys

from pythonjsonlogger import jsonlogger


def setup_logging(service_name: str, level: str | None = None) -> None:
    log_level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()

    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


class StructuredLogger:
    """Thin wrapper around stdlib Logger that accepts keyword args as JSON fields.

    Converts ``log.info("msg", job_id=x, symbol=y)`` into a log call with
    ``extra={"job_id": x, "symbol": y}`` so pythonjsonlogger includes them
    as top-level fields in the structured output.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    @property
    def name(self) -> str:
        return self._logger.name

    def _emit(self, level: int, msg: str, *args, **kwargs) -> None:
        exc_info = kwargs.pop("exc_info", None)
        stack_info = kwargs.pop("stack_info", None)
        extra = kwargs or None
        self._logger.log(
            level, msg, *args,
            exc_info=exc_info,
            stack_info=stack_info,
            extra=extra,
        )

    def debug(self, msg: str, *args, **kwargs) -> None:
        self._emit(logging.DEBUG, msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self._emit(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self._emit(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        self._emit(logging.ERROR, msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs) -> None:
        self._emit(logging.CRITICAL, msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs) -> None:
        kwargs.setdefault("exc_info", True)
        self._emit(logging.ERROR, msg, *args, **kwargs)

    def isEnabledFor(self, level: int) -> bool:
        return self._logger.isEnabledFor(level)


def get_logger(name: str) -> StructuredLogger:
    return StructuredLogger(logging.getLogger(name))
