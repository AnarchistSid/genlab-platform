# Dependency Locking

This repository now uses a two-file strategy for reproducible installs:

- `requirements.txt`: human-maintained dependency intent (ranges/minimums).
- `constraints.txt`: pinned versions used to constrain installs in CI and ops.
- `requirements.lock.txt`: pinned direct dependency snapshot used to refresh `constraints.txt`.

## Install

```bash
pip install -r requirements.txt -c constraints.txt
```

## Refresh pins

1. Update `requirements.txt` as needed.
2. Regenerate `requirements.lock.txt` with pinned versions.
3. Copy/sync `requirements.lock.txt` into `constraints.txt`.
4. Run `pip check`, `ruff check`, and `pytest` before merging.

