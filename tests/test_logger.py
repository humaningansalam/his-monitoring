"""Logging configuration and idempotency tests."""

from __future__ import annotations

import importlib
import io
import logging
import os
import sys
import threading
from collections.abc import Generator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from unittest.mock import patch

import pytest

import his_mon.logger as logger_module
from his_mon.logger import LogEventCode, setup_logging


@contextmanager
def _isolated_root_logger(
    *preserved_handlers: logging.Handler,
) -> Generator[logging.Logger, None, None]:
    root_logger = logging.getLogger()
    saved_handlers = list(root_logger.handlers)
    saved_level = root_logger.level
    try:
        root_logger.handlers = list(preserved_handlers)
        yield root_logger
    finally:
        root_logger.handlers = saved_handlers
        root_logger.setLevel(saved_level)


def _count_stream_handlers(logger: logging.Logger) -> int:
    return sum(
        1
        for handler in logger.handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
    )


def _count_file_handlers(logger: logging.Logger, path: str) -> int:
    absolute_path = os.path.abspath(path)
    return sum(
        1
        for handler in logger.handlers
        if isinstance(handler, RotatingFileHandler)
        and os.path.abspath(handler.baseFilename) == absolute_path
    )


class TestStreamHandlerIdempotency:
    def test_repeated_calls_do_not_duplicate_stream_handler(self):
        with _isolated_root_logger() as root_logger:
            setup_logging()
            setup_logging()
            setup_logging()
            assert _count_stream_handlers(root_logger) == 1

    def test_repeated_calls_with_different_levels_do_not_duplicate(self):
        with _isolated_root_logger() as root_logger:
            setup_logging(level="DEBUG")
            setup_logging(level="WARNING")
            assert _count_stream_handlers(root_logger) == 1

    @pytest.mark.parametrize("level", [True, object()])
    def test_non_level_types_are_rejected(self, level):
        with _isolated_root_logger(), pytest.raises(TypeError):
            setup_logging(level=level)

    def test_unknown_level_name_is_rejected(self):
        with _isolated_root_logger(), pytest.raises(ValueError):
            setup_logging(level="NOT_A_LEVEL")

    def test_numeric_level_is_applied(self):
        with _isolated_root_logger() as root_logger:
            setup_logging(level=logging.ERROR)
            assert root_logger.level == logging.ERROR

    def test_subclassed_stdout_stream_handlers_are_deduped(self):
        class StdoutStreamHandler(logging.StreamHandler):
            pass

        with _isolated_root_logger() as root_logger:
            root_logger.addHandler(StdoutStreamHandler(sys.stdout))
            setup_logging()
            assert _count_stream_handlers(root_logger) == 1

    def test_owned_handler_tracks_stdout_capture_replacement(self):
        first_stream = io.StringIO()
        second_stream = io.StringIO()
        third_stream = io.StringIO()

        with _isolated_root_logger() as root_logger:
            with patch.object(sys, "stdout", first_stream):
                setup_logging()
                first_handler = root_logger.handlers[0]
                root_logger.warning("first capture")

            first_stream.close()
            with patch.object(sys, "stdout", second_stream):
                setup_logging()
                root_logger.warning("second capture")

            assert root_logger.handlers == [first_handler]
            assert "second capture" in second_stream.getvalue()

            second_stream.close()
            with patch.object(sys, "stdout", third_stream):
                root_logger.warning("after capture replacement")

            assert root_logger.handlers == [first_handler]
            assert "after capture replacement" in third_stream.getvalue()

    def test_owned_handler_yields_to_unrelated_current_stdout_handler(self):
        first_stream = io.StringIO()
        second_stream = io.StringIO()
        unrelated_handler = logging.StreamHandler(first_stream)

        with _isolated_root_logger(unrelated_handler) as root_logger:
            with patch.object(sys, "stdout", first_stream):
                setup_logging()
                assert root_logger.handlers == [unrelated_handler]

            with patch.object(sys, "stdout", second_stream):
                setup_logging()
                assert len(root_logger.handlers) == 2

            with patch.object(sys, "stdout", first_stream):
                setup_logging()

            assert root_logger.handlers == [unrelated_handler]


