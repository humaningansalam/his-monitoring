"""ResourceMonitor lifecycle and sampling tests."""

from __future__ import annotations

import logging
import math
import os
import select
import signal
import threading
import time
import types
from unittest.mock import MagicMock

import psutil
import pytest

from his_mon import monitor as monitor_module
from his_mon.monitor import (
    InvalidMonitorIntervalError,
    MonitorErrorCode,
    ResourceMonitor,
)


def _make_metrics_stub():
    metrics_stub = MagicMock()
    metrics_stub.cpu_usage = MagicMock()
    metrics_stub.ram_usage = MagicMock()
    return metrics_stub


def _wait_for(predicate, timeout=1.2, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_start_stop_lifecycle():
    metrics = _make_metrics_stub()
    resource_monitor = ResourceMonitor(metrics, interval=1)
    resource_monitor.start()
    assert resource_monitor.is_running
    worker_thread = resource_monitor._thread
    assert worker_thread is not None

    started_at = time.monotonic()
    resource_monitor.stop()
    elapsed = time.monotonic() - started_at
    assert not worker_thread.is_alive(), "thread should have exited"
    assert not resource_monitor.is_running
    assert elapsed < 2.0, f"stop() took too long: {elapsed:.2f}s"


def test_stop_before_start_is_a_noop():
    resource_monitor = ResourceMonitor(_make_metrics_stub())
    resource_monitor.stop()
    assert not resource_monitor.is_running


def test_metrics_updated_after_one_interval():
    metrics = _make_metrics_stub()
    resource_monitor = ResourceMonitor(metrics, interval=1)
    resource_monitor.start()
    assert _wait_for(
        lambda: metrics.cpu_usage.set.called and metrics.ram_usage.set.called
    )
    resource_monitor.stop()

    assert metrics.cpu_usage.set.called, "cpu_usage.set() was never called"
    assert metrics.ram_usage.set.called, "ram_usage.set() was never called"
    sampled_cpu_percent = metrics.cpu_usage.set.call_args[0][0]
    assert isinstance(sampled_cpu_percent, float) and sampled_cpu_percent >= 0.0, (
        f"unexpected cpu value: {sampled_cpu_percent}"
    )


def test_transient_sampling_error_does_not_stop_monitor(caplog):
    metrics = _make_metrics_stub()
    resource_monitor = ResourceMonitor(metrics, interval=0.01)
    resource_monitor.process = MagicMock(pid=os.getpid())
    resource_monitor.process.cpu_percent.side_effect = [
        0.0,
        psutil.AccessDenied(pid=123),
        12.5,
    ]
    resource_monitor.process.memory_info.return_value = types.SimpleNamespace(
        rss=1024 * 1024
    )

    with caplog.at_level("ERROR", logger="ResourceMonitor"):
        resource_monitor.start()
        assert _wait_for(lambda: metrics.cpu_usage.set.called, timeout=0.5)
        assert (
            resource_monitor._thread is not None and resource_monitor._thread.is_alive()
        )
        resource_monitor.stop()

    metrics.cpu_usage.set.assert_called_with(12.5)
    metrics.ram_usage.set.assert_called_with(1.0)
    assert any(
        record.levelno == logging.ERROR and record.exc_info is not None
        for record in caplog.records
    )


def test_transient_initial_cpu_error_does_not_stop_monitor(caplog):
    metrics = _make_metrics_stub()
    resource_monitor = ResourceMonitor(metrics, interval=0.01)
    resource_monitor.process = MagicMock(pid=os.getpid())
    resource_monitor.process.cpu_percent.side_effect = [
        psutil.AccessDenied(pid=123),
        12.5,
    ]
    resource_monitor.process.memory_info.return_value = types.SimpleNamespace(
        rss=1024 * 1024
    )

    with caplog.at_level(logging.ERROR, logger="ResourceMonitor"):
        resource_monitor.start()
        assert _wait_for(lambda: metrics.cpu_usage.set.called, timeout=0.5)
        assert (
            resource_monitor._thread is not None and resource_monitor._thread.is_alive()
        )
        resource_monitor.stop()

    metrics.cpu_usage.set.assert_called_with(12.5)
    metrics.ram_usage.set.assert_called_with(1.0)
    assert any(
        record.levelno == logging.ERROR
        and isinstance(record.exc_info[1], psutil.AccessDenied)
        for record in caplog.records
        if record.exc_info
    )


def test_metric_adapter_error_stops_instead_of_being_retried(monkeypatch):
    failure = RuntimeError("metric adapter failed")
    captured = []
    thread_failed = threading.Event()

    class FailingGauge:
        calls = 0
        last_value = None

        def set(self, value):
            self.calls += 1
            self.last_value = value
            raise failure

    def capture_thread_failure(args):
        captured.append(args)
        thread_failed.set()

    monkeypatch.setattr(threading, "excepthook", capture_thread_failure)
    cpu_usage = FailingGauge()
    metrics = types.SimpleNamespace(cpu_usage=cpu_usage)
    resource_monitor = ResourceMonitor(metrics, interval=0.01)
    resource_monitor.process = MagicMock(pid=os.getpid())
    resource_monitor.process.cpu_percent.return_value = 12.5
    resource_monitor.process.memory_info.return_value = types.SimpleNamespace(
        rss=1024 * 1024
    )

    resource_monitor.start()
    assert thread_failed.wait(0.5)
    assert _wait_for(
        lambda: (
            resource_monitor._thread is not None
            and not resource_monitor._thread.is_alive()
        )
    )
    resource_monitor.stop()

    assert cpu_usage.calls == 1
    assert cpu_usage.last_value == 12.5
    assert len(captured) == 1
    assert captured[0].exc_type is RuntimeError
    assert captured[0].exc_value is failure


def test_stop_is_responsive():
    metrics = _make_metrics_stub()
    resource_monitor = ResourceMonitor(metrics, interval=10)
    resource_monitor.start()
    assert _wait_for(
        lambda: (
            resource_monitor._thread is not None and resource_monitor._thread.is_alive()
        ),
        timeout=0.1,
    )

    started_at = time.monotonic()
    resource_monitor.stop()
    elapsed = time.monotonic() - started_at
    assert elapsed < 1.5, f"stop() was not responsive: took {elapsed:.2f}s"


def test_max_supported_interval_starts_and_stops():
    metrics = _make_metrics_stub()
    resource_monitor = ResourceMonitor(metrics, interval=threading.TIMEOUT_MAX)
    resource_monitor.start()
    assert resource_monitor.is_running
    worker_thread = resource_monitor._thread
    assert worker_thread is not None
    resource_monitor.stop()
    assert not worker_thread.is_alive()
    assert not resource_monitor.is_running


def test_double_start_no_extra_thread():
    metrics = _make_metrics_stub()
    resource_monitor = ResourceMonitor(metrics, interval=1)
    resource_monitor.start()
    first_thread = resource_monitor._thread
    resource_monitor.start()
    assert resource_monitor._thread is first_thread, (
        "start() should not replace a live thread"
    )
    resource_monitor.stop()


def test_concurrent_start_is_single_threaded(monkeypatch: pytest.MonkeyPatch):
    metrics = _make_metrics_stub()
    resource_monitor = ResourceMonitor(metrics, interval=1)

    started_threads = []

    class CountingThread(threading.Thread):
        def __init__(self, *args, **kwargs):
            started_threads.append(self)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(monitor_module, "Thread", CountingThread)
    start_gate = threading.Barrier(3)

    def _runner():
        start_gate.wait()
        resource_monitor.start()

    callers = [threading.Thread(target=_runner), threading.Thread(target=_runner)]
    for caller in callers:
        caller.start()
    start_gate.wait()
    for caller in callers:
        caller.join()

    assert len(started_threads) == 1
    assert resource_monitor._thread is not None
    resource_monitor.stop()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
@pytest.mark.filterwarnings(
    r"ignore:This process .* is multi-threaded, use of fork\(\) may lead to deadlocks in the child:DeprecationWarning"
)
def test_pre_fork_monitor_samples_the_child_process(monkeypatch):
    sample_reader, sample_writer = os.pipe()

    class RecordingProcess:
        def __init__(self, pid):
            self.pid = pid
            self._reported = False

        def cpu_percent(self, interval=None):
            assert interval is None
            return 1.0

        def memory_info(self):
            if not self._reported:
                os.write(sample_writer, f"{self.pid}\n".encode())
                self._reported = True
            return types.SimpleNamespace(rss=1024 * 1024)

    monkeypatch.setattr(monitor_module.psutil, "Process", RecordingProcess)
    resource_monitor = ResourceMonitor(_make_metrics_stub(), interval=0.01)

    try:
        child_pid = os.fork()
        if child_pid == 0:
            os.close(sample_reader)
            try:
                resource_monitor.start()
                time.sleep(0.05)
                resource_monitor.stop()
            except Exception:  # noqa: BLE001 - child failures become exit status.
                os._exit(1)
            os.close(sample_writer)
            os._exit(0)

        os.close(sample_writer)
        sample_writer = -1
        readable, _, _ = select.select([sample_reader], [], [], 2.0)
        sampled_pid = os.read(sample_reader, 64) if readable else b""
        _, child_status = os.waitpid(child_pid, 0)

        assert os.waitstatus_to_exitcode(child_status) == 0
        assert sampled_pid == f"{child_pid}\n".encode()
    finally:
        if sample_writer >= 0:
            os.close(sample_writer)
        os.close(sample_reader)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
@pytest.mark.filterwarnings(
    r"ignore:This process .* is multi-threaded, use of fork\(\) may lead to deadlocks in the child:DeprecationWarning"
)
def test_fork_resets_parent_owned_lifecycle_lock():
    join_entered = threading.Event()
    release_join = threading.Event()

    class BlockingThread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            assert timeout == 2.0
            join_entered.set()
            release_join.wait(2.0)

    resource_monitor = ResourceMonitor(_make_metrics_stub(), interval=0.01)
    resource_monitor._thread = BlockingThread()
    parent_stop = threading.Thread(target=resource_monitor.stop)
    parent_stop.start()
    assert join_entered.wait(1.0)

    try:
        child_pid = os.fork()
        if child_pid == 0:
            signal.signal(signal.SIGALRM, lambda _signum, _frame: os._exit(77))
            signal.alarm(2)
            try:
                resource_monitor.start()
                assert (
                    resource_monitor._thread is not None
                    and resource_monitor._thread.is_alive()
                )
                resource_monitor.stop()
            except Exception:  # noqa: BLE001 - child failures become exit status.
                os._exit(1)
            os._exit(0)

        release_join.set()
        parent_stop.join(2.0)
        _, child_status = os.waitpid(child_pid, 0)

        assert not parent_stop.is_alive()
        assert os.waitstatus_to_exitcode(child_status) == 0
    finally:
        release_join.set()
        parent_stop.join(2.0)


@pytest.mark.parametrize(
    "interval",
    [
        0,
        -1,
        float("nan"),
        float("inf"),
        10**1000,
        threading.TIMEOUT_MAX * 2,
        True,
        "1",
    ],
)
def test_invalid_interval_rejected_with_structured_error(interval):
    metrics = _make_metrics_stub()

    with pytest.raises(InvalidMonitorIntervalError) as raised:
        ResourceMonitor(metrics, interval=interval)

    assert raised.value.code is MonitorErrorCode.INVALID_INTERVAL
    assert raised.value.max_interval == threading.TIMEOUT_MAX
    assert raised.value.interval == interval or (
        math.isnan(raised.value.interval) and math.isnan(interval)
    )


def test_missing_gauge_attributes():
    metrics = MagicMock(spec=[])  # no attributes
    resource_monitor = ResourceMonitor(metrics, interval=0.01)
    resource_monitor.process = MagicMock(pid=os.getpid())
    resource_monitor.process.cpu_percent.return_value = 1.0
    resource_monitor.process.memory_info.return_value = types.SimpleNamespace(
        rss=1024 * 1024
    )
    resource_monitor.start()
    assert _wait_for(
        lambda: (
            resource_monitor.process.memory_info.called
            and resource_monitor._thread is not None
            and resource_monitor._thread.is_alive()
        ),
        timeout=0.5,
    )
    resource_monitor.stop()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_is_running_after_fork_does_not_use_parent_lock():
    resource_monitor = ResourceMonitor(_make_metrics_stub(), interval=1)
    with resource_monitor._thread_lock:
        child_pid = os.fork()
        if child_pid == 0:
            signal.signal(signal.SIGALRM, lambda _signum, _frame: os._exit(77))
            signal.alarm(2)
            os._exit(1 if resource_monitor.is_running else 0)
        _, child_status = os.waitpid(child_pid, 0)
    assert os.waitstatus_to_exitcode(child_status) == 0
