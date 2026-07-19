from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock, RLock
from typing import TYPE_CHECKING

try:
    from os import register_at_fork
except ImportError:  # Windows does not expose fork lifecycle hooks.
    register_at_fork = None

if TYPE_CHECKING:
    from .webhook import WebhookManager


class WebhookDeliveryErrorCode(str, Enum):
    HTTP_STATUS = "http_status"
    TRANSPORT_ERROR = "transport_error"


class WebhookDeliveryError(RuntimeError):
    code = WebhookDeliveryErrorCode.HTTP_STATUS

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Webhook delivery failed: HTTP {status_code}")


class WebhookState(str, Enum):
    RUNNING = "running"
    DRAINING = "draining"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class WebhookConfig:
    url: str


@dataclass(frozen=True, slots=True)
class WebhookAlert:
    message: str


@dataclass(frozen=True, slots=True)
class WebhookStopCommand:
    pass


WEBHOOK_STOP = WebhookStopCommand()
logging_setup_lock = RLock()
monitor_rebind_lock = Lock()
webhook_lock = Lock()
webhook_config: WebhookConfig | None = None
webhook_manager: WebhookManager | None = None


def _after_fork_child() -> None:
    global logging_setup_lock, monitor_rebind_lock, webhook_lock, webhook_manager

    # Locks and threads belong to the parent process. The immutable webhook
    # config remains the source for lazy child-local worker creation.
    logging_setup_lock = RLock()
    monitor_rebind_lock = Lock()
    webhook_lock = Lock()
    webhook_manager = None


if register_at_fork is not None:
    register_at_fork(after_in_child=_after_fork_child)
