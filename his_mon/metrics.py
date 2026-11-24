from __future__ import annotations

import re
from collections.abc import Callable
from enum import Enum
from typing import TypeVar, cast

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge
from prometheus_client.registry import Collector

_METRIC_PREFIX_PATTERN = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
MetricCollector = Gauge | Counter
CollectorT = TypeVar("CollectorT", Gauge, Counter)


class MetricConflictCode(str, Enum):  # noqa: UP042 - preserve existing string formatting.
    TYPE_MISMATCH = "type_mismatch"
    LABEL_MISMATCH = "label_mismatch"


class MetricTypeConflictError(TypeError):
    code = MetricConflictCode.TYPE_MISMATCH

    def __init__(
        self,
        metric_name: str,
        expected_type: type[Gauge] | type[Counter],
        actual_type: type[object],
    ) -> None:
        self.metric_name = metric_name
        self.expected_type = expected_type
        self.actual_type = actual_type
        super().__init__(
            f"Metric {metric_name} is already registered as {actual_type.__name__}; "
            f"expected {expected_type.__name__}"
        )


class MetricLabelConflictError(ValueError):
    code = MetricConflictCode.LABEL_MISMATCH

    def __init__(
        self,
        metric_name: str,
        expected_labelnames: tuple[str, ...],
        actual_labelnames: tuple[str, ...],
    ) -> None:
        self.metric_name = metric_name
        self.expected_labelnames = expected_labelnames
        self.actual_labelnames = actual_labelnames
        super().__init__(
            f"Metric {metric_name} is already registered with label names "
            f"{actual_labelnames!r}; expected {expected_labelnames!r}"
        )


def _validate_metric_prefix(app_name: object) -> str:
    if not isinstance(app_name, str) or not app_name:
        raise ValueError("app_name must be a non-empty string")
    if not _METRIC_PREFIX_PATTERN.fullmatch(app_name):
        raise ValueError(
            f"Invalid app_name for Prometheus metric prefix: {app_name!r}. "
            "Use a name that starts with a letter, '_' or ':' and then contains "
            "only letters, digits, '_' or ':' (for example, 'orders_api')."
        )
    return app_name


def _resolve_registry(registry: object | None) -> CollectorRegistry:
    if registry is None:
        return REGISTRY
    if not isinstance(registry, CollectorRegistry):
        raise TypeError("registry must be a prometheus_client CollectorRegistry")
    return registry


def _collector_label_names(collector: MetricCollector) -> tuple[str, ...]:
    return tuple(collector._labelnames)


def _lookup_collector(
    registry: CollectorRegistry,
    metric_name: str,
) -> Collector | None:
    # prometheus_client has no public lookup API; keep private access in one adapter.
    with registry._lock:
        return registry._names_to_collectors.get(metric_name)


def _require_compatible_collector(
    collector: Collector,
    *,
    metric_name: str,
    expected_type: type[CollectorT],
    expected_label_names: tuple[str, ...],
) -> CollectorT:
    if not isinstance(collector, expected_type):
        raise MetricTypeConflictError(metric_name, expected_type, type(collector))

    typed_collector = cast(MetricCollector, collector)
    actual_label_names = _collector_label_names(typed_collector)
    if actual_label_names != expected_label_names:
        raise MetricLabelConflictError(
            metric_name,
            expected_label_names,
            actual_label_names,
        )
    return cast(CollectorT, typed_collector)


def _get_or_create_collector(
    registry: CollectorRegistry,
    metric_name: str,
    expected_type: type[CollectorT],
    factory: Callable[[], CollectorT],
    *,
    label_names: tuple[str, ...] = (),
) -> CollectorT:
    existing_collector = _lookup_collector(registry, metric_name)
    if existing_collector is not None:
        return _require_compatible_collector(
            existing_collector,
            metric_name=metric_name,
            expected_type=expected_type,
            expected_label_names=label_names,
        )

    try:
        return factory()
    except ValueError:
        # A concurrent caller may have registered the collector after the lookup.
        existing_collector = _lookup_collector(registry, metric_name)
        if existing_collector is None:
            raise
        return _require_compatible_collector(
            existing_collector,
            metric_name=metric_name,
            expected_type=expected_type,
            expected_label_names=label_names,
        )


class BaseMetrics:
    """Create or reuse CPU, RAM, and error collectors for one application."""

    def __init__(
        self,
        app_name: str,
        *,
        registry: CollectorRegistry | None = None,
    ) -> None:
        metric_prefix = _validate_metric_prefix(app_name)
        selected_registry = _resolve_registry(registry)

        self.app_name = metric_prefix
        self.registry = selected_registry

        cpu_metric_name = f"{metric_prefix}_cpu_usage_percent"
        self.cpu_usage: Gauge = _get_or_create_collector(
            selected_registry,
            cpu_metric_name,
            Gauge,
            lambda: Gauge(
                cpu_metric_name,
                "Process CPU usage percentage",
                registry=selected_registry,
            ),
        )

        ram_metric_name = f"{metric_prefix}_ram_usage_mb"
        self.ram_usage: Gauge = _get_or_create_collector(
            selected_registry,
            ram_metric_name,
            Gauge,
            lambda: Gauge(
                ram_metric_name,
                "Process resident memory in mebibytes",
                registry=selected_registry,
            ),
        )

        error_metric_name = f"{metric_prefix}_errors_total"
        error_label_names = ("type",)
        self.error_count: Counter = _get_or_create_collector(
            selected_registry,
            error_metric_name,
            Counter,
            lambda: Counter(
                error_metric_name,
                "Application errors",
                labelnames=error_label_names,
                registry=selected_registry,
            ),
            label_names=error_label_names,
        )

    def inc_error(self, error_type: str = "unknown") -> None:
        self.error_count.labels(type=error_type).inc()
