# A-0032b Apply-Gate — RLS Deny-by-Default Migration Safety

**Migration under review:** `genlab-core/migrations/versions/a0b0c0d0e0f0_rls_deny_by_default.py`
(commit `1f6b3abc` on branch `audit-a/fix-queue-execution` — written, NOT applied to prod).

**Question:** If applied, will any running application code path start silently returning zero rows?

**Verdict:** **NO-GO.**

**Risk count (UNSET + UNSURE sites querying the 24 RLS tables):** ~40 unique call sites across
the codebase. Concentrated in `scripts/` (systemd-timer entry points, backfills), `genlab-core/
src/genlab_core/learning/`, `genlab-core/src/genlab_core/monitoring/`, and a handful of
`PostgresBackend` calls that omit `niche_id`.

The migration is CORRECT and the fix strategy is sound (bounded pattern — one line per site).
It is not APPLY-SAFE today because roughly 40 read paths depend on the current fail-open
behaviour: the migration would flip them from "return all rows admin-style" to "return zero
rows silently" without a single traceback.

---

## 1. What the migration does

The migration rewrites 24 RLS policies. Before: fail-open when `app.niche_id` GUC unset.
After: deny-by-default when unset. Admin escape-hatch (`''` or `'all'`) is preserved.

```
BEFORE (fail-open):
  niche_id = current_setting('app.niche_id', true)
  OR current_setting('app.niche_id', true) = ANY (ARRAY['', 'all'])
  OR current_setting('app.niche_id', true) IS NULL     -- THE LEAK

AFTER (deny-by-default):
  niche_id = current_setting('app.niche_id', true)
  OR current_setting('app.niche_id', true) = ANY (ARRAY['', 'all'])
```

**Important nuance for site classification:** any code that runs `SET app.niche_id = ''` (or
`SET app.niche_id = 'all'`) is **SAFE** — the migration preserves those as the admin escape
hatch. Only code that leaves the GUC entirely unset breaks.

### The 24 tables

Standard `niche_isolation` policy (21 tables):
`ab_tests, affiliate_revenue, analytics, assets, audience_snapshots, bandit_arms,
bandit_validation, blueprints, config_updates, content_memory, email_subscribers,
monetisationprogress, pending_engagement, pending_feedback, post_decision_trace,
preference_data, publishing_analytics, sources, stories, templates, tier_history`

Non-standard policy names (3 tables):
`affiliate_clicks (affiliate_clicks_niche_policy)`,
`compliance_events (compliance_events_rls)`,
`niche_pauses (niche_pauses_rls)`

Comment on migration line 97 says "22 tables" but list contains 21. Doc-drift only, no
runtime impact.

---

## 2. Reference SET patterns (what a safe site looks like)

Three shapes exist today:

- **A. `PostgresBackend` abstraction** (`storage/postgres.py`) — `find/insert/create/update/
  update_where/delete` methods take a `niche_id` kwarg and internally run
  `SELECT set_config('app.niche_id', %s, true)` before the query. This is the primary safe
  path. `niche_id=""` is admin mode (safe post-migration).
- **B. `pg_connect`** (`storage/tenant_context.py:186`) — `psycopg.connect` drop-in that
  auto-runs `SET app.niche_id = <value>` post-connect. Any site using this is SAFE.
- **C. Direct `set_config` call** — `cur.execute("SELECT set_config('app.niche_id', %s,
  true)", (niche,))` before running queries. Some scripts do this explicitly with `''`
  (admin mode).

The dangerous shape is raw `psycopg.connect(dsn)` + query without any SET call.

---

## 3. Read-site inventory — the risk table

### 3.1 Direct-`psycopg.connect` sites (bypass the abstraction)

**~38 sites** identified by parallel Explore agent enumerating every non-migration,
non-test `psycopg.connect` call. Each site was verified to (a) NOT call `pg_connect`,
(b) NOT run `set_config('app.niche_id', …)` before its SQL, and (c) query at least one of
the 24 RLS tables. See raw enumeration in the sub-agent's ledger.

**Classification: UNSET** (query runs with GUC unset → returns zero rows post-migration).

