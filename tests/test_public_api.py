"""Smoke tests for the public his_mon package API."""

from his_mon import BaseMetrics, ResourceMonitor, init_webhook, send_alert, setup_logging
import his_mon


EXPECTED_PUBLIC_API = [
    "setup_logging",
    "BaseMetrics",
    "ResourceMonitor",
    "init_webhook",
    "send_alert",
]


def test_public_api_exports_match_expected_names():
    assert his_mon.__all__ == EXPECTED_PUBLIC_API


def test_public_api_imports_work():
    assert setup_logging is his_mon.setup_logging
    assert BaseMetrics is his_mon.BaseMetrics
    assert ResourceMonitor is his_mon.ResourceMonitor
    assert init_webhook is his_mon.init_webhook
    assert send_alert is his_mon.send_alert
