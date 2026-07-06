# Changelog

## 0.1.1

- Core package metadata identifies this release as `0.1.1` and marks the package as `his-monitoring`.
- Runtime requirements are `prometheus-client`, `psutil`, and `requests`.
- Loki support is intentionally optional and available through the `loki` extra (`his-monitoring[loki]`).
- Public API entry points are still exported from `his_mon`:
  - `setup_logging`
  - `BaseMetrics`
  - `ResourceMonitor`
  - `init_webhook`
  - `send_alert`
- No migration is currently required for consumers of the documented public package surface.

### Upgrade path notes

- If you need Loki logging, install with the `loki` extra when upgrading or first installing.
- If you only rely on the documented core logging, metrics, monitoring, and webhook APIs, no migration actions are required when moving onto the documented 0.1.1 surface.
