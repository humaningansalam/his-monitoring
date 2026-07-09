import os
import time
import threading
import psutil
import logging
from typing import Any

class ResourceMonitor:
    def __init__(self, metrics_obj: Any, interval: int = 5):
        """
        Background thread to monitor CPU and RAM usage.

        :param metrics_obj: An object inherited from BaseMetrics (must have cpu_usage/ram_usage attributes).
        :param interval: Update interval in seconds.
        """
        if interval <= 0:
            raise ValueError("interval must be greater than 0")

        self.metrics = metrics_obj
        self.interval = interval
        self.process = psutil.Process(os.getpid())
        self._stop_event = threading.Event()
        self.logger = logging.getLogger("ResourceMonitor")
        self._thread = None
        self._thread_lock = threading.Lock()

    def start(self):
        """Start the monitoring thread."""
        with self._thread_lock:
            if self._thread and self._thread.is_alive():
                return

            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            self.logger.info("Resource monitor started")

    def stop(self):
        """Stop the monitoring thread."""
        with self._thread_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
            if thread is not threading.current_thread():
                thread.join(timeout=2.0)

    def _run(self):
        # Prime the CPU counter so the first non-blocking sample is meaningful.
        self.process.cpu_percent(interval=None)
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

                if hasattr(self.metrics, 'cpu_usage'):
                    self.metrics.cpu_usage.set(cpu)
                if hasattr(self.metrics, 'ram_usage'):
                    self.metrics.ram_usage.set(ram)

            except Exception:
                self.logger.exception("Resource monitor error")
