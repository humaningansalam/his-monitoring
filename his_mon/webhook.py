import threading
import requests
import json
from queue import Queue, Empty

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
                self._post(msg)
                self.queue.task_done()
            except Empty: continue
            except Exception as e: print(f"⚠️ Webhook worker error: {e}")

    def _post(self, message: str):
        try:
            payload = {"text": message}
            requests.post(self.url, json=payload, timeout=5)
        except Exception as e:
            print(f"⚠️ Webhook send failed: {e}")

# --- Public Interface ---

def init_webhook(url: str):
    """ Initialize webhook manager """
    global _webhook_manager
    if _webhook_manager is None:
        _webhook_manager = WebhookManager(url)
        print(f"✅ [HisMon] Webhook initialized")

def send_alert(message: str):
    """ Send webhook """
    if _webhook_manager:
        _webhook_manager.send(message)
    else:
        print(f"⚠️ Webhook not initialized. Ignored: {message}")