# A-0032b Migration Recipe — Fix Pattern for Direct-`psycopg.connect` Bypass Sites

**Purpose.** Codify the mechanical fix pattern the current fix batch established, so the
remaining ~27 bypass sites can be closed in a follow-up session (or by the operator with
an agent) without per-site head-scratching about which shape to use.

**Companion docs.** `.audit/A-0032b_APPLY_GATE.md` (site inventory + risk table);
`.audit/AUDIT_A_REGISTER.yaml` (A-0032b entry); commits `709d6703`, `bc1e8b94`, `bface52a`,
`6e11dddf` (pattern-establishing examples).

**Reference module.** `genlab-core/src/genlab_core/storage/tenant_context.py` — the
purpose-built `pg_connect` drop-in written for this class of fix. Read its docstring before
applying the recipe; it explains the ContextVar mechanism, the `GENLAB_REQUIRE_TENANT_GUC`
fail-closed flag, and the "why not monkey-patch psycopg" tradeoff.

---

## 1. The three shapes

### Shape A — bare `psycopg.connect(dsn, ...)`

**Detection grep**: `grep -rIn 'psycopg\.connect(' --include='*.py' --exclude-dir=.venv
--exclude-dir=migrations --exclude-dir=tests .`

**Fix**: replace with `pg_connect(dsn, niche_id=<intent>, ...)`.

```python
# BEFORE
import psycopg
from psycopg.rows import dict_row
conn = psycopg.connect(dsn, row_factory=dict_row)

# AFTER (per-niche intent)
from psycopg.rows import dict_row
from genlab_core.storage.tenant_context import pg_connect
# A-0032b: <one-line rationale for niche choice>
conn = pg_connect(dsn, niche_id=niche_id, row_factory=dict_row)

# AFTER (admin/cross-niche intent)
conn = pg_connect(dsn, niche_id="all", row_factory=dict_row)
```

**Note**: `pg_connect` transparently forwards `row_factory`, `connect_timeout`, and every
other kwarg `psycopg.connect` accepts. No behavioral change beyond adding the `set_config`
call post-connect. Established at: `learning/late_reward.py:137,352`, `learning/metrics/
follower_delta.py:107`, `dashboard/server/core/attribution_health.py:80`, `monitoring/
attribution_health_monitor.py:96`, `scripts/nightly_schedule_*.py`, `scripts/
monetization_preflight.py`.

### Shape B — `psycopg_pool.ConnectionPool` pool sites

**Detection grep**: `grep -rIn 'psycopg_pool\|pool\.connection()\|pool\.getconn(' --include='*.py'
--exclude-dir=.venv --exclude-dir=tests .`

**Fix**: add a `set_config` call at the top of every `with conn.cursor()` block, INSIDE the
cursor's implicit transaction. `pg_connect` doesn't apply — the pool owns the connection.

```python
# BEFORE
pool = _get_pg_pool()
with pool.connection() as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ... FROM some_rls_table WHERE niche_id = %s",
            (niche_id,),
        )

# AFTER (per-niche intent — niche_id in scope)
pool = _get_pg_pool()
with pool.connection() as conn:
    with conn.cursor() as cur:
        # A-0032b: set niche context before queries (transaction-scoped,
        # does not leak to other pool consumers).
        cur.execute(
            "SELECT set_config('app.niche_id', %s, true)", (niche_id,)
        )
        cur.execute(
            "SELECT ... FROM some_rls_table WHERE niche_id = %s",
            (niche_id,),
        )

# AFTER (admin/cross-niche intent)
        cur.execute(
            "SELECT set_config('app.niche_id', '', true)"
        )
```

**Rationale for the 3rd arg `true`**: `set_config('key', 'value', true)` is transaction-
local (equivalent to `SET LOCAL`). Setting on a pool-managed connection without `true`
would persist for the connection's lifetime, potentially leaking to whichever caller gets
the pool connection next — that would silently break tenant isolation. `true` scopes it
to the current transaction only.