class TestFileHandlerIdempotency:
    def test_single_file_call_adds_one_file_handler(self, tmp_path):
        log_file = str(tmp_path / "nested" / "app.log")
        with _isolated_root_logger() as root_logger:
            setup_logging(log_file=log_file)
            assert (tmp_path / "nested").is_dir()
            assert _count_file_handlers(root_logger, log_file) == 1

    def test_repeated_calls_same_file_do_not_duplicate(self, tmp_path):
        log_file = str(tmp_path / "app.log")
        with _isolated_root_logger() as root_logger:
            setup_logging(log_file=log_file)
            setup_logging(log_file=log_file)
            setup_logging(log_file=log_file)
            assert _count_file_handlers(root_logger, log_file) == 1

    def test_different_file_paths_each_get_own_handler(self, tmp_path):
        log_file_a = str(tmp_path / "a.log")
        log_file_b = str(tmp_path / "b.log")
        with _isolated_root_logger() as root_logger:
            setup_logging(log_file=log_file_a)
            setup_logging(log_file=log_file_b)
            assert _count_file_handlers(root_logger, log_file_a) == 1
            assert _count_file_handlers(root_logger, log_file_b) == 1
            assert (
                len(
                    [
                        handler
                        for handler in root_logger.handlers
                        if isinstance(handler, RotatingFileHandler)
                    ]
                )
                == 2
            )


class TestInvalidFileHandling:
    def test_invalid_log_dir_logs_warning_not_raises(self, caplog):
        bad_log = "/proc/sys/nonexistent_dir/app.log"
        with _isolated_root_logger(caplog.handler), caplog.at_level(logging.WARNING):
            setup_logging(level="CRITICAL", log_file=bad_log)
        setup_records = [
            record for record in caplog.records if record.name == "his_mon.logger"
        ]
        assert setup_records, "Expected a structured file-handler setup failure event"
        assert (
            setup_records[-1].his_mon_code.value
            == LogEventCode.FILE_HANDLER_SETUP_FAILED.value
        )

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permits deleting the active cwd")
    def test_relative_log_path_with_deleted_cwd_warns_not_raises(
        self,
        tmp_path,
        caplog,
    ):
        original_cwd = os.open(".", os.O_RDONLY)
        stale_cwd = tmp_path / "deleted-cwd"
        try:
            stale_cwd.mkdir()
            os.chdir(stale_cwd)
            os.rmdir(stale_cwd)

            with (
                _isolated_root_logger(caplog.handler),
                caplog.at_level(logging.WARNING),
            ):
                setup_logging(level="CRITICAL", log_file="app.log")
        finally:
            os.fchdir(original_cwd)
            os.close(original_cwd)

        setup_records = [
            record for record in caplog.records if record.name == "his_mon.logger"
        ]
        assert setup_records
        assert setup_records[-1].his_mon_code is LogEventCode.FILE_HANDLER_SETUP_FAILED


class FakeLokiHandler(logging.Handler):
    def __init__(self, _queue, *, url: str, tags: dict, version: str = "1"):
        super().__init__()
        self.url = url
        self.tags = tags
        self.version = version

    def emit(self, _record):  # pragma: no cover
        pass