| Category | Sites | Example files |
|---|---|---|
| Learning-loop workers | 4 | `learning/late_reward.py:137,352`, `learning/metric_collector.py:214`, `learning/follower_delta.py:107` |
| Monitoring / health | 3 | `monitoring/attribution_health.py:80`, `monitoring/attribution_health_monitor.py:96,225` |
| Scheduling / cross-platform | 2 | `scheduling/cross_platform_gate.py:132`, related sites |
| Backfill scripts | ~15 | `scripts/backfill_bandit_from_pending_feedback.py:113`, `scripts/backfill_insights.py:247`, `scripts/backfill_calibration_2026_06_15.py:254`, `scripts/backfill_column_from_extra.py:72`, `scripts/backfill_historical_metrics.py:244`, `scripts/backfill_orphan_rewards.py:222`, `scripts/backfill_product_slug.py:73`, `scripts/backfill_analytics_double_prefix.py:75`, `scripts/rerender_edited_hooks.py:277`, `scripts/retro_credit_uncredited_posts.py:158,308`, others |
| Nightly scheduler | 2 | `scripts/nightly_schedule_remediate.py:289`, `scripts/nightly_schedule_top_per_niche.py:198` |
| Strategist / experiment | 3 | `scripts/strategist_actions.py:79`, `scripts/auto_accept_strategist_proposals.py:171`, `scripts/parse_testable_predictions.py:137` |
| Preflight (uses `GROUP BY niche_id`, admin-mode intent) | 4 | `scripts/monetization_preflight.py:192,213,234,255` |
| Revenue / affiliate | 3 | `scripts/register_click_rewards.py:168`, `scripts/register_conversion_rewards.py:207`, `scripts/scrape_affiliate_revenue.py:69`, `scripts/import_cuelinks_conversions.py:231` |
| Engagement | 1 | `scripts/drain_engagement_review_queue.py:152` |
| Verifiers / validators | 2 | `scripts/validate_calibration_data.py:286`, `scripts/verify_intelligent_transform.py:113`, `scripts/run_shadow_reviewer.py:164` |
| Experiment lifecycle | 1 | `scripts/run_experiment_lifecycle.py:111` |
| Config / preference | 2 | `scripts/run_config_update.py:151`, `scripts/preference_hint.py:93` |

Spot-checks against three of the highest-impact sites confirmed the classification:

- **`learning/late_reward.py:137`** — bare `psycopg.connect(dsn, row_factory=dict_row)`,
  no SET call. Query joins `blueprints`, `publishing_analytics`, `pending_feedback` — three
  RLS tables in one query. Post-migration: returns zero rows silently → Intervention 1
  reward-shaping goes dark exactly the same way it did in the 2026-07-02 SQL bug the same
  file's docstring warns about.
- **`learning/metric_collector.py:214`** — uses `psycopg_pool.ConnectionPool` (getconn does
  NOT run SET). Fetches per-niche `monetisationprogress` rows for reward computation.
  Post-migration: zero rows → RewardShaper falls back to defaults → learning loop degrades
  silently.