Established at: `learning/metric_collector.py:361,654`.

### Shape C — `PostgresBackend` calls that omit `niche_id`

**Detection grep**: `grep -rIn 'pg\.\(create\|update\|delete\|insert\)\|backend\.\(create\|
update\|delete\|insert\)' --include='*.py' --exclude-dir=.venv --exclude-dir=tests .`
Then filter to calls that omit `niche_id=` kwarg OR pass `niche_id=None` OR pass
`niche_id=x or None`.

**Fix**: pass `niche_id` explicitly. For `create/insert/update/delete`, method defaults
to `None` which post-migration is rejected. For `find`, method defaults to `""` which is
admin-mode escape-hatch — SAFE post-migration; still worth passing explicitly for clarity.

```python
# BEFORE (dangerous)
pg.create("affiliate_clicks", record)

# AFTER (per-niche)
pg.create("affiliate_clicks", record, niche_id=niche_id)

# AFTER (admin/cross-niche)
pg.create("some_table", record, niche_id="")

# BEFORE (dangerous conditional)
be.create("Analytics", fields, typecast=True, niche_id=niche_id or None)

# AFTER (admin fallback when caller's niche is falsy)
be.create("Analytics", fields, typecast=True, niche_id=niche_id or "")
```

Established at: `monetization/link_tracker.py:144` (per-niche), `http/analytics_store.py`
× 9 sites (admin fallback pattern).

---

## 2. Deciding per-niche vs admin intent

For every fix site, ask: **"After the migration, what should this code see?"**

### Per-niche intent
- A pipeline stage running for a specific niche (`run_pipeline(niche_id)`, `render_stage`,
  `write_content`).
- A reward-writer that persists a per-blueprint reward (blueprint carries niche_id).
- A collector that fetches metrics for a specific `(niche, platform)` tuple.
- A dashboard route that renders "this niche's" view (`GET /api/v1/analytics?niche=X`).

**Fix**: pass the niche variable that's in scope. If the code needs to look it up first
(e.g. from a blueprint row), lift the lookup ABOVE the connect, or use admin-mode for the
lookup and then `set_config` for the subsequent per-niche queries.

### Admin/cross-niche intent
- Nightly schedulers that iterate all niches (`for niche_id in NICHE_IDS: ...`).
- Cross-niche summary queries (`GROUP BY niche_id`, `SELECT DISTINCT niche_id FROM ...`).
- Health monitors that compute per-niche AND overall percentages.
- Backfill scripts that operate on historical data across niches.
- Read-only ops queries and one-shot investigations.

**Fix**: `niche_id="all"` (or `""` — the migration preserves both as escape-hatch). Add a
one-line comment noting WHY this site is admin-mode, so a future reader doesn't accidentally
"tighten" it back to a per-niche pattern.

### The rule of thumb from CLAUDE.md rule #27
Belt-and-suspenders: **niche GUC + explicit `AND niche_id = %s` in the WHERE clause**.
Admin GUC + per-niche WHERE clauses (as in `nightly_schedule_top_per_niche.py`) is the
safest combination — RLS lets the row through AND the query narrows to the right niche.

---

## 3. Remaining fix work — per-category classification

From `.audit/A-0032b_APPLY_GATE.md` §3.1, the ~27 remaining sites:

