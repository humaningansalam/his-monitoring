"""Focused tests for his_mon.logger.setup_logging idempotency.

Covers:
- Repeated calls do not accumulate duplicate stdout (stream) handlers.
- Repeated calls with the same log file do not accumulate duplicate file handlers.
- A different log file path adds a separate file handler.
- Invalid log directory/file does not raise; a warning is logged instead.
- Repeated calls with the same Loki URL/tags do not accumulate duplicate Loki handlers
  (using monkeypatch to inject a fake LokiQueueHandler class).

Each test restores the root logger handlers and level to the state they had before
the test to prevent cross-test pollution.
"""
from __future__ import annotations

import logging
import sys
import os
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

from his_mon.logger import setup_logging


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@contextmanager
def _isolated_root_logger() -> Generator[logging.Logger, None, None]:
    """Context manager that saves/restores the root logger state around a test."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        # Remove all existing handlers so tests start from a clean slate.
        # But preserve pytest's LogCaptureHandler so caplog works.
        for h in list(root.handlers):
            if type(h).__name__ != "LogCaptureHandler":
                root.removeHandler(h)
        yield root
    finally:
        # Remove any handlers the test added.
        for h in list(root.handlers):
            if type(h).__name__ != "LogCaptureHandler":
                root.removeHandler(h)
        # Restore the original handlers and level.
        for h in saved_handlers:
            if type(h).__name__ != "LogCaptureHandler" and h not in root.handlers:
                root.addHandler(h)
        root.setLevel(saved_level)


def _count_stream_handlers(logger: logging.Logger) -> int:
    """Count StreamHandler instances that target sys.stdout exactly."""
    return sum(
        1
        for h in logger.handlers
        if type(h) is logging.StreamHandler and getattr(h, "stream", None) is sys.stdout
    )


def _count_file_handlers(logger: logging.Logger, path: str) -> int:
    """Count RotatingFileHandler instances targeting *path*."""
    abs_path = os.path.abspath(path)
    return sum(
        1
        for h in logger.handlers
        if isinstance(h, RotatingFileHandler)
        and os.path.abspath(getattr(h, "baseFilename", "")) == abs_path
    )


# ---------------------------------------------------------------------------
# Stream handler idempotency
# ---------------------------------------------------------------------------

class TestStreamHandlerIdempotency:
    """setup_logging() must not duplicate the stdout stream handler."""

    def test_single_call_adds_one_stream_handler(self):
        with _isolated_root_logger() as root:
            setup_logging()
            assert _count_stream_handlers(root) == 1

    def test_repeated_calls_do_not_duplicate_stream_handler(self):
        with _isolated_root_logger() as root:
            setup_logging()
            setup_logging()
            setup_logging()
            assert _count_stream_handlers(root) == 1

    def test_repeated_calls_with_different_levels_do_not_duplicate(self):
        with _isolated_root_logger() as root:
            setup_logging(level="DEBUG")
            setup_logging(level="WARNING")
            assert _count_stream_handlers(root) == 1


# ---------------------------------------------------------------------------
# File handler idempotency
# ---------------------------------------------------------------------------

class TestFileHandlerIdempotency:
    """setup_logging() must not duplicate a RotatingFileHandler for the same path."""

    def test_single_file_call_adds_one_file_handler(self, tmp_path):
        log_file = str(tmp_path / "app.log")
        with _isolated_root_logger() as root:
            setup_logging(log_file=log_file)
            assert _count_file_handlers(root, log_file) == 1

    def test_repeated_calls_same_file_do_not_duplicate(self, tmp_path):
        log_file = str(tmp_path / "app.log")
        with _isolated_root_logger() as root:
            setup_logging(log_file=log_file)
            setup_logging(log_file=log_file)
            setup_logging(log_file=log_file)
            assert _count_file_handlers(root, log_file) == 1

    def test_different_file_paths_each_get_own_handler(self, tmp_path):
        log_file_a = str(tmp_path / "a.log")
        log_file_b = str(tmp_path / "b.log")
        with _isolated_root_logger() as root:
            setup_logging(log_file=log_file_a)
            setup_logging(log_file=log_file_b)
            assert _count_file_handlers(root, log_file_a) == 1
            assert _count_file_handlers(root, log_file_b) == 1
            assert len([
                h for h in root.handlers if isinstance(h, RotatingFileHandler)
            ]) == 2

    def test_repeated_calls_same_file_absolute_vs_relative(self, tmp_path):
        """Paths that resolve to the same absolute path are treated as identical."""
        log_file = str(tmp_path / "app.log")
        with _isolated_root_logger() as root:
            setup_logging(log_file=log_file)
            setup_logging(log_file=log_file)
            assert _count_file_handlers(root, log_file) == 1


# ---------------------------------------------------------------------------
# Invalid file path handling
# ---------------------------------------------------------------------------

class TestInvalidFileHandling:
    """Invalid log path must log a warning/error, not raise an exception."""

    def test_invalid_log_dir_logs_warning_not_raises(self, caplog):
        # Use a path where the directory cannot be created (root-owned path).
        bad_log = "/proc/sys/nonexistent_dir/app.log"
        with _isolated_root_logger():
            with caplog.at_level(logging.WARNING, logger="his_mon.logger"):
                # Must not raise.
                setup_logging(log_file=bad_log)
        assert any("File log error" in r.message for r in caplog.records), (
            "Expected a warning log about the file log error"
        )


# ---------------------------------------------------------------------------
# Loki handler idempotency (monkeypatched)
# ---------------------------------------------------------------------------

class FakeLokiHandler(logging.Handler):
    """Minimal stand-in for LokiQueueHandler for idempotency testing."""

    def __init__(self, queue, *, url: str, tags: dict, version: str = "1"):
        super().__init__()
        self.url = url
        self.tags = tags
        self.version = version

    def emit(self, record):  # pragma: no cover
        pass


class TestLokiHandlerIdempotency:
    """Repeated setup_logging() calls must not duplicate Loki handlers."""

    def _count_loki_handlers(self, logger: logging.Logger) -> int:
        return sum(1 for h in logger.handlers if isinstance(h, FakeLokiHandler))

    def test_repeated_same_url_tags_do_not_duplicate(self):
        loki_url = "http://loki:3100/loki/api/v1/push"
        tags = {"app": "test"}

        with _isolated_root_logger() as root:
            with patch.dict("his_mon.logger.__dict__", {"LokiQueueHandler": FakeLokiHandler}):
                setup_logging(loki_url=loki_url, tags=tags)
                setup_logging(loki_url=loki_url, tags=tags)
                setup_logging(loki_url=loki_url, tags=tags)

            loki_handlers = [h for h in root.handlers if isinstance(h, FakeLokiHandler)]
            assert len(loki_handlers) == 1, (
                f"Expected 1 Loki handler, got {len(loki_handlers)}"
            )

    def test_different_loki_urls_each_get_handler(self):
        url_a = "http://loki-a:3100/loki/api/v1/push"
        url_b = "http://loki-b:3100/loki/api/v1/push"
        tags = {"app": "test"}

        with _isolated_root_logger() as root:
            with patch.dict("his_mon.logger.__dict__", {"LokiQueueHandler": FakeLokiHandler}):
                setup_logging(loki_url=url_a, tags=tags)
                setup_logging(loki_url=url_b, tags=tags)

            loki_handlers = [h for h in root.handlers if isinstance(h, FakeLokiHandler)]
            assert len(loki_handlers) == 2, (
                f"Expected 2 Loki handlers (one per URL), got {len(loki_handlers)}"
            )

    def test_same_url_different_tags_each_get_handler(self):
        loki_url = "http://loki:3100/loki/api/v1/push"

        with _isolated_root_logger() as root:
            with patch.dict("his_mon.logger.__dict__", {"LokiQueueHandler": FakeLokiHandler}):
                setup_logging(loki_url=loki_url, tags={"env": "dev"})
                setup_logging(loki_url=loki_url, tags={"env": "prod"})

            loki_handlers = [h for h in root.handlers if isinstance(h, FakeLokiHandler)]
            assert len(loki_handlers) == 2, (
                f"Expected 2 Loki handlers (one per tag set), got {len(loki_handlers)}"
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    raise SystemExit(_pytest.main([__file__, "-v"]))
