"""Focused tests for the optimized ResourceMonitor sampling loop.

Covers:
- start() / stop() public API lifecycle
- cpu_usage and ram_usage gauges are set after one interval
- stop() completes quickly (responsiveness)
- Double-start guard (no second thread)
- Missing-gauge tolerance
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

from his_mon.monitor import ResourceMonitor


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


def test_double_start_no_extra_thread():
    """Calling start() twice does not create a second monitoring thread."""
    metrics = _make_metrics()
    mon = ResourceMonitor(metrics, interval=1)
    mon.start()
    first_thread = mon._thread
    mon.start()  # should be a no-op
    assert mon._thread is first_thread, "start() should not replace a live thread"
    mon.stop()


def test_zero_interval_rejected():
    """interval=0 is rejected before the monitor thread can start."""
    metrics = _make_metrics()
    try:
        ResourceMonitor(metrics, interval=0)
    except ValueError as exc:
        assert str(exc) == "interval must be greater than 0"
    else:
        raise AssertionError("interval=0 should raise ValueError")


def test_negative_interval_rejected():
    """Negative intervals are rejected before the monitor thread can start."""
    metrics = _make_metrics()
    try:
        ResourceMonitor(metrics, interval=-1)
    except ValueError as exc:
        assert str(exc) == "interval must be greater than 0"
    else:
        raise AssertionError("negative interval should raise ValueError")


def test_missing_gauge_attributes():
    """Monitor tolerates metrics objects that lack cpu_usage or ram_usage."""
    metrics = MagicMock(spec=[])  # no attributes
    mon = ResourceMonitor(metrics, interval=1)
    mon.start()
    assert _wait_for(lambda: mon._thread is not None and mon._thread.is_alive(), timeout=0.1)
    mon.stop()  # must not raise


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
