"""Smoke tests for the public his_mon package API."""

import his_mon
from his_mon import (
    BaseMetrics,
    ResourceMonitor,
    init_webhook,
    send_alert,
    setup_logging,
    shutdown_webhook,
)


EXPECTED_PUBLIC_API = [
    "setup_logging",
    "BaseMetrics",
    "ResourceMonitor",
    "init_webhook",
    "send_alert",
    "shutdown_webhook",
]


def test_public_api_exports_match_expected_names():
    assert his_mon.__all__ == EXPECTED_PUBLIC_API


def test_public_api_imports_work():
    assert setup_logging is his_mon.setup_logging
    assert BaseMetrics is his_mon.BaseMetrics
    assert ResourceMonitor is his_mon.ResourceMonitor
    assert init_webhook is his_mon.init_webhook
    assert send_alert is his_mon.send_alert
    assert shutdown_webhook is his_mon.shutdown_webhook
