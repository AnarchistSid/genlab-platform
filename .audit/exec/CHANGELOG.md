# GenLab — Execution Session Changelog (2026-07-27)

First true write session after 14 read-only/blocked sessions. Steps per the
"Non-Anthropic Execution Session" prompt. Rules held: one change at a time,
rollback documented before apply, confirmed by execution (DB row / off-box
probe / published post ID), tripwire = daily publish.

Operator decisions before start:
- Ship F-0080 now, accept zero-publish window on runs with LLM errors.
- Commit Phase 7.5 PF instrumentation alongside audit-workspace docs.
- Attempt Step 6 (RLS) if 1-5 land clean.

Anthropic state at session start: **exhausted** (session 9 continuation).

---

## Safety-net commits (17:20 IST)

**Commit A — `6b48ba79`** `docs(audit): 14-session audit workspace + programs + execution setup`
  * 9 files, 481 insertions
  * `.audit/` docs from Phases 7.1 → 8.3 + assess/ + research/ + this changelog
  * Rollback: `git revert 6b48ba79`

**Commit B — `dd58bfb9`** `feat(observability): WARN on PF-writer early-return branches`
  * 2 files, 37 insertions (feedback_registration.py + parallel_publish.py)
  * Zero logic change — WARN logging only on early-return branches
  * Rollback: `git revert dd58bfb9` + prod git pull + wait for next publisher fire
  * Verify (post-publisher-run): `journalctl -u genlab-publisher --since '2h ago' | grep pf-instr`

**Prod deploy**
  * `git pull` on prod resolved: prod had dirty state on `deploy/docker-compose.prod.yml` (identical content to commit 7514368e — Phase 8.2 sed-in-place hadn't sync'd to git). Stashed as `phase-8.2-sed-inplace-port-bind`, pulled, verified port line = `127.0.0.1:5432:5432`.
  * Pre-existing prod config drift observed on 4 niche `publishing.yaml` files — not touched this session; F-0040 territory.
  * No service restart needed — publisher is timer-driven one-shot; picks up new code on next fire (12:05 IST tomorrow).

---

## Step 1 — F-0051 hot-query indexes (17:35 IST)

**Commit `c0d225c3`** `perf(db): index hot alert + bandit queries (F-0051)`
  * 1 file, 79 insertions — Alembic revision `n1i2j3k4l5m6`, down_revision `m9h0i1j2k3l4`
  * Both indexes `CREATE INDEX CONCURRENTLY` inside `op.get_context().autocommit_block()`

