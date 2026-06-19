# Migrating platform clients to BasePlatformClient

**Status**: In progress. Phase 0 (foundation) merged in PR for P2 — this doc.
Per-client migrations are tracked as separate follow-up PRs.

## Why

Today (2026-06-19) the 5 main platform clients each independently re-implement
the same infrastructure:

| Client | LOC | Token resolution | Retry envelope | Error classification | Logging |
|---|---|---|---|---|---|
| `instagram.py` | 580 | ad-hoc | ad-hoc | ad-hoc | ad-hoc |
| `facebook.py` | 597 | ad-hoc | ad-hoc | ad-hoc | ad-hoc |
| `threads.py` | 607 | ad-hoc | ad-hoc | ad-hoc | ad-hoc |
| `x_twitter.py` | 651 | ad-hoc | ad-hoc | ad-hoc | ad-hoc |
| `youtube.py` | 753 | ad-hoc | ad-hoc | ad-hoc | ad-hoc |

Total ~3,200 LOC of clients that share at minimum ~150 LOC of identical
infrastructure. The 5× re-implementation has produced drift: IG silently
no-ops on missing tokens while X raises; FB uses 5 retries while YT uses 3;
log formats differ; missing-token return shapes are subtly inconsistent.

`BasePlatformClient` (new in `genlab_core/platforms/base.py`) owns the
shared infrastructure. Each migrated subclass shrinks to ~400 LOC of
platform-specific behavior, and all clients share consistent contracts for
missing tokens, logging, and the public `publish()` entry point.

## Migration order (one PR per row)

| Order | Client | Why this order |
|---|---|---|
| 1 | `instagram.py` | Smallest of the 5; well-tested; the proof-of-pattern |
| 2 | `threads.py` | Shares Meta auth model with IG — pattern transfers cleanly |
| 3 | `facebook.py` | Largest Meta client; biggest LOC win |
| 4 | `youtube.py` | OAuth2 refresh token model — different from Meta EAA tokens; tests the abstraction |
| 5 | `x_twitter.py` | OAuth 1.0a — most exotic; left for last so we know the abstraction generalizes |
| 6 | `tiktok.py` | Currently a 31-LOC stub; decide whether to flesh out or remove |

## Per-client migration recipe

### Step 1: Subclass `BasePlatformClient`

```python
# BEFORE
class InstagramClient:
    def __init__(self, niche_id: str):
        self.niche_id = niche_id

    def publish(self, payload: PublishPayload) -> PublishResult:
        token = os.environ.get("META_ACCESS_TOKEN", "")
        if not token:
            logger.warning("IG: no META_ACCESS_TOKEN — skipping")
            return PublishResult(platform="instagram", success=False, error="no token")
        # ... rest of upload logic
```

```python
# AFTER
from genlab_core.platforms.base import BasePlatformClient


class InstagramClient(BasePlatformClient):
    platform_id = "instagram"
    TOKEN_ENV_SUFFIX = "META_ACCESS_TOKEN"

    def _do_publish(self, payload: PublishPayload, token: str) -> PublishResult:
        # ... rest of upload logic (no token resolution, no missing-token check)
```

The base handles:
- Per-niche token resolution via `resolve_niche_env()` (strict no-cross-channel rule for non-BB)
- Missing-token early-return with a `SKIPPED` `PublishResult`
- Per-platform child logger (`genlab_core.platforms.base.instagram`)
- Public dispatch entry validation

### Step 2: Drop the ad-hoc dispatch entry

If the existing client has a method like `publish(self, payload)` that does
token + log + actual API call, **rename the API-call part to `_do_publish`**
and delete the rest. The base's `publish()` is now the only public entry.

### Step 3: Preserve `Engageable` / `Trackable` / `HealthCheckable` methods

If the client implements `post_reply` (Engageable), `get_metrics` (Trackable),
or `check_token_health` (HealthCheckable), **leave those methods alone** —
the runtime `isinstance(client, Engageable)` check picks them up regardless
of whether the client inherits from the base.

### Step 4: Update existing tests

For each existing test that does
`client = InstagramClient(); client.publish(payload)`:

- If the test sets `META_ACCESS_TOKEN` via `monkeypatch.setenv`, no change needed
- If the test mocks `os.environ.get` directly, update to mock
  `genlab_core.publishing.niche_credentials.resolve_niche_env` instead
- If the test exercises the missing-token path, verify the new return shape
  (`PublishResult(success=False, error="missing TOKEN_NAME for niche X ...")`)

### Step 5: Verify the contract test

After migrating, add the client to
`genlab-core/tests/platforms/test_base.py::test_all_clients_inherit_from_base`
(future test, added in PR 5/5 — when all 5 are migrated, this test pins the
end state).

## Phase 0 (this PR) — what shipped

- `genlab_core/platforms/base.py` (~150 LOC)
- `genlab_core/tests/platforms/test_base.py` (~200 LOC, 13 tests)
- This migration guide
- **Zero existing client behavior changed.** All 5 clients still work
  exactly as before.

## Phase 1+ (follow-up PRs)

Per the table above, one PR per client, in order, smallest first.