class TestLokiSetupFailures:
    def test_missing_dependency_logs_structured_warning(self, caplog):
        with (
            _isolated_root_logger(caplog.handler),
            patch.object(logger_module, "LokiQueueHandler", None),
            caplog.at_level(logging.WARNING, logger="his_mon.logger"),
        ):
            setup_logging(level="CRITICAL", loki_url="https://loki.example/push")

        assert any(
            record.his_mon_code is LogEventCode.LOKI_UNAVAILABLE
            for record in caplog.records
            if hasattr(record, "his_mon_code")
        )

    def test_handler_constructor_failure_logs_error_type(self, caplog):
        class FailingLokiHandler(logging.Handler):
            def __init__(self, *_args, **_kwargs):
                raise RuntimeError("constructor failed")

        with (
            _isolated_root_logger(caplog.handler),
            patch.object(logger_module, "LokiQueueHandler", FailingLokiHandler),
            caplog.at_level(logging.WARNING, logger="his_mon.logger"),
        ):
            setup_logging(level="CRITICAL", loki_url="https://loki.example/push")

        failure = next(
            record
            for record in caplog.records
            if getattr(record, "his_mon_code", None)
            is LogEventCode.LOKI_HANDLER_SETUP_FAILED
        )
        assert failure.error_type == "RuntimeError"


class TestLokiHandlerIdempotency:
    def test_repeated_same_url_tags_do_not_duplicate(self):
        loki_url = "http://loki:3100/loki/api/v1/push"
        tags = {"app": "test"}

        with _isolated_root_logger() as root_logger:
            with patch.dict(
                "his_mon.logger.__dict__", {"LokiQueueHandler": FakeLokiHandler}
            ):
                setup_logging(loki_url=loki_url, tags=tags)
                setup_logging(loki_url=loki_url, tags=tags)
                setup_logging(loki_url=loki_url, tags=tags)

            loki_handlers = [
                handler
                for handler in root_logger.handlers
                if isinstance(handler, FakeLokiHandler)
            ]
            assert len(loki_handlers) == 1, (
                f"Expected 1 Loki handler, got {len(loki_handlers)}"
            )

    def test_caller_tag_mutation_does_not_change_handler_configuration(self):
        loki_url = "http://loki:3100/loki/api/v1/push"
        tags = {"app": "test"}

        with _isolated_root_logger() as root_logger:
            with patch.dict(
                "his_mon.logger.__dict__", {"LokiQueueHandler": FakeLokiHandler}
            ):
                setup_logging(loki_url=loki_url, tags=tags)
                tags["app"] = "mutated"
                setup_logging(loki_url=loki_url, tags={"app": "test"})

            loki_handlers = [
                handler
                for handler in root_logger.handlers
                if isinstance(handler, FakeLokiHandler)
            ]
            assert len(loki_handlers) == 1
            assert loki_handlers[0].tags == {"app": "test"}

    def test_different_loki_urls_each_get_handler(self):
        url_a = "http://loki-a:3100/loki/api/v1/push"
        url_b = "http://loki-b:3100/loki/api/v1/push"
        tags = {"app": "test"}

        with _isolated_root_logger() as root_logger:
            with patch.dict(
                "his_mon.logger.__dict__", {"LokiQueueHandler": FakeLokiHandler}
            ):
                setup_logging(loki_url=url_a, tags=tags)
                setup_logging(loki_url=url_b, tags=tags)

            loki_handlers = [
                handler
                for handler in root_logger.handlers
                if isinstance(handler, FakeLokiHandler)
            ]
            assert len(loki_handlers) == 2, (
                f"Expected 2 Loki handlers (one per URL), got {len(loki_handlers)}"
            )

    def test_same_url_different_tags_each_get_handler(self):
        loki_url = "http://loki:3100/loki/api/v1/push"

        with _isolated_root_logger() as root_logger:
            with patch.dict(
                "his_mon.logger.__dict__", {"LokiQueueHandler": FakeLokiHandler}
            ):
                setup_logging(loki_url=loki_url, tags={"env": "dev"})
                setup_logging(loki_url=loki_url, tags={"env": "prod"})

            loki_handlers = [
                handler
                for handler in root_logger.handlers
                if isinstance(handler, FakeLokiHandler)
            ]
            assert len(loki_handlers) == 2, (
                f"Expected 2 Loki handlers (one per tag set), got {len(loki_handlers)}"
            )

    def test_repeated_setup_after_module_reload_does_not_duplicate(self):
        loki_url = "http://loki:3100/loki/api/v1/push"
        tags = {"app": "reload"}

        with _isolated_root_logger() as root_logger:
            with patch.dict(
                logger_module.__dict__,
                {"LokiQueueHandler": FakeLokiHandler},
            ):
                logger_module.setup_logging(loki_url=loki_url, tags=tags)

            reloaded_logger = importlib.reload(logger_module)
            with patch.dict(
                reloaded_logger.__dict__,
                {"LokiQueueHandler": FakeLokiHandler},
            ):
                reloaded_logger.setup_logging(loki_url=loki_url, tags=tags)

            loki_handlers = [
                handler
                for handler in root_logger.handlers
                if isinstance(handler, FakeLokiHandler)
            ]
            assert len(loki_handlers) == 1

    @pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
    def test_child_replaces_parent_owned_loki_handler(self):
        loki_url = "http://loki:3100/loki/api/v1/push"
        result_reader, result_writer = os.pipe()

        with (
            _isolated_root_logger() as root_logger,
            patch.dict(
                logger_module.__dict__,
                {"LokiQueueHandler": FakeLokiHandler},
            ),
        ):
            logger_module.setup_logging(loki_url=loki_url, tags={"app": "fork"})
            parent_handler = next(
                handler
                for handler in root_logger.handlers
                if isinstance(handler, FakeLokiHandler)
            )

            child_pid = os.fork()
            if child_pid == 0:
                os.close(result_reader)
                try:
                    logger_module.setup_logging(
                        loki_url=loki_url,
                        tags={"app": "fork"},
                    )
                    child_handlers = [
                        handler
                        for handler in root_logger.handlers
                        if isinstance(handler, FakeLokiHandler)
                    ]
                    assert len(child_handlers) == 1
                    assert child_handlers[0] is not parent_handler
                except Exception:  # noqa: BLE001 - child failures become exit status.
                    os._exit(1)
                os.write(result_writer, b"rebound")
                os._exit(0)

            os.close(result_writer)
            result_writer = -1
            result = os.read(result_reader, 64)
            _, child_status = os.waitpid(child_pid, 0)

        if result_writer >= 0:
            os.close(result_writer)
        os.close(result_reader)
        assert os.waitstatus_to_exitcode(child_status) == 0
        assert result == b"rebound"