- **`scripts/monetization_preflight.py:192-255`** — legitimately runs `GROUP BY niche_id`
  cross-niche aggregation. Intent is admin-mode. Fix is a one-line `SET app.niche_id = 'all'`
  before the queries (matches the migration's preserved escape hatch).

### 3.2 `PostgresBackend` abstraction sites

**Method defaults** (from `storage/postgres.py`):
- `find` — default `niche_id=""` → **safe** post-migration (admin escape hatch preserved)
- `insert` / `create` / `update` / `update_where` / `delete` — default `niche_id=None` →
  **DANGEROUS** post-migration (NULL is what the migration explicitly rejects)

| File:line | Method | Table | Argument | Classification | Notes |
|---|---|---|---|---|---|
| `monitoring/token_health.py:463` | `find` | `blueprints` | omitted (default `""`) | **SAFE** | Agent 2 flagged as risky but `""` is the admin hatch; `pg.find("blueprints", max_records=1)` returns rows in admin mode after migration |
| `monetization/link_tracker.py:144` | `create` | `affiliate_clicks` | omitted (default `None`) | **UNSET** | INSERT will fail post-migration; the `record` dict carries `niche_id` but the method arg is not passed |
| `storage/factory.py:9` | `find` | `stories` | omitted (default `""`) | **SAFE** (example code) | `backend.find("Stories", ...)` — doc/example only |
| `http/analytics_store.py:365` | `create` | `analytics` | `niche_id=niche_id or None` | **UNSURE** | Conditional; when caller's `niche_id` is falsy, passes `None` which the migration rejects |

**Net PostgresBackend risk: 1 confirmed UNSET + 1 UNSURE = 2 sites.**

### 3.3 In-source documented UNSET sites

Three places where the current code documents ITS OWN dependence on the fail-open behaviour
in comments or module docstrings — these are known-unsafe and were surveilled by the
`tenant_context` refactor which was never completed:

| File:line | Documented dependency | Notes |
|---|---|---|
| `storage/tenant_context.py:6-7` | "sites in the codebase don't set app.niche_id GUC at all" | The module was written explicitly to fix this class — its `pg_connect` drop-in is the shipped solution but adoption is incomplete |
| `storage/disk_quota.py:205-207` | "leaves `app.niche_id` GUC unset → today's RLS policy treats unset as [visible-to-all]" | Explicit reliance on fail-open |
| `learning/arm_loader.py:40` | "``app.niche_id`` is set on the session, which is not [always the case]" | Signals uncertainty in the surrounding path |

These overlap with 3.1's enumeration; not double-counted in the risk total.

---

## 4. Verdict

### Overall risk tally

| Category | Count | Post-migration behavior |
|---|---|---|
| Direct-`psycopg.connect` UNSET (§3.1) | ~38 | Returns 0 rows silently |
| `PostgresBackend` UNSET / UNSURE (§3.2) | 2 | INSERT fails or reads 0 rows on `None` branch |
| **Total unique risky sites** | **~40** | |
| SAFE (admin-mode with `''`) | ~5 | Preserved by migration's escape hatch |
| UNAFFECTED (no RLS-table query) | ~19 | Migration has no effect on this path |

### Reasoning for the verdict

The migration is not GO because it fails the gate's stated criterion — "every read site
against all 24 tables is SET." It is not GO-WITH-STAGING because the migration is atomic
per-transaction across all 24 policies (one `alembic upgrade head`) — partial apply is not
possible.

Applying it today would:
1. Silently break the learning loop's reward path (`late_reward.py:137` → three RLS tables in one
   join → zero rows → no reward writes → LinUCB updates stop).
2. Silently break metric collection (`metric_collector.py:214` → per-niche
   `monetisationprogress` reads return zero → RewardShaper falls back to defaults).
3. Silently break the nightly scheduler (`nightly_schedule_top_per_niche.py:198` → zero
   blueprint candidates → nothing scheduled).
4. Break every backfill script (~15 sites) that runs on-demand from the ops runbooks.
5. Break monetization preflight (cross-niche `GROUP BY niche_id` returns empty).
6. Break `affiliate_clicks` INSERT via `link_tracker.py:144` → revenue attribution stops.

None of these would fire a traceback. The whole class-of-bug the audit's Rule #19 and #26
were written to prevent (silent-failures masquerading as no-work-available) would compose
with the migration into exactly the shape those rules were designed to detect after the fact.

### Recommended path forward

**Prompt-2 step 0** should fix the ~40 sites BEFORE the maintenance window applies the
migration. The fix pattern is bounded and mechanical:

- **Per-niche scripts / workers** — add
  `cur.execute("SELECT set_config('app.niche_id', %s, true)", (niche,))` after connect,
  once per connection, before any query. Follow the pattern in
  `storage/postgres.py:635/705/839/957`.
- **Cross-niche aggregators** (health monitors, `monetization_preflight`, backfills that
  operate across niches) — set admin mode explicitly:
  `cur.execute("SET app.niche_id = ''")`. This is the migration-preserved escape hatch.
- **`PostgresBackend` sites** — pass `niche_id="…"` explicitly at the call site.

Estimated scope: 2-3 sessions of mechanical edits, per-subsystem commits. A follow-up
pin-test can grep for `psycopg.connect(` in application code and require every site to be
in an allowlist of files that also contain either `pg_connect(` or `set_config(...niche_id`.

**After the fixes land**, re-run this apply-gate. The verdict should flip to GO.

### What to record in `OPERATOR_ACTIONS.md`

Row #3 (role switch / A-0032 code-side) status: gain Wave-2 code-shipped, but the migration
is **NOT APPLY-SAFE** until ~40 read sites fix the SET pattern. Do not schedule the
maintenance window's `alembic upgrade head` step until this gate re-runs GREEN.

---

## 5. Discipline

Verdict was produced by:
- Direct read of the migration file (line-by-line policy enumeration).
- Two parallel Explore sub-agents: one for direct `psycopg.connect` sites, one for
  `PostgresBackend` call sites. Both restricted to non-migration, non-test paths across
  the 7 workspace members.
- Spot-checks against the three highest-impact reported sites to confirm the agents' reads
  matched the file contents (`late_reward.py:137`, `metric_collector.py:214`,
  `token_health.py:463`, `link_tracker.py:144`).

