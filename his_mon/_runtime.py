from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from threading import Lock, RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .webhook import WebhookManager

_register_at_fork: Callable[..., None] | None
try:
    from os import register_at_fork as _register_at_fork
except ImportError:  # pragma: no cover - Windows has no fork lifecycle.
    _register_at_fork = None


class WebhookDeliveryErrorCode(str, Enum):  # noqa: UP042 - preserve existing string formatting.
    HTTP_STATUS = "http_status"
    TRANSPORT_ERROR = "transport_error"


class WebhookState(str, Enum):  # noqa: UP042 - preserve existing string formatting.
    RUNNING = "running"
    DRAINING = "draining"
    STOPPING = "stopping"
    STOPPED = "stopped"


logging_setup_lock = RLock()
monitor_rebind_lock = Lock()
webhook_lock = Lock()
webhook_url: str | None = None
webhook_manager: WebhookManager | None = None


def _reset_process_local_state_after_fork() -> None:
    global logging_setup_lock, monitor_rebind_lock, webhook_lock, webhook_manager

    logging_setup_lock = RLock()
    monitor_rebind_lock = Lock()
    webhook_lock = Lock()
    webhook_manager = None


if _register_at_fork is not None:
    _register_at_fork(after_in_child=_reset_process_local_state_after_fork)
