import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from multiprocessing import Queue
from typing import Optional, Dict

try:
    from logging_loki import LokiQueueHandler
except ImportError:
    LokiQueueHandler = None

_setup_logger = logging.getLogger(__name__)


def _emit_warning_fallback(message: str) -> None:
    """Emit a warning message even when root logger filtering would otherwise drop it."""
    _setup_logger.warning(message)
    root_logger = logging.getLogger()
    if root_logger.isEnabledFor(logging.WARNING):
        return

    record = root_logger.makeRecord(
        name=_setup_logger.name,
        level=logging.WARNING,
        fn=__file__,
        lno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    for handler in list(root_logger.handlers):
        if record.levelno >= handler.level and handler.filter(record):
            handler.handle(record)


def _has_stream_handler(logger: logging.Logger) -> bool:
    """Return True if *logger* already has a StreamHandler targeting stdout.

    RotatingFileHandler is a subclass of StreamHandler, so we must check the
    stream attribute explicitly to avoid false positives.
    """
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout:
            return True
    return False


def _has_file_handler(logger: logging.Logger, log_file: str) -> bool:
    """Return True if *logger* already has a RotatingFileHandler for *log_file*."""
    abs_path = os.path.abspath(log_file)
    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler):
            try:
                if os.path.abspath(h.baseFilename) == abs_path:
                    return True
            except AttributeError:
                pass
    return False


def _has_loki_handler(logger: logging.Logger, loki_url: str, tags: Dict[str, str]) -> bool:
    """Return True if *logger* already has a LokiQueueHandler for *loki_url* and *tags*."""
    if LokiQueueHandler is None:
        return False
    for h in logger.handlers:
        if isinstance(h, LokiQueueHandler):
            if getattr(h, "url", None) == loki_url and getattr(h, "tags", None) == tags:
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
    logger = logging.getLogger()
    if isinstance(level, int):
        log_level = level
    else:
        log_level = getattr(logging, str(level).upper(), logging.INFO)
    logger.setLevel(log_level)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Console Handler
    if not _has_stream_handler(logger):
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    # File Handler
    if log_file:
        if _has_file_handler(logger, log_file):
            _setup_logger.debug("[HisMon] File log already configured: %s", log_file)
        else:
            try:
                log_dir = os.path.dirname(log_file)
                if log_dir:
                    os.makedirs(log_dir, exist_ok=True)

                file_handler = RotatingFileHandler(
                    filename=log_file, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8'
                )
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
                _setup_logger.info("[HisMon] File log: %s", log_file)
            except Exception as e:
                _emit_warning_fallback(f"[HisMon] File log error: {e}")

    # Loki Handler
    if loki_url and LokiQueueHandler:
        resolved_tags = tags or {}
        if _has_loki_handler(logger, loki_url, resolved_tags):
            _setup_logger.debug("[HisMon] Loki handler already configured: %s", loki_url)
        else:
            try:
                loki_handler = LokiQueueHandler(
                    Queue(-1), url=loki_url, tags=resolved_tags, version="1"
                )
                logger.addHandler(loki_handler)
                _setup_logger.info("[HisMon] Loki attached: %s", loki_url)
            except Exception as e:
                _setup_logger.warning("[HisMon] Loki error: %s", e)
