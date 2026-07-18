import logging
import threading
from queue import Queue

import requests

from . import _runtime
from ._runtime import (
    WebhookDeliveryError,
    WebhookDeliveryErrorCode,
    WebhookState,
)

_logger = logging.getLogger(__name__)


class WebhookManager:
    def __init__(self, config: _runtime.WebhookConfig):
        self.config = config
        self.queue: Queue[
            _runtime.WebhookAlert | _runtime.WebhookStopCommand
        ] = Queue()
        self._state_lock = threading.Lock()
        self._state = WebhookState.RUNNING
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return self.config.url

    @property
    def state(self) -> WebhookState:
        with self._state_lock:
            return self._state

    def send(self, message: str) -> bool:
        with self._state_lock:
            if self._state is not WebhookState.RUNNING:
                _logger.debug("Webhook manager is stopped; alert ignored")
                return False
            self.queue.put(_runtime.WebhookAlert(message))
            return True

    def _request_stop(self, drain: bool) -> WebhookState:
        is_worker_thread = self._thread is threading.current_thread()

        with self._state_lock:
            if self._state is WebhookState.STOPPED:
                return self._state
            if self._state is WebhookState.RUNNING:
                self._state = (
                    WebhookState.DRAINING
                    if drain and not is_worker_thread
                    else WebhookState.STOPPING
                )
                self.queue.put(_runtime.WEBHOOK_STOP)
            elif self._state is WebhookState.DRAINING and not drain:
                self._state = WebhookState.STOPPING
            return self._state

    def _wait_for_stop(self, timeout: float | None) -> WebhookState:
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)
        return self.state

    def stop(
        self,
        drain: bool = False,
        timeout: float | None = 2.0,
    ) -> WebhookState:
        requested_state = self._request_stop(drain)
        wait_timeout = None if requested_state is WebhookState.DRAINING else timeout
        return self._wait_for_stop(wait_timeout)

    def _worker(self):
        try:
            while True:
                command = self.queue.get()
                try:
                    if isinstance(command, _runtime.WebhookStopCommand):
                        return

                    with self._state_lock:
                        state = self._state
                    if state is WebhookState.STOPPING:
                        _logger.debug("Webhook manager stopped; queued alert discarded")
                    else:
                        try:
                            self._post(command.message)
                        except WebhookDeliveryError as error:
                            _logger.error(
                                "Webhook delivery failed: HTTP %s",
                                error.status_code,
                                extra={
                                    "his_mon_code": error.code,
                                    "http_status": error.status_code,
                                },
                            )
                        except (requests.RequestException, OSError) as error:
                            _logger.error(
                                "Webhook delivery failed: %s",
                                type(error).__name__,
                                extra={
                                    "his_mon_code": (
                                        WebhookDeliveryErrorCode.TRANSPORT_ERROR
                                    ),
                                    "error_type": type(error).__name__,
                                },
                            )
                finally:
                    self.queue.task_done()
        finally:
            with self._state_lock:
                self._state = WebhookState.STOPPED

    def _post(self, message: str):
        payload = {"text": message}
        response = requests.post(
            self.url,
            json=payload,
            timeout=5,
            allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            raise WebhookDeliveryError(response.status_code)


def init_webhook(url: str | None):
    """Initialize webhook manager."""
    if not url:
        return None

    with _runtime.webhook_lock:
        manager = _runtime.webhook_manager
        if manager is None or manager.state is WebhookState.STOPPED:
            config = _runtime.WebhookConfig(url)
            manager = WebhookManager(config)
            _runtime.webhook_config = config
            _runtime.webhook_manager = manager
            _logger.info("Webhook initialized")
        elif manager.url != url:
            _logger.warning("Webhook already initialized with a different URL; ignoring new URL")


def send_alert(message: str):
    """Send webhook."""
    with _runtime.webhook_lock:
        manager = _runtime.webhook_manager
        if manager is None and _runtime.webhook_config is not None:
            manager = WebhookManager(_runtime.webhook_config)
            _runtime.webhook_manager = manager
    if manager is None:
        _logger.debug("Webhook not initialized; alert ignored")
        return
    manager.send(message)


def shutdown_webhook(
    *,
    drain: bool = False,
    timeout: float | None = 2.0,
) -> None:
    """Shutdown the global webhook manager."""
    with _runtime.webhook_lock:
        manager = _runtime.webhook_manager
        _runtime.webhook_config = None
        if manager is None:
            return
        requested_state = manager._request_stop(drain)

    wait_timeout = None if requested_state is WebhookState.DRAINING else timeout
    state = manager._wait_for_stop(wait_timeout)
    if state is WebhookState.STOPPED:
        with _runtime.webhook_lock:
            if _runtime.webhook_manager is manager:
                _runtime.webhook_manager = None
