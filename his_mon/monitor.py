import logging
import math
import os
from enum import Enum
from numbers import Real
from threading import TIMEOUT_MAX, Event, Lock, Thread, current_thread
from typing import Any

import psutil

from . import _runtime


class MonitorErrorCode(str, Enum):
    INVALID_INTERVAL = "invalid_interval"


class InvalidMonitorIntervalError(ValueError):
    code = MonitorErrorCode.INVALID_INTERVAL

    def __init__(self, interval: Any):
        self.interval = interval
        self.max_interval = TIMEOUT_MAX
        super().__init__(
            f"interval must be a finite number greater than 0 and at most {TIMEOUT_MAX}"
        )


def _validated_interval(interval: Any) -> float:
    if not isinstance(interval, Real) or isinstance(interval, bool):
        raise InvalidMonitorIntervalError(interval)
    try:
        seconds = float(interval)
    except (OverflowError, ValueError) as exc:
        raise InvalidMonitorIntervalError(interval) from exc
    if seconds <= 0 or not math.isfinite(seconds) or seconds > TIMEOUT_MAX:
        raise InvalidMonitorIntervalError(interval)
    return seconds


class ResourceMonitor:
    def __init__(self, metrics_obj: Any, interval: float = 5):
        """
        Background thread to monitor CPU and RAM usage.

        :param metrics_obj: An object inherited from BaseMetrics (must have cpu_usage/ram_usage attributes).
        :param interval: Update interval in seconds.
        """
        self.metrics = metrics_obj
        self.interval = _validated_interval(interval)
        self.process = psutil.Process(os.getpid())
        self._stop_event = Event()
        self.logger = logging.getLogger("ResourceMonitor")
        self._thread = None
        self._thread_lock = Lock()

    def _bind_current_process(self) -> None:
        current_pid = os.getpid()
        if current_pid == self.process.pid:
            return

        with _runtime.monitor_rebind_lock:
            current_pid = os.getpid()
            if current_pid == self.process.pid:
                return

            self.process = psutil.Process(current_pid)
            self._stop_event = Event()
            self._thread = None
            self._thread_lock = Lock()

    def start(self):
        """Start the monitoring thread."""
        self._bind_current_process()
        with self._thread_lock:
            if self._thread and self._thread.is_alive():
                return

            self._stop_event.clear()
            self._thread = Thread(target=self._run, daemon=True)
            self._thread.start()
            self.logger.info("Resource monitor started")

    def stop(self):
        """Stop the monitoring thread."""
        self._bind_current_process()
        with self._thread_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
            if thread is not current_thread():
                thread.join(timeout=2.0)

    def _run(self):
        # Prime the CPU counter so the first non-blocking sample is meaningful.
        try:
            self.process.cpu_percent(interval=None)
        except psutil.Error:
            self.logger.exception("Resource monitor error")
        while not self._stop_event.is_set():
            # Wait the full interval first, then sample.  The Event.wait() call
            # returns immediately when stop() sets the event, keeping stop()
            # responsive within one interval regardless of `self.interval`.
            self._stop_event.wait(self.interval)
            if self._stop_event.is_set():
                break
            try:
                # interval=None: non-blocking; returns delta since last call.
                cpu = self.process.cpu_percent(interval=None)
                ram = self.process.memory_info().rss / (1024 * 1024)
            except psutil.Error:
                self.logger.exception("Resource monitor error")
                continue

            cpu_usage = getattr(self.metrics, "cpu_usage", None)
            if cpu_usage is not None:
                cpu_usage.set(cpu)
            ram_usage = getattr(self.metrics, "ram_usage", None)
            if ram_usage is not None:
                ram_usage.set(ram)
