import logging
import os
import sys
from enum import Enum
from logging.handlers import RotatingFileHandler
from multiprocessing import Queue
from typing import Dict, NamedTuple, Optional

from . import _runtime

try:
    from logging_loki import LokiQueueHandler
except ImportError:
    LokiQueueHandler = None

_setup_logger = logging.getLogger(__name__)
_setup_logger.setLevel(logging.WARNING)


class LogEventCode(str, Enum):
    FILE_HANDLER_SETUP_FAILED = "file_handler_setup_failed"
    LOKI_UNAVAILABLE = "loki_unavailable"
    LOKI_HANDLER_SETUP_FAILED = "loki_handler_setup_failed"


class _LokiHandlerIdentity(NamedTuple):
    schema: str
    owner_pid: int
    url: str
    tags: tuple[tuple[str, str], ...]

    @classmethod
    def from_values(cls, url: str, tags: Dict[str, str]) -> "_LokiHandlerIdentity":
        return cls(
            schema="his_mon.loki_handler.v2",
            owner_pid=os.getpid(),
            url=url,
            tags=tuple(sorted(tags.items())),
        )


class _CurrentStdout:
    def write(self, message: str) -> int:
        return sys.stdout.write(message)

    def flush(self) -> None:
        sys.stdout.flush()


_CONSOLE_IDENTITY = "his_mon.console_handler.v1"
_CONSOLE_IDENTITY_ATTRIBUTE = "_his_mon_console_identity"
_LOKI_IDENTITY_ATTRIBUTE = "_his_mon_loki_identity"


def _coerce_log_level(level: str | int) -> int:
    if isinstance(level, int) and not isinstance(level, bool):
        return level
    if not isinstance(level, str):
        raise TypeError("level must be a logging level name or integer")

    level_name = level.upper()
    levels = logging.getLevelNamesMapping()
    if level_name not in levels:
        raise ValueError(f"Invalid logging level: {level!r}")
    return levels[level_name]


def _owned_console_handler(logger: logging.Logger) -> logging.StreamHandler | None:
    for h in logger.handlers:
        if (
            isinstance(h, logging.StreamHandler)
            and getattr(h, _CONSOLE_IDENTITY_ATTRIBUTE, None)
            == _CONSOLE_IDENTITY
        ):
            return h
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


def _has_file_handler(logger: logging.Logger, log_file: str) -> bool:
    """Return True if *logger* already has a RotatingFileHandler for *log_file*."""
    abs_path = os.path.abspath(log_file)
    for h in logger.handlers:
        if (
            isinstance(h, RotatingFileHandler)
            and os.path.abspath(h.baseFilename) == abs_path
        ):
            return True
    return False


def _has_loki_handler(logger: logging.Logger, loki_url: str, tags: Dict[str, str]) -> bool:
    """Return True if *logger* already has a LokiQueueHandler for *loki_url* and *tags*."""
    if LokiQueueHandler is None:
        return False
    expected = _LokiHandlerIdentity.from_values(loki_url, tags)
    for h in list(logger.handlers):
        if not isinstance(h, LokiQueueHandler):
            continue
        identity = getattr(h, _LOKI_IDENTITY_ATTRIBUTE, None)
        if identity is None:
            continue
        if getattr(identity, "owner_pid", None) != expected.owner_pid:
            # QueueListener threads do not survive fork; retire only handlers
            # owned by this package and let setup create a child-local listener.
            logger.removeHandler(h)
            h.close()
            continue
        if identity == expected:
            return True
    return False


def setup_logging(
    level: str | int = "INFO",
    loki_url: Optional[str] = None,
    tags: Optional[Dict[str, str]] = None,
    log_file: Optional[str] = None,
    max_bytes: int = 1 * 1024 * 1024,  # 1 MB
    backup_count: int = 1
) -> None:
    """Setup unified logging (Console + File + Loki).

    Safe to call repeatedly: equivalent handlers are not added twice.

    :param level: Logging level (\"DEBUG\", \"INFO\", \"ERROR\")
    :param loki_url: Loki server URL (e.g., \"http://loki:3100/loki/api/v1/push\"). If None, Loki is disabled.
    :param tags: Tags for Loki logs (e.g., {'app': 'myapp'})
    :param log_file: Path to the log file. If None, file logging is disabled.
    """
    log_level = _coerce_log_level(level)

    with _runtime.logging_setup_lock:
        logger = logging.getLogger()
        logger.setLevel(log_level)

        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        # Console Handler
        console_handler = _owned_console_handler(logger)
        if console_handler is not None:
            if _has_current_stdout_handler(logger, exclude=console_handler):
                logger.removeHandler(console_handler)
                console_handler.close()
            else:
                console_handler.setFormatter(formatter)
        elif not _has_current_stdout_handler(logger):
            stream_handler = logging.StreamHandler(_CurrentStdout())
            stream_handler.setFormatter(formatter)
            setattr(
                stream_handler,
                _CONSOLE_IDENTITY_ATTRIBUTE,
                _CONSOLE_IDENTITY,
            )
            logger.addHandler(stream_handler)

        # File Handler
        if log_file:
            try:
                if not _has_file_handler(logger, log_file):
                    log_dir = os.path.dirname(log_file)
                    if log_dir:
                        os.makedirs(log_dir, exist_ok=True)

                    file_handler = RotatingFileHandler(
                        filename=log_file,
                        maxBytes=max_bytes,
                        backupCount=backup_count,
                        encoding="utf-8",
                    )
                    file_handler.setFormatter(formatter)
                    logger.addHandler(file_handler)
            except (OSError, ValueError) as e:
                _setup_logger.warning(
                    "[HisMon] File log error: %s",
                    e,
                    extra={"his_mon_code": LogEventCode.FILE_HANDLER_SETUP_FAILED},
                )

        # Loki Handler
        if loki_url:
            if LokiQueueHandler is None:
                _setup_logger.warning(
                    "[HisMon] Loki handler skipped because the optional dependency is unavailable",
                    extra={"his_mon_code": LogEventCode.LOKI_UNAVAILABLE},
                )
            else:
                # The handler retains this mapping, so snapshot caller-owned
                # dictionaries before they can be mutated after setup.
                resolved_tags = dict(tags) if tags else {}
                if not _has_loki_handler(logger, loki_url, resolved_tags):
                    try:
                        loki_handler = LokiQueueHandler(
                            Queue(-1), url=loki_url, tags=resolved_tags, version="1"
                        )
                        setattr(
                            loki_handler,
                            _LOKI_IDENTITY_ATTRIBUTE,
                            _LokiHandlerIdentity.from_values(
                                loki_url,
                                resolved_tags,
                            ),
                        )
                        logger.addHandler(loki_handler)
                    except Exception as e:
                        _setup_logger.warning(
                            "[HisMon] Loki error: %s",
                            e,
                            extra={"his_mon_code": LogEventCode.LOKI_HANDLER_SETUP_FAILED},
                        )
