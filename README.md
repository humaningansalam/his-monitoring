# his-monitoring

`his-monitoring` provides a small set of helpers for service logging, process metrics, resource monitoring, and webhook-based alerts.

## Installation

The package targets Python 3.11+ and depends on:

- `prometheus-client` for metrics
- `psutil` for process resource sampling
- `requests` for webhook delivery

The package is currently distributed from its source repository rather than a
public package index. From a local checkout, run this from the `repos/`
directory; pip resolves the declared runtime dependencies from `pyproject.toml`:

```bash
pip install .
```

Install the `loki` extra only when Loki logging support is needed:

```bash
pip install "his-monitoring[loki]"
```

## Public API

The package exports these entry points from `his_mon`:

- `setup_logging(...)`: configure root logging with stdout, optional rotating file output, and optional Loki support.
- `BaseMetrics(app_name, *, registry=None)`: create Prometheus gauges for CPU and RAM usage plus an error counter named from `app_name`.
- `ResourceMonitor(metrics_obj, interval=5)`: start a background sampler that updates `cpu_usage` and `ram_usage` on a metrics object.
- `init_webhook(url)`: initialize the webhook sender once for a destination URL.
- `send_alert(message)`: queue a webhook message if a webhook manager has been initialized.
- `shutdown_webhook(...)`: stop the process-wide webhook worker, optionally after draining queued alerts.

## Minimal usage

```python
from his_mon import BaseMetrics, ResourceMonitor, init_webhook, send_alert, setup_logging

setup_logging(level="INFO", log_file="app.log")
metrics = BaseMetrics("my_app")
monitor = ResourceMonitor(metrics, interval=10)

init_webhook("https://example.invalid/webhook")
monitor.start()
send_alert("service started")
```

## Verification

When evaluating upgrade impact for a version, check `CHANGELOG.md` first for version-to-version notes and compatibility guidance.

Run the package tests and build from the `repos/` directory after activating the project environment:

```bash
uv run python -m pytest tests -v
uv build
```

## Operational notes

- `ResourceMonitor` samples the current Python process and updates metrics in a daemon thread; stop it explicitly when shutting down.
- `setup_logging` is idempotent for equivalent handlers and supports stdout, rotating file output, and Loki when `python-logging-loki` is available.
- `init_webhook` keeps a single process-wide webhook manager; `send_alert` is a no-op until initialization.
- `BaseMetrics` registers Prometheus collectors using the provided app name as a prefix, so reuse the same prefix consistently within one registry.
- The webhook sender posts JSON payloads with a `text` field and reports HTTP or transport failures through the logger.
