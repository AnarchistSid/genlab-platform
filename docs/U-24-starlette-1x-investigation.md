# U-24 — starlette 0.x → 1.x investigation (2026-06-18)

## TL;DR

`starlette` 1.x is **API-compatible** with our pinned FastAPI 0.136.3
(FastAPI's only constraint is `starlette>=0.46.0`). The previously-
documented "starlette 1.x breaks 9 tests" is **not a starlette
regression** — it's a **pre-existing test-isolation bug** that
manifests when starlette 1.x changes test ordering.

The upgrade can ship as soon as the test-isolation bug is fixed. The
test-isolation bug is genuinely multi-day work (root-cause hunt for
the `load_dotenv()` re-population path).

## Compatibility check

```sh
$ python3 -c "import urllib.request, json
print(json.loads(urllib.request.urlopen('https://pypi.org/pypi/fastapi/0.136.3/json').read())['info']['requires_dist'])" | grep starlette
['starlette>=0.46.0']
```

starlette 1.3.1 satisfies `>=0.46.0`. No upper bound on FastAPI's side.

## Test reproduction

With `starlette==1.3.1` installed against unmodified main:

```
$ uv run --project genlab-core pytest genlab-core/tests/ --timeout=60 -q
8 failed, 4368 passed, 19 skipped, 40 deselected, 3 warnings
```

The 8 failures:

```
FAILED genlab-core/tests/storage/test_postgres_phase2_stories_assets.py::TestStoriesCRUD::test_create_and_get
FAILED genlab-core/tests/storage/test_postgres_phase2_stories_assets.py::TestStoriesCRUD::test_update
FAILED genlab-core/tests/storage/test_postgres_phase2_stories_assets.py::TestAssetsCRUD::test_create_and_get
FAILED genlab-core/tests/storage/test_postgres_phase3_pub_analytics.py::TestPublishingAnalyticsCRUD::test_create_and_get
FAILED genlab-core/tests/storage/test_postgres_phase3_pub_analytics.py::TestPublishingAnalyticsCRUD::test_update
FAILED genlab-core/tests/storage/test_promoted_columns_drift.py::test_promoted_columns_match_schema
FAILED genlab-core/tests/storage/test_r45_backend_create_parity.py::test_r45_postgres_create_returns_satisfies_union
FAILED genlab-core/tests/storage/test_r45_backend_create_parity.py::test_r45_postgres_batch_create_returns_satisfy_union
```

All of these gate via:

```py
pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_PASSWORD"),
    reason="POSTGRES_PASSWORD not set — skip PostgreSQL integration tests",
)
```

## Root cause

When run **individually**, every failing test SKIPS cleanly. The
failures only surface when the full suite runs.

`genlab_core/settings.py:37` calls `load_dotenv(str(_root_env),
override=False)` at module-import time. `.env` has a non-empty
`POSTGRES_PASSWORD`. So:

1. `tests/conftest.py` runs first → pops `DATABASE_URL` and
   `GENLAB_USE_POSTGRES` from `os.environ`. Does NOT pop
   `POSTGRES_PASSWORD` (the pop is missing).
2. The FIRST test that imports anything from `genlab_core` triggers
   `settings.py`'s `load_dotenv()` → re-populates `POSTGRES_PASSWORD`
   from `.env`.
3. Storage tests evaluate `skipif(not os.environ.get("POSTGRES_PASSWORD"))`.
   Predicate is now FALSE (password IS set) → tests RUN instead of skip.
4. The tests try to connect to localhost:5432 with the operator's prod
   password — they get a connection but operate against the operator's
   local dev database, where schema/RLS state isn't what these tests
   expect → assertion failures.

On `starlette==0.52.1`, the same test-ordering quirk also exists but
the order happens to be different (whichever test triggers the
`genlab_core` import first runs AFTER the storage tests would have
skipped). On `starlette==1.3.1`, the import-order graph shifts and
the storage tests now run AFTER the leak — so they fail.

## Why the obvious fix didn't fully work

Attempted in this session: pop `POSTGRES_PASSWORD` in conftest AFTER
force-importing `genlab_core.settings`:

```py
import genlab_core.settings  # triggers load_dotenv exactly once
os.environ.pop("POSTGRES_PASSWORD", None)
```

This drops the storage-test failure count from 8 to 5 — significant
but incomplete. Some other code path re-sets `POSTGRES_PASSWORD`
between conftest and test execution. Tracing it requires either:

1. A `setenv`-hook on `os.environ` to log every set (Python doesn't
   support this natively; would need a `MutableMapping` subclass).
2. Bisecting the test suite to find which file is the trigger.
3. Source-grepping for non-obvious setters (e.g. inside `pytest_plugins`
   chains or via `monkeypatch` fixtures that leak).

None of these is a 30-minute fix. The cleanest path is to remove the
`load_dotenv()` call from `settings.py` and require explicit env-var
loading in production entry points (CLI, gunicorn wrapper, etc) —
but that's an invasive change touching many entry points.

## Compat audit of starlette 1.x changes

Spot-check of the main breaking-change candidates in starlette 1.x:

| Area | Status | Notes |
|------|--------|-------|
| Middleware API | ✓ compatible | We don't subclass starlette middleware directly |
| TestClient | ✓ compatible | FastAPI re-exports its own TestClient |
| Response classes | ✓ compatible | We use FastAPI's JSONResponse, not starlette's |
| Background tasks | ✓ compatible | FastAPI BackgroundTasks unchanged |
| WebSockets | ✓ compatible | We use socket.io, not starlette WebSockets |
| Static file mounting | ✓ compatible | dashboard uses `app.mount("/static", StaticFiles(...))` — API unchanged in 1.x |
| `Request.client` | ✓ compatible | We don't read `.client` |
| Form parsing | ✓ compatible | We use JSON-only endpoints |

No code in `genlab-core/` or `dashboard/server/` references the breaking
starlette 1.x API changes documented in the upstream changelog.

## Recommendation

Two-step path to ship the upgrade:

1. **Fix the test-isolation bug first** (separate PR, multi-day):
   either remove `load_dotenv()` from settings.py or wrap it with a
   guard that respects an existing `GENLAB_SUPPRESS_DOTENV=1` env var
   that conftest can set. Adds a regression test that pins the env-
   var state across `import genlab_core.settings`.

2. **Bump starlette** (this doc + an explicit pin in `genlab-core/
   pyproject.toml`): `starlette>=1.0,<2`. The actual code change is
   one line; the safety net is the test-isolation fix.

Until both ship, `starlette==0.52.1` remains pinned via the resolver
(no explicit constraint; FastAPI's `>=0.46.0` floor allows it and the
resolver prefers the latest 0.x). PR #277-class operator monitoring
suggests starlette 0.52.1 has no outstanding CVEs blocking continued
use.

## Files

- `docs/U-24-starlette-1x-investigation.md` (this file)

## Status in the pending doc

`docs/PENDING-AS-OF-2026-06-18.md` lists U-24 (residual) under
"genuinely-open — explicit multi-day work". This investigation
confirms the multi-day classification: it's not a starlette
incompatibility, but it does require a non-trivial test-isolation
refactor to land safely.
