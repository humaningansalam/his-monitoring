import re
from enum import Enum
from typing import Any

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge

_METRIC_PREFIX_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")


class MetricConflictCode(str, Enum):
    TYPE_MISMATCH = "type_mismatch"
    LABEL_MISMATCH = "label_mismatch"


class MetricTypeConflictError(TypeError):
    code = MetricConflictCode.TYPE_MISMATCH

    def __init__(self, metric_name: str, expected_type: type, actual_type: type):
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
    ):
        self.metric_name = metric_name
        self.expected_labelnames = expected_labelnames
        self.actual_labelnames = actual_labelnames
        super().__init__(
            f"Metric {metric_name} is already registered with label names "
            f"{actual_labelnames!r}; expected {expected_labelnames!r}"
        )


def _validate_metric_prefix(app_name: str) -> None:
    if not isinstance(app_name, str) or not app_name:
        raise ValueError("app_name must be a non-empty string")
    if not _METRIC_PREFIX_RE.fullmatch(app_name):
        raise ValueError(
            f"Invalid app_name for Prometheus metric prefix: {app_name!r}"
        )


def _collector_labelnames(collector: Gauge | Counter) -> tuple[str, ...]:
    return tuple(collector._labelnames)


def _lookup_collector(
    registry: CollectorRegistry,
    name: str,
) -> Gauge | Counter | None:
    with registry._lock:
        return registry._names_to_collectors.get(name)


def _require_compatible_collector(
    collector: Gauge | Counter,
    *,
    name: str,
    collector_type: type,
    labelnames: tuple[str, ...],
) -> Any:
    if not isinstance(collector, collector_type):
        raise MetricTypeConflictError(name, collector_type, type(collector))

    actual_labelnames = _collector_labelnames(collector)
    if actual_labelnames != labelnames:
        raise MetricLabelConflictError(name, labelnames, actual_labelnames)
    return collector


def _get_or_create_collector(
    registry: CollectorRegistry,
    name: str,
    collector_type: type,
    documentation: str,
    *,
    labelnames: tuple[str, ...] = (),
) -> Any:
    collector = _lookup_collector(registry, name)
    if collector is not None:
        return _require_compatible_collector(
            collector,
            name=name,
            collector_type=collector_type,
            labelnames=labelnames,
        )

    try:
        return collector_type(
            name,
            documentation,
            registry=registry,
            labelnames=labelnames,
        )
    except ValueError:
        # Another caller may have registered the same collector after our
        # lookup. Reconcile against the registry once through the same path.
        collector = _lookup_collector(registry, name)
        if collector is None:
            raise
        return _require_compatible_collector(
            collector,
            name=name,
            collector_type=collector_type,
            labelnames=labelnames,
        )


class BaseMetrics:
    """
    Base metrics class providing common resource metrics (CPU, RAM, Error)
    """
    def __init__(
        self,
        app_name: str,
        *,
        registry: CollectorRegistry | None = None,
    ):
        _validate_metric_prefix(app_name)
        if registry is None:
            registry = REGISTRY
        if not isinstance(registry, CollectorRegistry):
            raise TypeError("registry must be a prometheus_client CollectorRegistry")
        self.app_name = app_name

        # Common Resource Metrics
        self.cpu_usage = _get_or_create_collector(
            registry, f"{app_name}_cpu_usage_percent", Gauge, "App CPU usage %"
        )
        self.ram_usage = _get_or_create_collector(
            registry, f"{app_name}_ram_usage_mb", Gauge, "App RAM usage MB"
        )
        # Common Error Counter
        self.error_count = _get_or_create_collector(
            registry,
            f"{app_name}_errors_total",
            Counter,
            "Total errors",
            labelnames=("type",),
        )

    def inc_error(self, error_type: str = 'unknown'):
        self.error_count.labels(type=error_type).inc()
