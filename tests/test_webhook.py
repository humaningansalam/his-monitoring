"""Public-flow tests for webhook delivery, shutdown, and secrecy contracts."""

from __future__ import annotations

import importlib
import logging
import os
import select
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

import his_mon.webhook as webhook_module
from his_mon.webhook import (
    WebhookDeliveryErrorCode,
    init_webhook,
    send_alert,
    shutdown_webhook,
)


def _make_response(status_code: int, reason: str) -> SimpleNamespace:
    return SimpleNamespace(status_code=status_code, reason=reason)


@pytest.fixture(autouse=True)
def _reset_global_webhook():
    shutdown_webhook()
    yield
    shutdown_webhook()


class TestWebhookDelivery:
    def test_successful_public_delivery_uses_payload_and_timeout(self):
        with patch(
            "his_mon.webhook.requests.post",
            return_value=_make_response(204, "No Content"),
        ) as mock_post:
            assert init_webhook("https://example.com/hook") is None
            send_alert("hello world")
            assert shutdown_webhook(drain=True) is None

        mock_post.assert_called_once_with(
            "https://example.com/hook",
            json={"text": "hello world"},
            timeout=5,
            allow_redirects=False,
        )

    @pytest.mark.parametrize(
        ("status_code", "reason"),
        [
            (302, "Found"),
            (503, "Service Unavailable"),
            (503, "http failure secret"),
        ],
    )
    def test_non_2xx_public_delivery_logs_typed_failure(
        self,
        status_code,
        reason,
        caplog,
    ):
        secret_message = "http failure secret"
        with caplog.at_level(logging.ERROR, logger="his_mon.webhook"):
            with patch(
                "his_mon.webhook.requests.post",
                return_value=_make_response(status_code, reason),
            ):
                assert init_webhook("https://example.com/hook") is None
                send_alert(secret_message)
                shutdown_webhook(drain=True)

        failures = [
            record
            for record in caplog.records
            if getattr(record, "his_mon_code", None)
            is WebhookDeliveryErrorCode.HTTP_STATUS
        ]
        assert len(failures) == 1
        failure = failures[0]
        assert failure.http_status == status_code
        assert failure.exc_info is None
        assert failure.getMessage() == f"Webhook delivery failed: HTTP {status_code}"
        assert all(
            secret_message not in record.getMessage()
            for record in caplog.records
        )
        assert secret_message not in caplog.text

    @pytest.mark.parametrize(
        ("transport_error", "error_type"),
        [
            (requests.ConnectionError("timeout"), requests.ConnectionError),
            (OSError("missing CA bundle"), OSError),
        ],
        ids=["requests", "os-error"],
    )
    def test_transport_failure_is_logged_and_drain_completes(
        self,
        transport_error,
        error_type,
        caplog,
    ):
        secret_messages = [
            "transport failure secret one",
            "transport failure secret two",
            "transport failure secret three",
        ]
        with caplog.at_level(logging.ERROR, logger="his_mon.webhook"):
            with patch(
                "his_mon.webhook.requests.post",
                side_effect=transport_error,
            ) as mock_post:
                assert init_webhook("https://example.com/hook") is None
                for secret_message in secret_messages:
                    send_alert(secret_message)
                shutdown_webhook(drain=True)

        failures = [
            record
            for record in caplog.records
            if getattr(record, "his_mon_code", None)
            is WebhookDeliveryErrorCode.TRANSPORT_ERROR
        ]
        assert mock_post.call_count == len(secret_messages)
        assert len(failures) == len(secret_messages)
        assert all(failure.error_type == error_type.__name__ for failure in failures)
        assert all(failure.exc_info is None for failure in failures)
        assert all(
            secret_message not in record.getMessage()
            for record in caplog.records
            for secret_message in secret_messages
        )
        assert all(secret_message not in caplog.text for secret_message in secret_messages)

    def test_transport_exception_text_cannot_reinject_alert_body(self, caplog):
        secret_message = "transport response echo secret"
        transport_error = requests.exceptions.ChunkedEncodingError(secret_message)

        with caplog.at_level(logging.ERROR, logger="his_mon.webhook"):
            with patch(
                "his_mon.webhook.requests.post",
                side_effect=transport_error,
            ):
                assert init_webhook("https://example.com/hook") is None
                send_alert(secret_message)
                shutdown_webhook(drain=True)

        failures = [
            record
            for record in caplog.records
            if getattr(record, "his_mon_code", None)
            is WebhookDeliveryErrorCode.TRANSPORT_ERROR
        ]
        assert len(failures) == 1
        assert failures[0].error_type == "ChunkedEncodingError"
        assert failures[0].exc_info is None
        assert secret_message not in caplog.text

    def test_non_draining_shutdown_discards_queued_alert_without_leaking_it(
        self,
        caplog,
    ):
        first_delivery_started = threading.Event()
        release_first_delivery = threading.Event()
        secret_message = "queued alert sk_live_789 should not be logged"
        overlap_message = "overlapping worker must not deliver"
        replacement_message = "replacement worker delivery"

        def blocking_post(url, json, timeout, allow_redirects):
            assert allow_redirects is False
            first_delivery_started.set()
            release_first_delivery.wait(1.0)
            return _make_response(204, "No Content")

        with caplog.at_level(logging.DEBUG, logger="his_mon.webhook"):
            with patch(
                "his_mon.webhook.requests.post",
                side_effect=blocking_post,
            ) as mock_post:
                assert init_webhook("https://example.com/hook") is None
                send_alert("in-flight alert")
                assert first_delivery_started.wait(1.0)
                send_alert(secret_message)

                shutdown_webhook(drain=False, timeout=0.01)
                reloaded_webhook = importlib.reload(webhook_module)
                assert reloaded_webhook.init_webhook("https://example.com/hook") is None
                reloaded_webhook.send_alert(overlap_message)

                release_first_delivery.set()
                reloaded_webhook.shutdown_webhook(timeout=1.0)

                assert reloaded_webhook.init_webhook("https://example.com/hook") is None
                reloaded_webhook.send_alert(replacement_message)
                reloaded_webhook.shutdown_webhook(drain=True)

        assert [call.kwargs["json"] for call in mock_post.call_args_list] == [
            {"text": "in-flight alert"},
            {"text": replacement_message},
        ]
        assert all(
            secret_message not in record.getMessage()
            for record in caplog.records
        )
        assert all(
            overlap_message not in record.getMessage()
            for record in caplog.records
        )


