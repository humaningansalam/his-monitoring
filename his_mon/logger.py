from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Mapping
from enum import Enum
from logging.handlers import RotatingFileHandler
from multiprocessing import Queue
from typing import NamedTuple, Protocol, cast

from . import _runtime

LokiQueueHandler: type[logging.Handler] | None = None
try:
    from logging_loki import (  # type: ignore[import-untyped]
        LokiQueueHandler as _ImportedLokiQueueHandler,
    )
except ImportError:
    pass
else:
    LokiQueueHandler = _ImportedLokiQueueHandler

_setup_logger = logging.getLogger(__name__)
_setup_logger.setLevel(logging.WARNING)

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_CONSOLE_IDENTITY = "his_mon.console_handler.v1"
_CONSOLE_IDENTITY_ATTRIBUTE = "_his_mon_console_identity"
_LOKI_IDENTITY_ATTRIBUTE = "_his_mon_loki_identity"


class LogEventCode(str, Enum):  # noqa: UP042 - preserve existing string formatting.
    FILE_HANDLER_SETUP_FAILED = "file_handler_setup_failed"
    LOKI_UNAVAILABLE = "loki_unavailable"
    LOKI_HANDLER_SETUP_FAILED = "loki_handler_setup_failed"


class _LokiHandlerIdentity(NamedTuple):
    schema: str
    owner_pid: int
    url: str
    tags: tuple[tuple[str, str], ...]

    @classmethod
    def from_values(
        cls,
        url: str,
        tags: Mapping[str, str],
    ) -> _LokiHandlerIdentity:
        return cls(
            schema="his_mon.loki_handler.v2",
            owner_pid=os.getpid(),
            url=url,
            tags=tuple(sorted(tags.items())),
        )


class _ClosableQueue(Protocol):
    def close(self) -> None: ...


class _DynamicStdout:
    """Resolve stdout at write time so capture and reload tools stay compatible."""

    def write(self, message: str) -> int:
        return sys.stdout.write(message)

    def flush(self) -> None:
        sys.stdout.flush()


def _coerce_log_level(level: str | int) -> int:
    if isinstance(level, int) and not isinstance(level, bool):
        return level
    if not isinstance(level, str):
        raise TypeError("level must be a logging level name or integer")

    normalized_level = level.upper()
    known_levels = logging.getLevelNamesMapping()
    if normalized_level not in known_levels:
        raise ValueError(f"Invalid logging level: {level!r}")
    return known_levels[normalized_level]


def _owned_console_handler(logger: logging.Logger) -> logging.Handler | None:
    for handler in logger.handlers:
        if (
            isinstance(handler, logging.StreamHandler)
            and getattr(handler, _CONSOLE_IDENTITY_ATTRIBUTE, None) == _CONSOLE_IDENTITY
        ):
            return handler
    return None


def _has_current_stdout_handler(
    logger: logging.Logger,
    *,
    exclude: logging.Handler | None = None,
) -> bool:
    return any(
        handler is not exclude
        and isinstance(handler, logging.StreamHandler)
        and handler.stream is sys.stdout
        for handler in logger.handlers
    )


def _find_file_handler(
    logger: logging.Logger,
    log_file: str | os.PathLike[str],
) -> RotatingFileHandler | None:
    absolute_path = os.path.abspath(os.fspath(log_file))
    for handler in logger.handlers:
        if (
            isinstance(handler, RotatingFileHandler)
            and os.path.abspath(handler.baseFilename) == absolute_path
        ):
            return handler
    return None


def _find_loki_handler(
    logger: logging.Logger,
    loki_url: str,
    tags: Mapping[str, str],
) -> logging.Handler | None:
    if LokiQueueHandler is None:
        return None

    expected_identity = _LokiHandlerIdentity.from_values(loki_url, tags)
    for handler in list(logger.handlers):
        if not isinstance(handler, LokiQueueHandler):
            continue
        identity = getattr(handler, _LOKI_IDENTITY_ATTRIBUTE, None)
        if identity is None:
            continue
        if getattr(identity, "owner_pid", None) != expected_identity.owner_pid:
            logger.removeHandler(handler)
            handler.close()
            continue
        if identity == expected_identity:
            return handler
    return None


