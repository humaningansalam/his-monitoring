# Repository guide

Read `README.md`, `pyproject.toml`, and the module being changed before editing.

- `his_mon/__init__.py` defines the supported public imports.
- `his_mon/_runtime.py` owns only process-wide locks and webhook lifecycle state.
- `logger.py`, `metrics.py`, `monitor.py`, and `webhook.py` own their respective behavior and tests.
- Keep failure logs free of alert contents and webhook credentials.
- Preserve reload, fork, concurrency, and repeated-setup behavior covered by the tests.
- Do not add generic templates, speculative abstractions, or duplicate documentation.

Before finishing, run:

```bash
./scripts/verify
```
