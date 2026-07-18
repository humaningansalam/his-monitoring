# Changelog

## 0.2.0 - 2026-07-19

### Changed

- Completed the documented logging, metrics, resource-monitoring, and webhook lifecycle for Python 3.11 and later.
- Kept equivalent stdout, file, and Loki logging setup idempotent across repeated calls, reloads, concurrency, and process forks.
- Made metric collector reuse explicit across default and custom registries, with typed failures for incompatible collectors.
- Made resource monitoring recover from sampling errors and process forks while rejecting intervals the platform cannot wait for safely.

### Fixed

- Prevented webhook shutdown from dropping queue accounting, following redirects, or exposing alert content through HTTP and transport failure logs.
- Prevented inherited Loki listeners from silently losing child-process logs and snapshot caller-owned tag mappings during setup.

## 0.1.2

- Hardened logging, metrics, monitor, and webhook helpers.
- Moved `python-logging-loki` from the core runtime dependencies to the optional `loki` extra.
- Removed obsolete product documentation and feedback scratch file.
- Public API remains available from `his_mon` with the documented surface.

### Upgrade path notes

- Loki users installing from this source checkout must use `pip install ".[loki]"`.
- Core users do not need a migration; the documented logging, metrics, monitoring, and webhook APIs remain available without the Loki dependency.

## 0.1.1

- Core package metadata identifies this release as `0.1.1` and marks the package as `his-monitoring`.
- Runtime requirements are `prometheus-client`, `psutil`, `python-logging-loki`, and `requests`.
- Public API entry points are still exported from `his_mon`:
  - `setup_logging`
  - `BaseMetrics`
  - `ResourceMonitor`
  - `init_webhook`
  - `send_alert`
- No migration is currently required for consumers of the documented public package surface.
