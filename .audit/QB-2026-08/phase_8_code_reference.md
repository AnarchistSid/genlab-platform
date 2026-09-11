# Phase 8 code reference — authoritative learning-loop ground truth

Source: Explore agent dispatched during Phase 0 (before Phase 8 begins) to resolve the IG-metric contradiction between two prior subagents.

**Trust caveat:** this file summarises code state observed by an Explore agent; before any Phase 8 finding is finalised I MUST re-read the primary sources and quote them directly. Do not merge findings from this file into Phase 8 without verification.

---

## Key claims from the code-inspection agent (all to verify in Phase 8)

1. **`PostgresBackend` has belt-and-suspenders `AND niche_id = %s`** at ~line 741-743 of `storage/postgres.py`, applied when the table has `niche_id` in PROMOTED_COLUMNS. Rule #27 mitigation.

2. **Auto-approver `run_pass()` iterates all 5 niches** (`niches = list(NICHE_DIR_NAMES.keys())` on --niche all). No `if niche != 'ai_creators':` guards.

3. **Missing gate_examinations for 4 niches** must be one of:
   - `auto_publish.enabled: false` in publishing.yaml for those niches (early return at line 686-689)
   - No VISUAL_READY candidates in the DB
   - Policy disabled (`result.policy_disabled` set)

4. **IG fetcher** — needs primary-source verification in Phase 8. Prior agent contradiction unresolved by this pass either (agent claimed `total_interactions` IS requested; I need to see the API call).

5. **Reward formula BASE_WEIGHTS** — needs to be re-quoted from `reward_shaper.py` line 87-151.

6. **Sends-per-reach** — agent claims `retention_derivations.py` computes `(total_interactions - likes - comments - saves) / reach` but claims the output is consumed inside `transformation_reward_router` — needs verification of consumer.

7. **`analytics` table only ever gets `metric_type='composite'`** — this Phase 0 measured finding (F-QB-0005) means the raw per-metric data is discarded post-composite. Consumer path from MetricCollector to `storage.add()` must be traced.

**All above claims deferred to Phase 8 verification.**