class TestReloadConcurrency:
    def test_file_setup_overlapping_reload_uses_one_process_lock(self, tmp_path):
        log_file = str(tmp_path / "reload.log")
        entered_constructor = threading.Event()
        release_constructor = threading.Event()
        errors = []
        real_handler = logger_module.RotatingFileHandler

        class BlockingFileHandler(real_handler):
            def __init__(self, *args, **kwargs):
                entered_constructor.set()
                assert release_constructor.wait(2.0)
                super().__init__(*args, **kwargs)

        with _isolated_root_logger() as root_logger:
            logger_module.RotatingFileHandler = BlockingFileHandler

            def first_setup():
                try:
                    logger_module.setup_logging(log_file=log_file)
                except Exception as exc:  # noqa: BLE001 - assert every thread failure.
                    errors.append(exc)

            first_thread = threading.Thread(target=first_setup)
            first_thread.start()
            try:
                assert entered_constructor.wait(1.0)
                reloaded_logger = importlib.reload(logger_module)

                second_thread = threading.Thread(
                    target=reloaded_logger.setup_logging,
                    kwargs={"log_file": log_file},
                )
                second_thread.start()
                release_constructor.set()
                first_thread.join(2.0)
                second_thread.join(2.0)

                assert not first_thread.is_alive()
                assert not second_thread.is_alive()
                assert errors == []
                assert _count_file_handlers(root_logger, log_file) == 1
            finally:
                release_constructor.set()
                first_thread.join(2.0)