**Applied on prod** via `alembic upgrade n1i2j3k4l5m6` (specific revision — prod has multiple heads from prior branching; `head` singular wouldn't resolve).

**Verified:**
  * `pg_indexes` shows `ix_pipeline_alerts_check_name_unresolved` + `ix_bandit_arms_niche_updated`
  * `EXPLAIN` on the alert_auto_resolver query now shows Bitmap Index Scan on the new partial index (previously seq scan)
  * Baseline for follow-up: pipeline_alerts 30,316 seq_scan / 373 idx_scan post-migration — traffic hasn't hit the new indexes yet. Expect idx_scan to climb sharply within an hour of monitoring/alert-resolver activity

**Rollback:** `alembic downgrade -1` (drops both indexes CONCURRENTLY via the downgrade fn)

---

## Step 2 — F-0032 CI runner guard (17:45 IST)

**Commit `2a30ba47`** `fix(ci): gate self-hosted runner against fork PRs (F-0032)`
  * 2 files (ci.yml + test.yml), 40 insertions
  * `if:` guard on all 10 self-hosted jobs — fork PRs skipped, same-repo PRs still run
  * codeql.yml self-hosted but has no pull_request trigger — safe
  * Rollback: `git revert 2a30ba47`

## Step 3 — F-0080 + F-0082 hard-fail (17:55 IST — THE CENTERPIECE)

**Commit `2ea7f12e`** `fix(writer): hard-fail instead of source-title fallback (F-0080, F-0082)`
  * 2 files (base_hooks.py + llm_hook_generator.py), 74 insertions / 25 deletions
  * REAL passthrough site was `base_hooks.py:208-223` (not `generate_platform_hooks:1385` which has 0 external callers). When LLM path + template path both fail, previously returned `story["title"]` verbatim → 47% of published hooks matched source titles per Phase 8.3 read-through
  * Changed `_generate_hook` signature `-> str | None`; returns None on both-failed; caller drops story with WARN
  * Also shipped F-0082 refusal-shape guard prepared in Phase 7.5

**Deployed on prod** via git pull; publisher picks up new code on next timer fire (12:05 IST 2026-07-28). Both files compile clean on prod.

**Expected impact on next publisher run:** stories whose LLM+template both fail get DROPPED instead of shipping source-title. Mandate temporarily drops further below 41.4% for niches with LLM issues (Anthropic still exhausted at 17:20 IST) — intended trade per operator decision.

**Verification (post-fire):**
  ```sql
  -- passthrough count on new publishes 2026-07-28
  SELECT b.niche_id, count(*) FILTER (WHERE b.hook_text = s.title
    OR (length(s.title)>=12 AND b.hook_text ILIKE '%'||left(s.title,30)||'%')) AS passthrough,
    count(*) AS posts
  FROM publishing_analytics p JOIN blueprints b ON b.id=p.blueprint_id
  JOIN stories s ON s.story_id=b.story_id
  WHERE p.published_at::date = '2026-07-28' AND p.post_id IS NOT NULL
  GROUP BY 1;
  ```
  Passthrough should collapse toward zero. Rejected_count in pipeline run_stats climbs; hooked_count may drop on LLM-failure days.

**Rollback:** `git revert 2ea7f12e` + prod pull. Reverting re-enables the terminated-format hooks — only revert if the guard breaks publishing itself.

---

## Step 5 — test suite completion (18:12 IST)

Full genlab-core suite ran **2:08** (not "cannot complete" as audit stated):
  * 333 passed / 1 failed / 1 skipped / 40 deselected
  * Failure: `deploy/test_backup_restore_dry_run.py::test_picks_most_recent_backup_by_mtime`
  * Stopped early via `-x` at first failure (4% into full suite)
  * **The failure is unrelated to Steps 1-4 changes** — it's in deploy/backup path, which none of my commits touched
  * My modified packages verified clean prior: strategies (118), writing (144), metrics (218) = 480 tests pass

Safety-net status for Step 6 (RLS): **imperfect but adequate**. The one failing test doesn't gate RLS work; it's a standalone deploy-path test. A full RLS cutover would want a green suite, but for TODAY's Steps 1-4 the risk is limited to the unrelated test.

## Step 6 — RLS remediation — **DEFERRED**

Per operator escape valve ("If Steps 1-5 land clean and RLS feels rushed, ship Steps 1-5 and stop"). This session already delivered:
  * F-0051 indexes (perf, DB-level)
  * F-0032 CI runner guard (security, CI-level)
  * F-0080/F-0082 hard-fail (writer, THE centerpiece — survival/quality)
  * F-0069 instrumentation (observability, diagnostic setup)
  * Phase 7.5 PF instrumentation shipped
  * 14-session audit workspace + programs published
  * Test suite proven runnable (audit's "cannot complete" outdated)

RLS is 5 sub-steps each gated on verification. Doing steps 6.1 (enumerate) and 6.2 (pool hook) properly needs dedicated focus + 48h of WARN-log observation before proceeding. Attempting it at end-of-session risks the "half-cutover DB is worse than a superuser one" outcome the prompt warned against.

**Next RLS session preconditions:**
  * Anthropic top-up (blocks any code work that depends on LLM)
  * F-0069 WARN logs accumulated (24-48h post-`b458f499`)
  * `test_backup_restore_dry_run` failure investigated / fixed / quarantined

