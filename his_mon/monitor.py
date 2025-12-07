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
        self.metrics = metrics_obj
        self.interval = interval
        self.process = psutil.Process(os.getpid())
        self._stop_event = threading.Event()
        self.logger = logging.getLogger("ResourceMonitor")
        self._thread = None

    def start(self):
        """Start the monitoring thread."""
        if self._thread and self._thread.is_alive(): return
        
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.logger.info("✅ Resource Monitor Started")

    def stop(self):
        """Stop the monitoring thread."""
        self._stop_event.set()
        if self._thread: self._thread.join(timeout=2.0)

    def _run(self):
        while not self._stop_event.is_set():
            try:
                cpu = self.process.cpu_percent(interval=1.0)
                
                ram = self.process.memory_info().rss / (1024 * 1024)

                if hasattr(self.metrics, 'cpu_usage'):
                    self.metrics.cpu_usage.set(cpu)
                if hasattr(self.metrics, 'ram_usage'):
                    self.metrics.ram_usage.set(ram)

            except Exception as e:
                self.logger.error(f"Monitor error: {e}")
            
            self._stop_event.wait(max(1, self.interval - 1))