import threading
import requests
import logging
from queue import Queue, Empty
from typing import Optional

_logger = logging.getLogger(__name__)
_webhook_manager: Optional["WebhookManager"] = None
_webhook_lock = threading.Lock()


class WebhookManager:
    def __init__(self, url: str):
        self.url = url
        self.queue = Queue()
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def send(self, message: str) -> bool:
        with self._state_lock:
            if self._closed:
                _logger.debug("Webhook manager is stopped; alert ignored")
                return False
            self.queue.put(message)
            return True

    def stop(self, drain: bool = False, timeout: float = 2.0) -> None:
        is_worker_thread = self._thread is threading.current_thread()

        with self._state_lock:
            self._closed = True
            if not drain or is_worker_thread:
                self._stop_event.set()

        if drain and not is_worker_thread:
            self.queue.join()
            self._stop_event.set()

        if not is_worker_thread:
            self._thread.join(timeout=timeout)

    def _worker(self):
        while True:
            try:
                msg = self.queue.get(timeout=0.1)
            except Empty:
                if self._stop_event.is_set():
                    return
                continue
            try:
                if self._stop_event.is_set():
                    # A non-draining shutdown drops queued alerts, but each
                    # dropped item must still be acknowledged for Queue.join().
                    _logger.debug("Webhook manager stopped; queued alert discarded")
                else:
                    self._post(msg)
            except Exception:
                _logger.exception("Webhook delivery failed")
            finally:
                self.queue.task_done()

    def _post(self, message: str):
        payload = {"text": message}
        response = requests.post(self.url, json=payload, timeout=5)
        if not response.ok:
            raise RuntimeError(
                f"Webhook delivery failed: HTTP {response.status_code} {response.reason}"
            )


def init_webhook(url: str | None):
    """Initialize webhook manager."""
    global _webhook_manager
    if not url:
        return None

    with _webhook_lock:
        if _webhook_manager is None:
            _webhook_manager = WebhookManager(url)
            _logger.info("Webhook initialized")
        elif _webhook_manager.url != url:
            _logger.warning("Webhook already initialized with a different URL; ignoring new URL")
        return _webhook_manager


def send_alert(message: str):
    """Send webhook."""
    if _webhook_manager:
        _webhook_manager.send(message)
    else:
        _logger.debug("Webhook not initialized; alert ignored")


def shutdown_webhook(*, drain: bool = False, timeout: float = 2.0) -> None:
    """Shutdown the global webhook manager."""
    global _webhook_manager

    with _webhook_lock:
        manager = _webhook_manager
        _webhook_manager = None

    if manager is not None:
        manager.stop(drain=drain, timeout=timeout)
