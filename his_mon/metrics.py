from prometheus_client import REGISTRY, Gauge, Counter

class BaseMetrics:
    """
    Base metrics class providing common resource metrics (CPU, RAM, Error)
    """
    def __init__(self, app_name: str, *, registry=None):
        if registry is None:
            registry = REGISTRY
        self.app_name = app_name
        def _get_or_create(name, cls, *args, **kwargs):
            lock = getattr(registry, '_lock', None)
            if lock:
                with lock:
                    collector = registry._names_to_collectors.get(name)
            else:
                collector = registry._names_to_collectors.get(name)
            if collector is not None:
                if not isinstance(collector, cls):
                    raise TypeError(f"Metric {name} is already registered but is not a {cls.__name__}")
                return collector

            try:
                return cls(name, *args, registry=registry, **kwargs)
            except ValueError:
                if lock:
                    with lock:
                        collector = registry._names_to_collectors.get(name)
                else:
                    collector = registry._names_to_collectors.get(name)
                if collector is not None:
                    if not isinstance(collector, cls):
                        raise TypeError(f"Metric {name} is already registered but is not a {cls.__name__}")
                    return collector
                raise

        # Common Resource Metrics
        self.cpu_usage = _get_or_create(f'{app_name}_cpu_usage_percent', Gauge, 'App CPU usage %')
        self.ram_usage = _get_or_create(f'{app_name}_ram_usage_mb', Gauge, 'App RAM usage MB')
        # Common Error Counter
        self.error_count = _get_or_create(f'{app_name}_errors_total', Counter, 'Total errors', ['type'])

    def inc_error(self, error_type: str = 'unknown'):
        self.error_count.labels(type=error_type).inc()