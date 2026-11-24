# Contributing

## Set up

Use Python 3.11 or later. The verification entry point installs the locked development environment before running checks.

## Make changes

Keep the public surface in `his_mon/__init__.py` small. Preserve these contracts unless the change explicitly updates documentation and tests:

- logging setup is idempotent across repeated calls, reloads, threads, and forks;
- Prometheus collectors are reused only when type and labels are compatible;
- resource monitoring stops promptly and recovers after a fork;
- webhook failures never expose alert contents in logs;
- optional Loki support is not required for core installation.

Prefer a direct helper or protocol over a new framework-level abstraction. Keep third-party private API access inside one adapter and explain why it is necessary.

## Verify

Run the same checks as CI:

```bash
./scripts/verify
```

A pull request should explain the reason for the change, the observable behavior affected, and the commands actually run.