def _configure_console_handler(
    logger: logging.Logger,
    formatter: logging.Formatter,
) -> None:
    owned_handler = _owned_console_handler(logger)
    if owned_handler is not None:
        if _has_current_stdout_handler(logger, exclude=owned_handler):
            logger.removeHandler(owned_handler)
            owned_handler.close()
        else:
            owned_handler.setFormatter(formatter)
        return

    if _has_current_stdout_handler(logger):
        return

    handler = logging.StreamHandler(_DynamicStdout())
    handler.setFormatter(formatter)
    setattr(handler, _CONSOLE_IDENTITY_ATTRIBUTE, _CONSOLE_IDENTITY)
    logger.addHandler(handler)


def _configure_file_handler(
    logger: logging.Logger,
    formatter: logging.Formatter,
    log_file: str | os.PathLike[str],
    max_bytes: int,
    backup_count: int,
) -> None:
    try:
        if _find_file_handler(logger, log_file) is not None:
            return

        file_path = os.fspath(log_file)
        parent_directory = os.path.dirname(file_path)
        if parent_directory:
            os.makedirs(parent_directory, exist_ok=True)

        handler = RotatingFileHandler(
            filename=file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except (OSError, TypeError, ValueError) as error:
        _setup_logger.warning(
            "Could not configure file logging: %s",
            error,
            extra={"his_mon_code": LogEventCode.FILE_HANDLER_SETUP_FAILED},
        )


def _configure_loki_handler(
    logger: logging.Logger,
    loki_url: str,
    tags: Mapping[str, str] | None,
) -> None:
    if LokiQueueHandler is None:
        _setup_logger.warning(
            "Loki logging requires the optional 'loki' dependency",
            extra={"his_mon_code": LogEventCode.LOKI_UNAVAILABLE},
        )
        return

    resolved_tags = dict(tags or {})
    if _find_loki_handler(logger, loki_url, resolved_tags) is not None:
        return

    handler_queue = cast(_ClosableQueue, Queue(-1))
    try:
        handler_factory = cast(Callable[..., logging.Handler], LokiQueueHandler)
        handler = handler_factory(
            handler_queue,
            url=loki_url,
            tags=resolved_tags,
            version="1",
        )
        setattr(
            handler,
            _LOKI_IDENTITY_ATTRIBUTE,
            _LokiHandlerIdentity.from_values(loki_url, resolved_tags),
        )
        logger.addHandler(handler)
    except Exception as error:  # noqa: BLE001 - optional handler implementations vary.
        handler_queue.close()
        _setup_logger.warning(
            "Could not configure Loki logging: %s",
            type(error).__name__,
            extra={
                "his_mon_code": LogEventCode.LOKI_HANDLER_SETUP_FAILED,
                "error_type": type(error).__name__,
            },
        )


def setup_logging(
    level: str | int = "INFO",
    loki_url: str | None = None,
    tags: Mapping[str, str] | None = None,
    log_file: str | os.PathLike[str] | None = None,
    max_bytes: int = 1024 * 1024,
    backup_count: int = 1,
) -> None:
    """Configure root logging without duplicating equivalent handlers."""
    log_level = _coerce_log_level(level)

    with _runtime.logging_setup_lock:
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        formatter = logging.Formatter(_LOG_FORMAT)

        _configure_console_handler(root_logger, formatter)
        if log_file:
            _configure_file_handler(
                root_logger,
                formatter,
                log_file,
                max_bytes,
                backup_count,
            )
        if loki_url:
            _configure_loki_handler(root_logger, loki_url, tags)
