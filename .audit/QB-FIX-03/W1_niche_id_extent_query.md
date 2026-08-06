# QB-FIX-03 W1 — `niche_id` Extent Query Across All Niche-Scoped Tables

**Date:** 2026-08-06 22:45 IST
**Result:** **QB-FIX-02 §5 was overstated for `blueprints`/`stories`.** DB is clean; V3 fix was for an in-memory synth dict, not a data-hygiene defect. Three secondary tables have low-severity defects that are out of QB-FIX-03 scope.

## Method

1. Enumerated every table with a `niche_id` column via `information_schema.columns` (39 tables total).
2. Ran the null/empty/`unknown` extent query against each.
3. Characterized non-zero results as either legitimate (cross-niche pool, host-level alert) or actual defect.
4. Verified V3 gate on blueprints created in the last 24 hours.
5. Confirmed Yankee blueprint's `niche_id`.
6. Inspected the RLS policy on `blueprints` + which roles bypass RLS.

## Extent per table (all 39 rows)

| table | null | empty | unknown | total | classification |
|-------|------|-------|---------|-------|----------------|
| blueprints | 0 | 0 | 0 | 2230 | **clean** |
| stories | 0 | 0 | 0 | 2745 | **clean** |
| ab_tests | 0 | 0 | 0 | 238 | clean |
| affiliate_clicks | 0 | 0 | 0 | 176 | clean |
| affiliate_revenue | 0 | 0 | 0 | 50 | clean |
| analytics | 0 | 0 | 0 | 2383 | clean |
| audience_snapshots | 0 | 0 | 0 | 4515 | clean |
| auto_approval_calibration | 0 | 0 | 0 | 305 | clean |
| auto_experiments | 0 | 0 | 0 | 7 | clean |
| bandit_arms | 0 | 0 | 0 | 373 | clean |
| bandit_validation | 0 | 0 | 0 | 200 | clean |
| compliance_events | 0 | 0 | 0 | 1784 | clean |
| config_updates | 0 | 0 | 0 | 4 | clean |
| content_memory | 0 | 0 | 0 | 1634 | clean |
| gate_examinations | 0 | 0 | 0 | 965 | clean |
| late_reward_deltas | 0 | 0 | 0 | 72 | clean |
| learning_findings | 0 | 0 | 0 | 12 | clean |
| monetisationprogress | 0 | 0 | 0 | 15 | clean |
| outbound_reply_history | 0 | 0 | 0 | 7 | clean |
| pending_engagement | 0 | 0 | 0 | 23 | clean |
| pending_feedback | 0 | 0 | 0 | 1262 | clean |
| pipeline_run_costs | 0 | 0 | 0 | 433 | clean |
| post_decision_trace | 0 | 0 | 0 | 297 | clean |
| preference_data | 0 | 0 | 0 | 27 | clean |
| publishing_analytics | 0 | 0 | 0 | 3000 | clean |
| strategist_reports | 0 | 0 | 0 | 5 | clean |
| templates | 0 | 0 | 0 | 330 | clean |
| tenant_niches | 0 | 0 | 0 | 5 | clean |
| tier_history | 0 | 0 | 0 | 5 | clean |
| bandit_arms_pre_reset_20260517 | 0 | 0 | 0 | 20 | clean |
| **assets** | **0** | **0** | **3088** | 3109 | **legitimate — cross-niche pool** |
| **sources** | **0** | **0** | **35** | 219 | **legitimate — shared feed registry** |
| **pipeline_alerts** | **227** | **0** | **0** | 2044 | **legitimate — host-level alerts** |
| **dashboard_events** | **0** | **143** | **0** | 5069 | **defect — operator actions with no niche tag** |
| **ensemble_votes** | **0** | **452** | **0** | 5180 | **defect — ensemble decisions with no tenant** |
| **episodic_events** | **0** | **41** | **0** | 2680 | **defect — 1.5% orphan events** |
| drift_signals / email_subscribers / niche_pauses | — | — | — | 0 | empty tables |

**Total real defects across write paths:** 636 rows across 3 tables. **Zero in blueprints or stories.**

## Legitimate cross-niche / host-level cases

* **`assets` 3088 `unknown`** — shared media library (music beds, logo assets, cross-niche thumbnails). "unknown" is the seed value for the cross-niche pool. Not a defect. Also observed: ~20 `rls_test_*` rows from RLS testing infrastructure.
* **`sources` 35 `unknown`** — shared RSS/feed registry entries used across multiple niches. Not a defect.
* **`pipeline_alerts` 227 NULL** — host-level checks: service_down (69), swap_pressure (48+24), engagement_no_recent_writes (28), disk_pressure (14), warp_down (12), anthropic_credit_exhausted (10), git_drift (4). These are process/infrastructure alerts that are correctly not niche-scoped. NULL is the right value.

## Actual defects (out of QB-FIX-03 scope, filed as follow-ups)

