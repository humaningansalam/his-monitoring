# his-monitoring

`his-monitoring` is a small in-process observability library for Python 3.11 and later. It configures logging, registers Prometheus collectors, samples process resources, and delivers webhook alerts without owning an HTTP server or monitoring backend.

## Install

From a local checkout, run these commands from the repository root (the directory containing `pyproject.toml`):

```bash
pip install .
```

Loki support is optional:

```bash
pip install ".[loki]"
```

## Usage

```python
import os

from prometheus_client import start_http_server

from his_mon import (
    BaseMetrics,
    ResourceMonitor,
    init_webhook,
    send_alert,
    setup_logging,
    shutdown_webhook,
)

setup_logging(level="INFO", log_file="logs/service.log")
metrics = BaseMetrics("orders_api")
monitor = ResourceMonitor(metrics_obj, interval=5)

start_http_server(8000)
init_webhook(os.getenv("WEBHOOK_URL"))
monitor.start()

try:
    send_alert("orders_api started")
    # Run the service here; keep the monitor active for its lifetime.
finally:
    monitor.stop()
    shutdown_webhook(drain=True)
```

## Public API

### `setup_logging(...)`

Configures the root logger with stdout and optional rotating-file and Loki handlers. Equivalent repeated calls do not add duplicate handlers. `log_file` accepts strings and path-like objects.

### `BaseMetrics(app_name, *, registry=None)`

Creates or reuses these collectors in the selected `CollectorRegistry`:

- `<app_name>_cpu_usage_percent`
- `<app_name>_ram_usage_mb`
- `<app_name>_errors_total{type=...}`

Use a Prometheus-compatible prefix such as `orders_api`. RAM values are measured in MiB (despite the retained `_mb` suffix). A conflicting collector type or label set raises a structured exception. Expose the same registry through your service if you use a custom registry.

### `ResourceMonitor(metrics_obj, interval=5)`

Samples the current process on a daemon thread. Use `start()`, `stop()`, and `is_running`. The supplied object may expose `cpu_usage` and `ram_usage` gauges with a `set(float)` method; missing gauges are ignored.

### Webhook helpers

`init_webhook(url)` starts one process-wide delivery worker. `send_alert(message)` queues a message and is a no-op when no webhook is configured. `shutdown_webhook(drain=False, timeout=2)` stops the worker; `drain=True` waits until already queued alerts have been handled.

The drain option waits without a join timeout; a non-draining stop may leave an in-flight delivery to finish. Delivery uses a five-second request timeout, does not follow redirects, and logs failure metadata without logging alert contents.

## Boundaries

The library does not provide a `/metrics` route, persistence, dashboards, webhook authentication, retries, or deployment configuration. Those remain application or platform responsibilities.

Process forks and module reloads receive dedicated lifecycle handling. Keep `stop()` and `shutdown_webhook()` in application teardown paths even though worker threads are daemons.

## Development

```bash
./scripts/verify
```

The script syncs the locked development environment, runs all quality gates, and builds both distributions.

See [CONTRIBUTING.md](CONTRIBUTING.md) for change expectations and [CHANGELOG.md](CHANGELOG.md) for release history.