No files edited. No migration applied. No prod state changed. This document is the sole
output.

**Verdict: NO-GO. Unset-site count: ~40.**

---

# APPENDIX — Progress after Prompt-A first pass (2026-07-30)

The following updates were captured during Prompt-A execution (commits `709d6703`,
`bc1e8b94`, `bface52a`, `6e11dddf` on branch `audit-a/fix-queue-execution`). The overall
verdict remains **NO-GO** until the remaining ~20-25 UNSET sites are fixed. Progress:

## Subsystem fix summary

| Subsystem | Sites fixed | Commit | Intent |
|---|---|---|---|
| `learning/late_reward.py` | 2 | `709d6703` | admin — function looks up niche FROM the joined blueprint |
| `learning/metric_collector.py` (pool) | 2 | `709d6703` | per-niche — set_config LOCAL in cursor blocks |
| `learning/metrics/follower_delta.py` | 1 | `709d6703` | per-niche — pg_connect with niche_id kwarg |
| `dashboard/.../attribution_health.py` | 1 | `bc1e8b94` | admin — endpoint aggregates cross-niche |
| `monitoring/attribution_health_monitor.py:96` | 1 | `bc1e8b94` | admin — iterates all 5 niches internally |
| `monetization/link_tracker.py` (PostgresBackend) | 1 | `bface52a` | per-niche — pass niche_id to pg.create |
| `http/analytics_store.py` (PostgresBackend) | 9 | `bface52a` | admin fallback — `or None` → `or ""` |
| `scripts/nightly_schedule_top_per_niche.py` | 1 | `6e11dddf` | admin — iterates niches with WHERE-clause narrowing |
| `scripts/nightly_schedule_remediate.py` | 1 | `6e11dddf` | admin — cross-niche scheduling gap scan |
| `scripts/monetization_preflight.py` | 4 | `6e11dddf` | admin — cross-niche GROUP BY summary (2 are safe overshoot on non-RLS tables) |

**Total: 23 code-site edits, ~13 real UNSET closures on RLS-scoped tables.**

## Corrections to the original apply-gate inventory (Agent-1 report)

Sites reclassified during Prompt-A verification:

- `monitoring/attribution_health_monitor.py:225` and `:270` — Agent 1 said `blueprints`;
  actual query is on `pipeline_alerts` (line 228: `UPDATE pipeline_alerts`, line 273:
  `SELECT COUNT(*) FROM pipeline_alerts`). `pipeline_alerts` is NOT in the 24-table RLS
  list. **Both sites are UNAFFECTED.** Not counted in the risk tally.
- `scheduling/strategy_phase.py:157` and `scheduling/strategist_actions.py:79` — query
  `strategist_reports` and `learning_findings`, NOT in the 24-table RLS list.
  **UNAFFECTED.** Not counted.
- `dashboard/server/api/strategist.py:50` and `:197` — same tables. **UNAFFECTED.**
- `monitoring/checks/llm_cost.py:80` and `:107` — query `llm_cost` table. NOT in RLS list.
  **UNAFFECTED.**
- `scripts/monetization_preflight.py:192,213` — query `alembic_version` and
  `information_schema.tables`. NOT RLS-scoped. Fixed with uniform pattern in the same
  file (safe overshoot — no behavior change).

**Net corrections**: Agent 1's raw ~38 estimate was closer to ~28-30 real UNSET sites once
the non-RLS-table sites are removed. Approximately 20-25 sites remain unfixed after this
first pass.

## Migration recipe

The mechanical fix pattern for the remaining sites is codified in
`.audit/A-0032b_MIGRATION_RECIPE.md`. Three shapes (bare psycopg.connect, pool.connection(),
PostgresBackend calls) with fix examples, per-niche vs admin decision criteria, and a
per-category classification of the ~20-25 remaining sites. Follow-up session should
execute against that recipe.

## Updated verdict

**Verdict still NO-GO.** Do not apply the migration until:
1. The remaining ~20-25 UNSET sites (mostly `scripts/backfill_*.py`, revenue/affiliate
   writers, engagement drainer, verifiers, cross_platform_gate) are fixed per
   `MIGRATION_RECIPE.md`.
2. This apply-gate is re-run (independent Prompt-1 read-only enumeration) and the count
   of UNSET/UNSURE sites goes to zero.
3. `OPERATOR_ACTIONS.md` Row #3 is updated with the GO verdict.

Only then schedule the maintenance window.

