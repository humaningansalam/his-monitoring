# his-monitoring PRD

## Product Goal

`his-monitoring` is a lightweight Python package for service observability helpers. It gives service code a stable public API for logging setup, Prometheus process metrics, background resource sampling, and webhook alert delivery.

## Target Users

- Python service developers who need process-local observability helpers without adopting a full framework.
- Small service teams that already operate Prometheus scraping, webhook alert endpoints, or Loki-compatible log collection.

## Success Criteria

- A developer can add the package to a Python 3.11+ service and use the documented public API without importing private modules.
- Repeated setup calls are safe in test suites, reload workflows, and service bootstrap paths.
- Core installation excludes optional Loki dependencies unless the `loki` extra is requested.
- Alert no-op and failure paths do not expose alert message bodies unless a configured webhook endpoint receives the intended payload.

## Product Boundaries

- The package is a library, not a hosted service or CLI.
- The package targets Python 3.11+.
- Runtime dependencies are declared in `pyproject.toml`.
- The public package API is exported from `his_mon`.
- Prometheus metric exposure is the caller's responsibility; this package creates collectors and updates values, but does not start a `/metrics` server.
- Authentication, billing, deployment orchestration, dashboards, and persistence are out of scope.

## Public API Requirements

### Logging

- `setup_logging(...)` configures the root logger with stdout output.
- Repeated `setup_logging(...)` calls must not add duplicate equivalent stdout handlers.
- When `log_file` is provided, logging uses a rotating file handler and creates parent directories when possible.
- Repeated calls with the same `log_file` must not add duplicate equivalent file handlers.
- Invalid file paths must log a warning and must not raise to the caller.
- When Loki support is installed and `loki_url` is provided, repeated equivalent Loki configuration must not add duplicate Loki handlers.
- Loki support is optional and installed through the package's `loki` extra.

### Metrics

- `BaseMetrics(app_name, *, registry=None)` creates or reuses these collectors in the selected Prometheus registry:
  - `<app_name>_cpu_usage_percent`
  - `<app_name>_ram_usage_mb`
  - `<app_name>_errors_total` with label `type`
- Repeated construction for the same `app_name` in the same registry must reuse compatible collectors.
- Different registries must remain isolated.
- `inc_error(error_type="unknown")` increments the `type`-labeled error counter.

### Resource Monitoring

- `ResourceMonitor(metrics_obj, interval=5)` samples the current Python process in a daemon thread.
- `interval` must be greater than zero.
- `start()` starts one background thread and repeated calls while running must not create another one.
- `stop()` must return promptly and stop the background thread.
- The monitor updates `cpu_usage` and `ram_usage` when those attributes exist.
- Missing metric attributes must not crash the monitor thread.

### Webhook Alerts

- `init_webhook(url)` initializes one process-wide webhook manager when a non-empty URL is provided.
- `send_alert(message)` queues a JSON payload `{"text": message}` only when a webhook manager is initialized.
- When no webhook manager is initialized, `send_alert(message)` is a no-op and must not write the alert message body to logs.
- Webhook delivery uses a five-second HTTP timeout.
- Non-2xx responses and transport errors must be logged as delivery failures.
- Queue accounting must complete for successful and failed deliveries so callers waiting on queue drain do not hang.

## Verification Requirements

- `uv run python -m pytest tests -v` passes.
- `uv build` succeeds.
- A smoke test can import `his_mon`, configure logging, create metrics with an isolated registry, start and stop a monitor, and call `send_alert(...)` without a configured webhook.
- Packaging must include the `his_mon` modules and exclude test/build artifacts from the built distribution.