* **`dashboard_events` 143 empty (2.8%)** — operator dashboard actions (`review_approved` 59, `review_skipped` 28, `review_rejected` 28, `review_revised` 28) with no niche context. Impact: per-niche operator-agreement stats can't attribute these. Low severity.
* **`ensemble_votes` 452 empty (8.7%)** — ensemble decision votes with no tenant. Impact: ensemble learning-loop per-niche attribution loses signal. Medium severity for AUTO #1 calibration.
* **`episodic_events` 41 empty (1.5%)** — orphan events. Low severity.

**All three are secondary tables** — not tenant-visible on the reader path, not blueprint-adjacent. Filed as follow-ups; not fixed here.

## RLS behavior on null-niche row

Policy on `blueprints`:
```sql
CREATE POLICY niche_isolation ON blueprints AS PERMISSIVE ...
  USING (
    niche_id = current_setting('app.niche_id', true)
    OR current_setting('app.niche_id', true) = ANY (ARRAY['', 'all'])
    OR current_setting('app.niche_id', true) IS NULL
  );
```

Two Postgres roles:
- `genlab_app` — `rolbypassrls = f` (RLS enforced)
- `genlab` — `rolbypassrls = t` (RLS bypassed)

The application connects as `genlab_app` (verified via `DATABASE_URL` in `.env`). RLS IS enforced on all app queries. CLAUDE.md rule #27's `BYPASSRLS` concern applies to the `genlab` admin role used by direct psql scripts and migrations, not to the application's runtime queries.

**Behavior on a hypothetical `niche_id IS NULL` row for `genlab_app`:**
- Tenant-scoped session (`SET LOCAL app.niche_id = 'movies'`): first predicate is NULL (SQL null propagation on `NULL = 'movies'`), other two predicates false → **row invisible**.
- Session without `app.niche_id` set (unusual, indicates a bug in PostgresBackend): third predicate true → **row visible to whoever is asking**. This is the actual risk shape — a null-niche row leaks to any query that forgot to set the app-niche variable.
- Admin session (`genlab` role): RLS bypassed → row visible regardless.

**Practical answer for a null-niche row:** invisible to correctly-tenant-scoped app queries; visible to app queries that fail to set `app.niche_id`; visible to admin/superuser queries. NOT "visible to every tenant" — visible to NO tenant via the correct-scoping path. But the "forgot to SET LOCAL app.niche_id" scenario is real (belt-and-suspenders `AND niche_id = %s` in `PostgresBackend` catches it there).

## V3 gate (24h post-fix)

```sql
SELECT COUNT(*) FROM blueprints
WHERE created_at >= NOW() - INTERVAL '24 hours'
  AND (niche_id IS NULL OR niche_id = '' OR niche_id = 'unknown');
```
Result: **0**. Gate PASS.

Note: the gate also passed pre-V3-fix (the DB was always clean on the write path). The V3 fix hardened the pre-write in-memory synth dict for `auto_approval_gate.evaluate()` — separate from the DB write path this query measures. So the V3 gate as spec'd would have passed both before and after V3.

## Yankee blueprint (V3 auth check)

```
id=b2292ede-887f-4ee2-8b3f-1daf2c64be95  niche_id=movies (correct)  status=VISUAL_READY
```

**Still not approving** per QB-FIX-02 §5 auth caveat, but for reasons unrelated to niche_id (F4 batch 1 slots are filled by INHERIT + Primetime).

## Downgrade

**QB-FIX-02 §5 was overstated re: the RLS risk from Yankee.** The row was never null-tenant. The `niche=unknown` log was a synth-dict bug, not a persisted-row bug. The V3 code fix was correct but the framing that "a null-tenant row is precisely the shape that must never become normal" — while true in principle — did not describe the actual Yankee row.

The correct framing:
- **Row-level defect:** none in blueprints or stories, ever.
- **In-memory pre-write dict defect:** one, fixed by V3.
- **Downstream impact of the pre-write defect:** `auto_approval_confidence` values on new blueprints since the gate call was added used ai_creators-default thresholds instead of per-niche thresholds (from `gate_tuner.get_overrides_for_niche(niche_id)`). The DB row persisted the correct `niche_id`, but the `auto_approval_confidence` field it carried was computed against the wrong tenant.

That last consequence is real but bounded. `gate_tuner` overrides are the calibration tuning system that hasn't produced meaningfully different per-niche thresholds yet (calibration data is thin for most niches). The practical downstream effect is small.

**Not applying a `NOT NULL` constraint** in this pass. `blueprints.niche_id` already has `NOT NULL DEFAULT ''::text` at schema level — pre-existing. `stories.niche_id` likewise. The write path is clean and enforced.

## Follow-ups (not acted on)

- `dashboard_events` write path — 143 rows with empty niche_id. Trace which caller wrote them; add niche tag if per-niche operator stats become important.
- `ensemble_votes` write path — 452 rows with empty niche_id. Ensemble decisions should carry tenant. AUTO #1 calibration analysis would benefit.
- `episodic_events` 41 orphan events — trace when niche context was missing at write time.

## Gate

```sql
SELECT COUNT(*) FROM blueprints
WHERE created_at >= NOW() - INTERVAL '24 hours'
  AND (niche_id IS NULL OR niche_id = '' OR niche_id = 'unknown');
```
Result: **0** — PASS.

## Commit

`test(db): quantify null-tenant rows across niche-scoped tables`
