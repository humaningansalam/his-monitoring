"""Focused tests for WebhookManager delivery reliability.

Covers:
- Successful POST: correct payload and timeout are passed to requests.post.
- Non-2xx response: error is logged, exception is raised.
- requests.post exception: error is logged, exception propagates.
- Queue task_done() is called even after a failed send (no Queue.join() hang).
"""
from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock, patch

import pytest

import his_mon.webhook as webhook
from his_mon.webhook import WebhookManager, send_alert


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ok_response(status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.ok = True
    resp.status_code = status_code
    resp.reason = "OK"
    return resp


def _make_error_response(status_code: int = 500, reason: str = "Internal Server Error") -> MagicMock:
    resp = MagicMock()
    resp.ok = False
    resp.status_code = status_code
    resp.reason = reason
    return resp


def _stop_manager(manager: WebhookManager, timeout: float = 1.0):
    manager._stop_event.set()
    manager._thread.join(timeout)
    assert not manager._thread.is_alive(), f"Webhook worker did not stop within {timeout:.1f}s"


def _wait_for_queue_drain(manager: WebhookManager, timeout: float = 1.0):
    deadline = threading.Event()

    def _waiter():
        while not deadline.is_set():
            if manager.queue.unfinished_tasks == 0:
                return
            deadline.wait(0.01)

    thread = threading.Thread(target=_waiter, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        deadline.set()
        _stop_manager(manager, timeout)
        thread.join(timeout)
        pytest.fail(
            f"Webhook queue did not drain within {timeout:.1f}s; unfinished_tasks={manager.queue.unfinished_tasks}"
        )
    _stop_manager(manager, timeout)
    thread.join(timeout)
    assert not thread.is_alive(), "Queue drain helper thread did not exit after stopping the worker"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWebhookManagerPost:
    """Unit tests for WebhookManager._post() — worker thread is not involved."""

    def test_successful_post_payload_and_timeout(self):
        """_post() calls requests.post with correct JSON payload and 5-second timeout."""
        manager = WebhookManager.__new__(WebhookManager)
        manager.url = "https://example.com/hook"

        with patch("his_mon.webhook.requests.post", return_value=_make_ok_response()) as mock_post:
            manager._post("hello world")

        mock_post.assert_called_once_with(
            "https://example.com/hook",
            json={"text": "hello world"},
            timeout=5,
        )

    def test_non_2xx_response_logs_error_and_raises(self, caplog):
        """_post() logs an error and raises RuntimeError on a non-2xx HTTP response."""
        manager = WebhookManager.__new__(WebhookManager)
        manager.url = "https://example.com/hook"

        with patch("his_mon.webhook.requests.post", return_value=_make_error_response(503, "Service Unavailable")):
            with caplog.at_level(logging.ERROR, logger="his_mon.webhook"):
                with pytest.raises(RuntimeError, match="HTTP 503"):
                    manager._post("test message")

        assert any("503" in r.message for r in caplog.records), (
            "Expected an ERROR log containing the HTTP status code"
        )

    def test_requests_post_exception_logs_error_and_reraises(self, caplog):
        """_post() logs an error and re-raises when requests.post raises an exception."""
        manager = WebhookManager.__new__(WebhookManager)
        manager.url = "https://example.com/hook"

        with patch("his_mon.webhook.requests.post", side_effect=ConnectionError("timeout")):
            with caplog.at_level(logging.ERROR, logger="his_mon.webhook"):
                with pytest.raises(ConnectionError):
                    manager._post("test message")

        assert any("timeout" in r.message for r in caplog.records), (
            "Expected an ERROR log containing the exception message"
        )


class TestWebhookManagerWorkerQueueAccounting:
    """Integration tests exercising the worker thread and Queue accounting."""

    def test_queue_task_done_called_after_successful_send(self):
        """queue.task_done() is called after a successful send so join() never hangs."""
        with patch("his_mon.webhook.requests.post", return_value=_make_ok_response()):
            manager = WebhookManager("https://example.com/hook")
            manager.send("ok message")
            _wait_for_queue_drain(manager)

    def test_queue_task_done_called_after_failed_send_http_error(self):
        """queue.task_done() is called even when _post() raises due to non-2xx response."""
        with patch("his_mon.webhook.requests.post", return_value=_make_error_response(500)):
            manager = WebhookManager("https://example.com/hook")
            manager.send("bad message")
            _wait_for_queue_drain(manager)

    def test_queue_task_done_called_after_requests_exception(self):
        """queue.task_done() is called even when requests.post raises an exception."""
        with patch("his_mon.webhook.requests.post", side_effect=OSError("network down")):
            manager = WebhookManager("https://example.com/hook")
            manager.send("failing message")
            _wait_for_queue_drain(manager)


class TestSendAlertNoop:
    """Public send_alert() behavior when no webhook has been initialized."""

    def test_uninitialized_webhook_does_not_log_alert_payload(self, caplog, monkeypatch):
        """No-op alert sends must not leak alert content into debug logs."""
        secret_message = "service token sk_live_123 should not be logged"
        monkeypatch.setattr(webhook, "_webhook_manager", None)

        with caplog.at_level(logging.DEBUG, logger="his_mon.webhook"):
            send_alert(secret_message)

        assert caplog.records
        assert all(secret_message not in r.getMessage() for r in caplog.records)


if __name__ == "__main__":
    import pytest as _pytest
    raise SystemExit(_pytest.main([__file__, "-v"]))
