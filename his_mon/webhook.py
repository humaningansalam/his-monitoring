from __future__ import annotations

import logging
import threading
from queue import Queue
from typing import cast

import requests

from . import _runtime
from ._runtime import WebhookDeliveryErrorCode, WebhookState

_logger = logging.getLogger(__name__)
_HTTP_TIMEOUT_SECONDS = 5
_WORKER_THREAD_NAME = "his-mon-webhook"


class _StopCommand:
    pass


WebhookQueueItem = str | _StopCommand


class WebhookManager:
    def __init__(self, url: str) -> None:
        self.url = url
        self.queue: Queue[WebhookQueueItem] = Queue()
        self._stop_command = _StopCommand()
        self._state_lock = threading.Lock()
        self._state = WebhookState.RUNNING
        self._thread = threading.Thread(
            target=self._worker,
            name=_WORKER_THREAD_NAME,
            daemon=True,
        )
        self._thread.start()

    @property
    def state(self) -> WebhookState:
        with self._state_lock:
            return self._state

    def send(self, message: str) -> bool:
        with self._state_lock:
            if self._state is not WebhookState.RUNNING:
                _logger.debug("Webhook manager is stopping; alert ignored")
                return False
            self.queue.put(message)
            return True

    def _request_stop(self, drain: bool) -> WebhookState:
        called_from_worker = self._thread is threading.current_thread()

        with self._state_lock:
            if self._state is WebhookState.STOPPED:
                return self._state
            if self._state is WebhookState.RUNNING:
                self._state = (
                    WebhookState.DRAINING
                    if drain and not called_from_worker
                    else WebhookState.STOPPING
                )
                self.queue.put(self._stop_command)
            elif self._state is WebhookState.DRAINING and not drain:
                self._state = WebhookState.STOPPING
            return self._state

    def _wait_for_stop(self, timeout: float | None) -> WebhookState:
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)
        return self.state

    def _deliver(self, message: str) -> None:
        try:
            response = requests.post(
                self.url,
                json={"text": message},
                timeout=_HTTP_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
        except (requests.RequestException, OSError) as error:
            _logger.error(
                "Webhook delivery failed: %s",
                type(error).__name__,
                extra={
                    "his_mon_code": WebhookDeliveryErrorCode.TRANSPORT_ERROR,
                    "error_type": type(error).__name__,
                },
            )
            return

        if not 200 <= response.status_code < 300:
            _logger.error(
                "Webhook delivery failed: HTTP %s",
                response.status_code,
                extra={
                    "his_mon_code": WebhookDeliveryErrorCode.HTTP_STATUS,
                    "http_status": response.status_code,
                },
            )

    def _worker(self) -> None:
        try:
            while True:
                item = self.queue.get()
                try:
                    if item is self._stop_command:
                        return

                    with self._state_lock:
                        state = self._state
                    if state is WebhookState.STOPPING:
                        _logger.debug("Webhook manager stopped; queued alert discarded")
                    else:
                        self._deliver(cast(str, item))
                finally:
                    self.queue.task_done()
        finally:
            with self._state_lock:
                self._state = WebhookState.STOPPED


def init_webhook(url: str | None) -> None:
    """Initialize the process-wide webhook worker once."""
    normalized_url = url.strip() if url else ""
    if not normalized_url:
        return

    with _runtime.webhook_lock:
        manager = _runtime.webhook_manager
        if manager is None or manager.state is WebhookState.STOPPED:
            manager = WebhookManager(normalized_url)
            _runtime.webhook_url = normalized_url
            _runtime.webhook_manager = manager
            _logger.info("Webhook initialized")
        elif manager.url != normalized_url:
            _logger.warning(
                "Webhook is already initialized; a different URL was ignored"
            )


def send_alert(message: str) -> None:
    """Queue an alert when webhook delivery has been configured."""
    with _runtime.webhook_lock:
        manager = _runtime.webhook_manager
        configured_url = _runtime.webhook_url
        if configured_url is not None and (
            manager is None or manager.state is WebhookState.STOPPED
        ):
            manager = WebhookManager(configured_url)
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
    """Stop the process-wide worker, optionally draining queued alerts first."""
    with _runtime.webhook_lock:
        manager = _runtime.webhook_manager
        _runtime.webhook_url = None
        if manager is None:
            return
        requested_state = manager._request_stop(drain)

    wait_timeout = None if requested_state is WebhookState.DRAINING else timeout
    state = manager._wait_for_stop(wait_timeout)
    if state is WebhookState.STOPPED:
        with _runtime.webhook_lock:
            if _runtime.webhook_manager is manager:
                _runtime.webhook_manager = None
