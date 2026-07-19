"""Focused tests for BaseMetrics registration and stability."""

import threading

from prometheus_client import CollectorRegistry, Counter, Gauge
import pytest
from his_mon.metrics import (
    BaseMetrics,
    MetricConflictCode,
    MetricLabelConflictError,
    MetricTypeConflictError,
)


def _metric_families(registry: CollectorRegistry):
    return {metric.name: metric for metric in registry.collect()}


def test_repeated_construction_in_same_registry():
    """Cover repeated BaseMetrics('same_app', registry=registry) construction does not raise and reuses compatible collectors."""
    registry = CollectorRegistry()

    metrics1 = BaseMetrics("same_app", registry=registry)
    metrics2 = BaseMetrics("same_app", registry=registry)

    assert metrics1.cpu_usage is metrics2.cpu_usage
    assert metrics1.ram_usage is metrics2.ram_usage
    assert metrics1.error_count is metrics2.error_count

    assert set(_metric_families(registry)) == {
        "same_app_cpu_usage_percent",
        "same_app_ram_usage_mb",
        "same_app_errors",
    }


def test_concurrent_construction_reuses_registered_collectors():
    class ConcurrentRegistrationRegistry(CollectorRegistry):
        def __init__(self):
            super().__init__()
            self._first_registration = threading.Barrier(2)
            self._registration_count = 0
            self._registration_count_lock = threading.Lock()

        def register(self, collector):
            with self._registration_count_lock:
                self._registration_count += 1
                synchronize = self._registration_count <= 2
            if synchronize:
                self._first_registration.wait()
            return super().register(collector)

    registry = ConcurrentRegistrationRegistry()
    start = threading.Barrier(3)
    created = []
    errors = []

    def construct_metrics():
        start.wait()
        try:
            created.append(BaseMetrics("concurrent_app", registry=registry))
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=construct_metrics),
        threading.Thread(target=construct_metrics),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(2.0)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(created) == 2
    assert created[0].cpu_usage is created[1].cpu_usage
    assert created[0].ram_usage is created[1].ram_usage
    assert created[0].error_count is created[1].error_count


def test_registry_isolation_for_same_app():
    """Cover two different CollectorRegistry() instances are isolated for the same app_name."""
    registry1 = CollectorRegistry()
    registry2 = CollectorRegistry()

    metrics1 = BaseMetrics("isolated_app", registry=registry1)
    metrics2 = BaseMetrics("isolated_app", registry=registry2)

    assert metrics1.cpu_usage is not metrics2.cpu_usage
    assert metrics1.ram_usage is not metrics2.ram_usage
    assert metrics1.error_count is not metrics2.error_count

    assert set(_metric_families(registry1)) == set(_metric_families(registry2))


def test_registry_must_be_a_prometheus_collector_registry():
    with pytest.raises(TypeError):
        BaseMetrics("invalid_registry", registry=object())


def test_incompatible_existing_counter_labels_fail_fast():
    """Cover BaseMetrics rejects a pre-existing counter with mismatched label names."""
    registry = CollectorRegistry()
    Counter("demo_errors_total", "Total errors", ["other"], registry=registry)

    with pytest.raises(MetricLabelConflictError) as raised:
        BaseMetrics("demo", registry=registry)

    error = raised.value
    assert error.code is MetricConflictCode.LABEL_MISMATCH
    assert error.metric_name == "demo_errors_total"
    assert error.actual_labelnames == ("other",)
    assert error.expected_labelnames == ("type",)


def test_incompatible_existing_collector_type_fails_with_structured_error():
    registry = CollectorRegistry()
    Gauge("demo_errors_total", "Wrong collector type", registry=registry)

    with pytest.raises(MetricTypeConflictError) as raised:
        BaseMetrics("demo", registry=registry)

    error = raised.value
    assert error.code is MetricConflictCode.TYPE_MISMATCH
    assert error.metric_name == "demo_errors_total"
    assert error.expected_type is Counter
    assert error.actual_type is Gauge


def test_inc_error_increments_expected_labeled_counter():
    """Cover inc_error() increments the expected labeled counter."""
    registry = CollectorRegistry()
    metrics = BaseMetrics("test_inc", registry=registry)

    assert registry.get_sample_value(
        "test_inc_errors_total", {"type": "api_error"}
    ) is None

    metrics.inc_error("api_error")
    assert registry.get_sample_value(
        "test_inc_errors_total", {"type": "api_error"}
    ) == 1

    metrics.inc_error()  # default type is 'unknown'
    assert registry.get_sample_value(
        "test_inc_errors_total", {"type": "unknown"}
    ) == 1


def test_metric_names_and_labels_remain_unchanged():
    """Cover metric names and the `type` label remain unchanged."""
    registry = CollectorRegistry()
    metrics = BaseMetrics("my_app", registry=registry)
    metrics.inc_error("contract")

    families = _metric_families(registry)
    assert set(families) == {
        "my_app_cpu_usage_percent",
        "my_app_ram_usage_mb",
        "my_app_errors",
    }
    error_samples = families["my_app_errors"].samples
    assert any(sample.labels == {"type": "contract"} for sample in error_samples)
