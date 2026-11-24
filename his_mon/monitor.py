from __future__ import annotations

import logging
import math
import os
from enum import Enum
from numbers import Real
from threading import TIMEOUT_MAX, Event, Lock, Thread, current_thread
from typing import Protocol, cast

import psutil

from . import _runtime

_WORKER_THREAD_NAME = "his-mon-resource-monitor"


class _Gauge(Protocol):
    def set(self, value: float) -> None: ...


class MonitorErrorCode(str, Enum):  # noqa: UP042 - preserve existing string formatting.
    INVALID_INTERVAL = "invalid_interval"


class InvalidMonitorIntervalError(ValueError):
    code = MonitorErrorCode.INVALID_INTERVAL

    def __init__(self, interval: object) -> None:
        self.interval = interval
        self.max_interval = TIMEOUT_MAX
        super().__init__(
            f"interval must be a finite number greater than 0 and at most {TIMEOUT_MAX}"
        )


def _validated_interval(interval: object) -> float:
    if isinstance(interval, bool):
        raise InvalidMonitorIntervalError(interval)
    if not isinstance(interval, Real):
        raise InvalidMonitorIntervalError(interval)
    try:
        seconds = float(interval)
    except (OverflowError, ValueError) as error:
        raise InvalidMonitorIntervalError(interval) from error
    if seconds <= 0 or not math.isfinite(seconds) or seconds > TIMEOUT_MAX:
        raise InvalidMonitorIntervalError(interval)
    return seconds


def _set_metric_if_present(
    metrics: object,
    attribute_name: str,
    value: float,
) -> None:
    metric = getattr(metrics, attribute_name, None)
    if metric is not None:
        cast(_Gauge, metric).set(value)


class ResourceMonitor:
    """Sample the current process and publish CPU and memory gauges."""

    def __init__(self, metrics_obj: object, interval: float | int = 5.0) -> None:
        self.metrics = metrics_obj
        self.interval = _validated_interval(interval)
        self.process = psutil.Process(os.getpid())
        self._stop_event = Event()
        self.logger = logging.getLogger("ResourceMonitor")
        self._thread: Thread | None = None
        self._thread_lock = Lock()

    @property
    def is_running(self) -> bool:
        self._rebind_after_fork_if_needed()
        with self._thread_lock:
            return self._thread is not None and self._thread.is_alive()

    def _rebind_after_fork_if_needed(self) -> None:
        current_pid = os.getpid()
        if current_pid == self.process.pid:
            return

        with _runtime.monitor_rebind_lock:
            current_pid = os.getpid()
            if current_pid == self.process.pid:
                return

            process = psutil.Process(current_pid)
            self._stop_event = Event()
            self._thread = None
            self._thread_lock = Lock()
            self.process = process

    def start(self) -> None:
        """Start one monitor thread if it is not already running."""
        self._rebind_after_fork_if_needed()
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return

            self._stop_event.clear()
            self._thread = Thread(
                target=self._run,
                name=_WORKER_THREAD_NAME,
                daemon=True,
            )
            self._thread.start()
            self.logger.info("Resource monitor started")

    def stop(self) -> None:
        """Signal the monitor thread and wait briefly for it to exit."""
        self._rebind_after_fork_if_needed()
        with self._thread_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()

        if thread is not current_thread():
            thread.join(timeout=2.0)

        with self._thread_lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None

    def _sample_process(self) -> tuple[float, float]:
        cpu_percent = self.process.cpu_percent(interval=None)
        resident_memory_mb = self.process.memory_info().rss / (1024 * 1024)
        return cpu_percent, resident_memory_mb

    def _publish_sample(
        self,
        cpu_percent: float,
        resident_memory_mb: float,
    ) -> None:
        _set_metric_if_present(self.metrics, "cpu_usage", cpu_percent)
        _set_metric_if_present(self.metrics, "ram_usage", resident_memory_mb)

    def _run(self) -> None:
        try:
            self.process.cpu_percent(interval=None)
        except psutil.Error:
            self.logger.exception("Resource monitor sampling failed")

        while not self._stop_event.wait(self.interval):
            try:
                sample = self._sample_process()
            except psutil.Error:
                self.logger.exception("Resource monitor sampling failed")
                continue
            self._publish_sample(*sample)
