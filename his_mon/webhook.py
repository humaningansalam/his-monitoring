import threading
import requests
import logging
from queue import Queue, Empty

_logger = logging.getLogger(__name__)
_webhook_manager = None


class WebhookManager:
    def __init__(self, url: str):
        self.url = url
        self.queue = Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def send(self, message: str):
        self.queue.put(message)

    def _worker(self):
        while not self._stop_event.is_set():
            try:
                msg = self.queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                self._post(msg)
            except Exception as e:
                _logger.error("Webhook worker error: %s", e)
            finally:
                self.queue.task_done()

    def _post(self, message: str):
        payload = {"text": message}
        try:
            response = requests.post(self.url, json=payload, timeout=5)
        except Exception as e:
            _logger.error("Webhook send failed: %s", e)
            raise
        if not response.ok:
            _logger.error(
                "Webhook delivery failed: HTTP %s %s",
                response.status_code,
                response.reason,
            )
            raise RuntimeError(
                f"Webhook delivery failed: HTTP {response.status_code} {response.reason}"
            )


# --- Public Interface ---

def init_webhook(url: str | None):
    """Initialize webhook manager."""
    global _webhook_manager
    if not url:
        return
    if _webhook_manager is None:
        _webhook_manager = WebhookManager(url)
        _logger.info("Webhook initialized")


def send_alert(message: str):
    """Send webhook."""
    if _webhook_manager:
        _webhook_manager.send(message)
    else:
        _logger.debug("Webhook not initialized. Ignored message: %s", message)