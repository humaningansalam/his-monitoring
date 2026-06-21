"""Focused tests for BaseMetrics registration and stability."""

from prometheus_client import CollectorRegistry, Gauge, Counter
import pytest
from his_mon.metrics import BaseMetrics


def test_repeated_construction_in_same_registry():
    """Cover repeated BaseMetrics('same_app', registry=registry) construction does not raise and reuses compatible collectors."""
    registry = CollectorRegistry()

    metrics1 = BaseMetrics("same_app", registry=registry)
    metrics2 = BaseMetrics("same_app", registry=registry)

    assert metrics1.cpu_usage is metrics2.cpu_usage
    assert metrics1.ram_usage is metrics2.ram_usage
    assert metrics1.error_count is metrics2.error_count

    # Verify the collectors are indeed registered in the custom registry
    assert registry._names_to_collectors.get("same_app_cpu_usage_percent") is metrics1.cpu_usage
    assert registry._names_to_collectors.get("same_app_ram_usage_mb") is metrics1.ram_usage
    assert registry._names_to_collectors.get("same_app_errors_total") is metrics1.error_count


def test_registry_isolation_for_same_app():
    """Cover two different CollectorRegistry() instances are isolated for the same app_name."""
    registry1 = CollectorRegistry()
    registry2 = CollectorRegistry()

    metrics1 = BaseMetrics("isolated_app", registry=registry1)
    metrics2 = BaseMetrics("isolated_app", registry=registry2)

    assert metrics1.cpu_usage is not metrics2.cpu_usage
    assert metrics1.ram_usage is not metrics2.ram_usage
    assert metrics1.error_count is not metrics2.error_count

    assert registry1._names_to_collectors.get("isolated_app_cpu_usage_percent") is metrics1.cpu_usage
    assert registry2._names_to_collectors.get("isolated_app_cpu_usage_percent") is metrics2.cpu_usage


def test_inc_error_increments_expected_labeled_counter():
    """Cover inc_error() increments the expected labeled counter."""
    registry = CollectorRegistry()
    metrics = BaseMetrics("test_inc", registry=registry)

    # Initial value
    assert metrics.error_count.labels(type="api_error")._value.get() == 0

    metrics.inc_error("api_error")
    assert metrics.error_count.labels(type="api_error")._value.get() == 1

    metrics.inc_error()  # default type is 'unknown'
    assert metrics.error_count.labels(type="unknown")._value.get() == 1


def test_metric_names_and_labels_remain_unchanged():
    """Cover metric names and the `type` label remain unchanged."""
    registry = CollectorRegistry()
    metrics = BaseMetrics("my_app", registry=registry)

    # Verify names
    assert metrics.cpu_usage._name == "my_app_cpu_usage_percent"
    assert metrics.ram_usage._name == "my_app_ram_usage_mb"
    assert metrics.error_count._name == "my_app_errors"

    # Verify labels (only error_count has 'type' label)
    assert metrics.error_count._labelnames == ("type",)