class TestSendAlertNoop:
    def test_uninitialized_webhook_does_not_log_alert_payload(self, caplog):
        secret_message = "service token sk_live_123 should not be logged"

        with caplog.at_level(logging.DEBUG, logger="his_mon.webhook"):
            send_alert(secret_message)

        assert all(
            secret_message not in record.getMessage()
            for record in caplog.records
        )

    def test_send_after_shutdown_is_a_silent_noop(self, caplog):
        secret_message = "service token sk_live_456 should not be logged"
        assert init_webhook("https://example.com/hook") is None
        assert shutdown_webhook() is None

        with caplog.at_level(logging.DEBUG, logger="his_mon.webhook"):
            send_alert(secret_message)

        assert all(
            secret_message not in record.getMessage()
            for record in caplog.records
        )


def test_reload_reuses_the_process_wide_manager_and_worker():
    first_delivery_started = threading.Event()
    release_first_delivery = threading.Event()
    second_delivery_started = threading.Event()

    def blocking_post(url, json, timeout, allow_redirects):
        assert allow_redirects is False
        if json["text"] == "before reload":
            first_delivery_started.set()
            release_first_delivery.wait(1.0)
        else:
            second_delivery_started.set()
        return _make_response(204, "No Content")

    with patch(
        "his_mon.webhook.requests.post",
        side_effect=blocking_post,
    ) as mock_post:
        assert webhook_module.init_webhook("https://example.com/hook") is None
        webhook_module.send_alert("before reload")
        assert first_delivery_started.wait(1.0)

        reloaded_webhook = importlib.reload(webhook_module)
        assert reloaded_webhook.init_webhook("https://example.com/hook") is None
        reloaded_webhook.send_alert("after reload")
        assert not second_delivery_started.wait(0.05)

        release_first_delivery.set()
        reloaded_webhook.shutdown_webhook(drain=True)

    assert [call.kwargs["json"] for call in mock_post.call_args_list] == [
        {"text": "before reload"},
        {"text": "after reload"},
    ]


