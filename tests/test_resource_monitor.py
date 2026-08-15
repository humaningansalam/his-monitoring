"""Focused tests for the optimized ResourceMonitor sampling loop.

Covers:
- start() / stop() public API lifecycle
- cpu_usage and ram_usage gauges are set after one interval
- stop() completes quickly (responsiveness)
- Double-start guard (no second thread)
- Missing-gauge tolerance
"""
from __future__ import annotations

import logging
import math
import os
import select
import signal
import time
import threading
import types
from unittest.mock import MagicMock
import psutil
import pytest

from his_mon import monitor
from his_mon.monitor import (
    InvalidMonitorIntervalError,
    MonitorErrorCode,
    ResourceMonitor,
)


def _make_metrics():
    """Return a minimal metrics stub with cpu_usage and ram_usage gauge mocks."""
    m = MagicMock()
    m.cpu_usage = MagicMock()
    m.ram_usage = MagicMock()
    return m


def _wait_for(predicate, timeout=1.2, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_start_stop_lifecycle():
    """Monitor starts, thread becomes alive, stop() joins within 2 s."""
    metrics = _make_metrics()
    mon = ResourceMonitor(metrics, interval=1)
    mon.start()
    assert mon._thread is not None
    assert mon._thread.is_alive()

    t0 = time.monotonic()
    mon.stop()
    elapsed = time.monotonic() - t0
    assert not mon._thread.is_alive(), "thread should have exited"
    assert elapsed < 2.0, f"stop() took too long: {elapsed:.2f}s"


def test_metrics_updated_after_one_interval():
    """After one interval elapses, cpu_usage.set() and ram_usage.set() are called."""
    metrics = _make_metrics()
    mon = ResourceMonitor(metrics, interval=1)
    mon.start()
    assert _wait_for(lambda: metrics.cpu_usage.set.called and metrics.ram_usage.set.called)
    mon.stop()

    assert metrics.cpu_usage.set.called, "cpu_usage.set() was never called"
    assert metrics.ram_usage.set.called, "ram_usage.set() was never called"
    # CPU value must be a non-negative float (0.0 is fine on an idle process).
    cpu_val = metrics.cpu_usage.set.call_args[0][0]
    assert isinstance(cpu_val, float) and cpu_val >= 0.0, f"unexpected cpu value: {cpu_val}"


def test_transient_sampling_error_does_not_stop_monitor(caplog):
    """A psutil sampling failure is logged and retried on the normal loop."""
    metrics = _make_metrics()
    mon = ResourceMonitor(metrics, interval=0.01)
    mon.process = MagicMock(pid=os.getpid())
    mon.process.cpu_percent.side_effect = [
        0.0,
        psutil.AccessDenied(pid=123),
        12.5,
    ]
    mon.process.memory_info.return_value = types.SimpleNamespace(rss=1024 * 1024)

    with caplog.at_level("ERROR", logger="ResourceMonitor"):
        mon.start()
        assert _wait_for(lambda: metrics.cpu_usage.set.called, timeout=0.5)
        assert mon._thread is not None and mon._thread.is_alive()
        mon.stop()

    metrics.cpu_usage.set.assert_called_with(12.5)
    metrics.ram_usage.set.assert_called_with(1.0)
    assert any(
        record.levelno == logging.ERROR and record.exc_info is not None
        for record in caplog.records
    )


def test_transient_initial_cpu_error_does_not_stop_monitor(caplog):
    metrics = _make_metrics()
    mon = ResourceMonitor(metrics, interval=0.01)
    mon.process = MagicMock(pid=os.getpid())
    mon.process.cpu_percent.side_effect = [psutil.AccessDenied(pid=123), 12.5]
    mon.process.memory_info.return_value = types.SimpleNamespace(rss=1024 * 1024)

    with caplog.at_level(logging.ERROR, logger="ResourceMonitor"):
        mon.start()
        assert _wait_for(lambda: metrics.cpu_usage.set.called, timeout=0.5)
        assert mon._thread is not None and mon._thread.is_alive()
        mon.stop()

    metrics.cpu_usage.set.assert_called_with(12.5)
    metrics.ram_usage.set.assert_called_with(1.0)
    assert any(
        record.levelno == logging.ERROR
        and isinstance(record.exc_info[1], psutil.AccessDenied)
        for record in caplog.records
        if record.exc_info
    )


def test_metric_adapter_error_stops_instead_of_being_retried(monkeypatch):
    """Consumer programming errors must not enter the psutil recovery loop."""
    failure = RuntimeError("metric adapter failed")
    captured = []
    thread_failed = threading.Event()

    class FailingGauge:
        calls = 0

        def set(self, value):
            self.calls += 1
            raise failure

    def capture_thread_failure(args):
        captured.append(args)
        thread_failed.set()

    monkeypatch.setattr(threading, "excepthook", capture_thread_failure)
    cpu_usage = FailingGauge()
    metrics = types.SimpleNamespace(cpu_usage=cpu_usage)
    mon = ResourceMonitor(metrics, interval=0.01)
    mon.process = MagicMock(pid=os.getpid())
    mon.process.cpu_percent.return_value = 12.5
    mon.process.memory_info.return_value = types.SimpleNamespace(rss=1024 * 1024)

    mon.start()
    assert thread_failed.wait(0.5)
    assert _wait_for(lambda: mon._thread is not None and not mon._thread.is_alive())
    mon.stop()

    assert cpu_usage.calls == 1
    assert len(captured) == 1
    assert captured[0].exc_type is RuntimeError
    assert captured[0].exc_value is failure


def test_stop_is_responsive():
    """stop() returns in well under interval seconds, not blocked by CPU sampling."""
    metrics = _make_metrics()
    # Long interval — if stop() blocks on sampling it would take > interval seconds.
    mon = ResourceMonitor(metrics, interval=10)
    mon.start()
    assert _wait_for(lambda: mon._thread is not None and mon._thread.is_alive(), timeout=0.1)

    t0 = time.monotonic()
    mon.stop()
    elapsed = time.monotonic() - t0
    # Should wake from Event.wait immediately — allow generous 1.5 s margin.
    assert elapsed < 1.5, f"stop() was not responsive: took {elapsed:.2f}s"


def test_max_supported_interval_starts_and_stops():
    metrics = _make_metrics()
    mon = ResourceMonitor(metrics, interval=threading.TIMEOUT_MAX)
    mon.start()
    assert mon._thread is not None and mon._thread.is_alive()
    mon.stop()
    assert not mon._thread.is_alive()


def test_double_start_no_extra_thread():
    """Calling start() twice does not create a second monitoring thread."""
    metrics = _make_metrics()
    mon = ResourceMonitor(metrics, interval=1)
    mon.start()
    first_thread = mon._thread
    mon.start()  # should be a no-op
    assert mon._thread is first_thread, "start() should not replace a live thread"
    mon.stop()


def test_concurrent_start_is_single_threaded(monkeypatch: pytest.MonkeyPatch):
    """Concurrent start() calls should not create more than one monitoring thread."""
    metrics = _make_metrics()
    mon = ResourceMonitor(metrics, interval=1)

    started_threads = []

    class CountingThread(threading.Thread):
        def __init__(self, *args, **kwargs):
            started_threads.append(self)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(monitor, "Thread", CountingThread)
    start_gate = threading.Barrier(3)

    def _runner():
        start_gate.wait()
        mon.start()

    callers = [threading.Thread(target=_runner), threading.Thread(target=_runner)]
    for caller in callers:
        caller.start()
    start_gate.wait()
    for caller in callers:
        caller.join()

    assert len(started_threads) == 1
    assert mon._thread is not None
    mon.stop()


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
            return 1.0

        def memory_info(self):
            if not self._reported:
                os.write(sample_writer, f"{self.pid}\n".encode())
                self._reported = True
            return types.SimpleNamespace(rss=1024 * 1024)

    monkeypatch.setattr(monitor.psutil, "Process", RecordingProcess)
    mon = ResourceMonitor(_make_metrics(), interval=0.01)

    try:
        child_pid = os.fork()
        if child_pid == 0:
            os.close(sample_reader)
            try:
                mon.start()
                time.sleep(0.05)
                mon.stop()
            except BaseException:
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
            join_entered.set()
            release_join.wait(2.0)

    mon = ResourceMonitor(_make_metrics(), interval=0.01)
    mon._thread = BlockingThread()
    parent_stop = threading.Thread(target=mon.stop)
    parent_stop.start()
    assert join_entered.wait(1.0)

    try:
        child_pid = os.fork()
        if child_pid == 0:
            signal.signal(signal.SIGALRM, lambda _signum, _frame: os._exit(77))
            signal.alarm(2)
            try:
                mon.start()
                assert mon._thread is not None and mon._thread.is_alive()
                mon.stop()
            except BaseException:
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
    """Invalid intervals are rejected through one typed configuration boundary."""
    metrics = _make_metrics()

    with pytest.raises(InvalidMonitorIntervalError) as raised:
        ResourceMonitor(metrics, interval=interval)

    assert raised.value.code is MonitorErrorCode.INVALID_INTERVAL
    assert raised.value.max_interval == threading.TIMEOUT_MAX
    assert raised.value.interval == interval or (
        math.isnan(raised.value.interval) and math.isnan(interval)
    )


def test_missing_gauge_attributes():
    """Monitor tolerates metrics objects that lack cpu_usage or ram_usage."""
    metrics = MagicMock(spec=[])  # no attributes
    mon = ResourceMonitor(metrics, interval=0.01)
    mon.process = MagicMock(pid=os.getpid())
    mon.process.cpu_percent.return_value = 1.0
    mon.process.memory_info.return_value = types.SimpleNamespace(rss=1024 * 1024)
    mon.start()
    assert _wait_for(
        lambda: mon.process.memory_info.called
        and mon._thread is not None
        and mon._thread.is_alive(),
        timeout=0.5,
    )
    mon.stop()  # must not raise


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
