from prometheus_client import Gauge, Counter

class BaseMetrics:
    """
    Nase metrics class providing common resource metrics (CPU, RAM, Error)
    """
    def __init__(self, app_name: str):
        self.app_name = app_name
        
        # Common Resource Metrics
        self.cpu_usage = Gauge(f'{app_name}_cpu_usage_percent', 'App CPU usage %')
        self.ram_usage = Gauge(f'{app_name}_ram_usage_mb', 'App RAM usage MB')
        
        # Common Error Counter
        self.error_count = Counter(f'{app_name}_errors_total', 'Total errors', ['type'])

    def inc_error(self, error_type: str = 'unknown'):
        self.error_count.labels(type=error_type).inc()