"""BaseMetrics registration and compatibility tests."""

import threading

import pytest
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge

from his_mon.metrics import (
    BaseMetrics,
    MetricConflictCode,
    MetricLabelConflictError,
    MetricTypeConflictError,
)


def _metric_families(registry: CollectorRegistry):
    return {metric.name: metric for metric in registry.collect()}


def test_repeated_construction_in_same_registry():
    registry = CollectorRegistry()

    first_metrics = BaseMetrics("same_app", registry=registry)
    second_metrics = BaseMetrics("same_app", registry=registry)

    assert first_metrics.cpu_usage is second_metrics.cpu_usage
    assert first_metrics.ram_usage is second_metrics.ram_usage
    assert first_metrics.error_count is second_metrics.error_count

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
    created_metrics = []
    worker_errors = []

    def build_metrics():
        start.wait()
        try:
            created_metrics.append(BaseMetrics("concurrent_app", registry=registry))
        except Exception as exc:  # noqa: BLE001 - assert every thread failure.
            worker_errors.append(exc)

    threads = [
        threading.Thread(target=build_metrics),
        threading.Thread(target=build_metrics),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(2.0)

    assert all(not thread.is_alive() for thread in threads)
    assert worker_errors == []
    assert len(created_metrics) == 2
    assert created_metrics[0].cpu_usage is created_metrics[1].cpu_usage
    assert created_metrics[0].ram_usage is created_metrics[1].ram_usage
    assert created_metrics[0].error_count is created_metrics[1].error_count


def test_registry_isolation_for_same_app():
    first_registry = CollectorRegistry()
    second_registry = CollectorRegistry()

    first_metrics = BaseMetrics("isolated_app", registry=first_registry)
    second_metrics = BaseMetrics("isolated_app", registry=second_registry)

    assert first_metrics.cpu_usage is not second_metrics.cpu_usage
    assert first_metrics.ram_usage is not second_metrics.ram_usage
    assert first_metrics.error_count is not second_metrics.error_count

    assert set(_metric_families(first_registry)) == set(
        _metric_families(second_registry)
    )


def test_registry_must_be_a_prometheus_collector_registry():
    with pytest.raises(TypeError):
        BaseMetrics("invalid_registry", registry=object())


@pytest.mark.parametrize("app_name", [None, "", "1invalid", "contains space"])
def test_invalid_metric_prefix_is_rejected(app_name):
    with pytest.raises(ValueError):
        BaseMetrics(app_name)


def test_default_registry_is_recorded_on_metrics_instance():
    metrics = BaseMetrics("default_registry_contract")
    assert metrics.registry is REGISTRY


def test_incompatible_existing_counter_labels_fail_fast():
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
    registry = CollectorRegistry()
    metrics = BaseMetrics("test_inc", registry=registry)

    assert (
        registry.get_sample_value("test_inc_errors_total", {"type": "api_error"})
        is None
    )

    metrics.inc_error("api_error")
    assert (
        registry.get_sample_value("test_inc_errors_total", {"type": "api_error"}) == 1
    )

    metrics.inc_error()  # default type is 'unknown'
    assert registry.get_sample_value("test_inc_errors_total", {"type": "unknown"}) == 1


def test_metric_names_and_labels_remain_unchanged():
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