| Category | Sites | Likely intent |
|---|---|---|
| Backfill scripts | ~15 | Mostly admin (historical scans across niches) — the `backfill_*` files by name imply cross-niche one-shot ops |
| Cross-platform gate | 1 (`scheduling/cross_platform_gate.py:132`) | Per-niche (gate runs per publish; niche is in blueprint) |
| Strategist / experiment | 3 (`strategist_actions.py:79`, `auto_accept_strategist_proposals.py:171`, `parse_testable_predictions.py:137`) | Per-niche (strategist reports are per-niche) |
| Revenue / affiliate | 3 (`register_click_rewards.py:168`, `register_conversion_rewards.py:207`, `scrape_affiliate_revenue.py:69`, `import_cuelinks_conversions.py:231`) | Mostly per-niche (row carries niche); admin if the script joins across niches for a batch upsert |
| Engagement | 1 (`drain_engagement_review_queue.py:152`) | Per-niche (engagement queue is per-niche) |
| Verifiers / validators | 3 (`validate_calibration_data.py:286`, `verify_intelligent_transform.py:113`, `run_shadow_reviewer.py:164`) | Admin (validators sample across niches) |
| Config / preference | 2 (`run_config_update.py:151`, `preference_hint.py:93`) | Depends — read the surrounding code |
| Experiment lifecycle | 1 (`run_experiment_lifecycle.py:111`) | Per-niche |

**Total: ~27 sites.** At Shape A's ~2-tool-call per-site rate, this is a bounded 1-1.5 hour
mechanical follow-up. The pattern is uniform; the only per-site decision is intent.

---

## 4. Verification recipe (per-batch)

After each batch of fixes:

```bash
# 1. Static — no bare psycopg.connect remaining in the fixed files
grep -n 'psycopg\.connect(' <fixed-files>

# 2. Syntax parse (fast smoke test)
python3 -c "import ast; [ast.parse(open(f).read()) for f in [<fixed-files>]]; print('parse OK')"

# 3. Screening grep across the workspace — spot new/missed sites
grep -rIn 'psycopg\.connect(' --include='*.py' \
  --exclude-dir=.venv --exclude-dir=migrations --exclude-dir=tests . | wc -l
# baseline was ~38 at gate; each batch should reduce this.

# 4. Ensure test suite still parses (A-0074 dependency permitting)
uv run pytest --collect-only -q 2>&1 | tail -3
```

After the ENTIRE work list is done, re-run the apply-gate (Prompt 1 procedure from the
original v1.5 spec) and confirm the verdict flips to **GO**. Then — and only then — write
the updated verdict to `.audit/A-0032b_APPLY_GATE.md` and OPERATOR_ACTIONS.md Row #3 is
unblocked.

---

## 5. What NOT to do

- **Do not monkey-patch `psycopg.connect`**. `tenant_context.py`'s docstring explains why
  (other libraries use psycopg under the hood; silently intercepting their connections would
  set RLS state at unexpected times).
- **Do not add a bare `set_config('app.niche_id', ...)` next to a raw `psycopg.connect`
  call** when the surrounding code could route through `pg_connect` instead. Removing the
  bypass entirely is better than patching around it, per the audit-gate discipline.
- **Do not use `SET app.niche_id` without the `LOCAL` semantic** on pool-managed
  connections. `set_config('app.niche_id', 'x', true)` (3rd arg true) is the safe form.
- **Do not skip the "why admin" comment** at admin-mode sites. A future reader auditing
  tenant isolation will need to know whether the admin declaration was deliberate or
  accidental.
- **Do not flip `GENLAB_REQUIRE_TENANT_GUC=1`** until every site is either pg_connect'd
  or has an explicit `set_config` — else legitimate calls into unmigrated code fail-closed
  and take down the pipeline.

---

## 6. Pin-test follow-up (deferred to A-0074 unblock)

Once the test suite collects clean (Wave-1.2 A-0074), add a pin test that:

1. Greps for `psycopg.connect(` in application code (`src/`, `scripts/`, `dashboard/`).
2. Requires each match to be in an allowlist file OR to have `pg_connect(` or
   `set_config(...app.niche_id` within a 30-line window.
3. False-positives are acceptable (documented allowlist); false-negatives are the risk.

This is a **screening test, not a proof of correctness** — it flags likely bypass sites
for human review. The real safeguard is the two-part post-apply gate the migration
docstring specifies (`SELECT niche_id, COUNT(*) FROM blueprints` returns 0 rows unset,
correct rows with niche set), plus watching the learning-loop + nightly scheduler write
rows on the next fire after the operator applies the migration.