def test_draining_shutdown_waits_until_in_flight_delivery_finishes():
    delivery_started = threading.Event()
    release_delivery = threading.Event()
    shutdown_finished = threading.Event()
    replacement_message = "replacement delivery"

    def blocking_post(url, json, timeout, allow_redirects):
        assert allow_redirects is False
        delivery_started.set()
        release_delivery.wait(1.0)
        return _make_response(204, "No Content")

    with patch(
        "his_mon.webhook.requests.post",
        side_effect=blocking_post,
    ) as mock_post:
        assert webhook_module.init_webhook("https://example.com/hook") is None
        webhook_module.send_alert("blocked delivery")
        assert delivery_started.wait(1.0)

        def shutdown() -> None:
            assert webhook_module.shutdown_webhook(
                drain=True,
                timeout=0.01,
            ) is None
            shutdown_finished.set()

        shutdown_thread = threading.Thread(target=shutdown, daemon=True)
        shutdown_thread.start()
        assert not shutdown_finished.wait(0.05)
        reloaded_webhook = importlib.reload(webhook_module)
        assert reloaded_webhook.init_webhook("https://example.com/hook") is None
        reloaded_webhook.send_alert("ignored during drain")

        release_delivery.set()
        shutdown_thread.join(1.0)
        assert not shutdown_thread.is_alive()
        assert shutdown_finished.is_set()

        assert reloaded_webhook.init_webhook("https://example.com/hook") is None
        reloaded_webhook.send_alert(replacement_message)
        reloaded_webhook.shutdown_webhook(drain=True)

    assert [call.kwargs["json"] for call in mock_post.call_args_list] == [
        {"text": "blocked delivery"},
        {"text": replacement_message},
    ]


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
@pytest.mark.filterwarnings(
    r"ignore:This process .* is multi-threaded, use of fork\(\) may lead to deadlocks in the child:DeprecationWarning"
)
def test_pre_fork_configuration_creates_a_child_local_worker():
    delivery_reader, delivery_writer = os.pipe()

    def post_from_child(url, json, timeout, allow_redirects):
        assert allow_redirects is False
        os.write(delivery_writer, json["text"].encode() + b"\n")
        return _make_response(204, "No Content")

    try:
        with patch("his_mon.webhook.requests.post", new=post_from_child):
            assert init_webhook("https://example.com/hook") is None

            child_pid = os.fork()
            if child_pid == 0:
                os.close(delivery_reader)
                try:
                    send_alert("from child")
                    shutdown_webhook(drain=True)
                except BaseException:
                    os._exit(1)
                os._exit(0)

            readable, _, _ = select.select([delivery_reader], [], [], 2.0)
            child_payload = os.read(delivery_reader, 64) if readable else b""
            _, child_status = os.waitpid(child_pid, 0)

            assert os.waitstatus_to_exitcode(child_status) == 0
            assert child_payload == b"from child\n"

            send_alert("from parent")
            shutdown_webhook(drain=True)
            readable, _, _ = select.select([delivery_reader], [], [], 2.0)
            parent_payload = os.read(delivery_reader, 64) if readable else b""
            assert parent_payload == b"from parent\n"
    finally:
        os.close(delivery_writer)
        os.close(delivery_reader)
        shutdown_webhook()
