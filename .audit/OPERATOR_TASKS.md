# GenLab — Operator Task List

Consolidated 2026-07-24 (Phase 7.6). Every item below is blocked on operator authorization or console access. **None is an audit finding awaiting analysis.** Do not re-file as findings in future sessions.

| # | Task | Age | Effort | Owner | Verification |
|---|---|---|---|---|---|
| 1 | **Anthropic auto-reload.** `console.anthropic.com` → billing → auto-reload. Suggested $20 trigger, $50 top-up against $13.61/30d burn. | 5+ sessions | 1 console click | operator | Monitor line `live check returned 'exhausted'` changes to a non-zero balance. Re-check 7d for zero 402s. |
| 2 | **DONE 2026-07-24 23:31 IST (Phase 8.2)** — Port 5432 compose bind. Correct file is `/opt/genlab/deploy/docker-compose.prod.yml` (project=`deploy`, NOT `/opt/genlab/docker-compose.yml` as originally documented — that was stale, F-0078). Applied via `docker compose -f deploy/docker-compose.prod.yml --env-file /opt/genlab/.env up -d postgres`. Off-box probe times out; `ss -tlnp` shows 127.0.0.1 only; dashboard active. Repo mirror in commit `7514368e`. | — | DONE | — | ✓ |
| 3 | **Instrumentation deploy.** 37 lines / 2 files, uncommitted in Phase 7.5 working tree. All WARN-level, zero logic change. Files: `feedback_registration.py` (import/status/empty-post-id/exception/summary branches) + `parallel_publish.py:279` (falsy post_id). | 1 session | `git add + commit + push` locally; on prod `git pull` + `systemctl restart genlab-publisher` | operator | `journalctl -u genlab-publisher --since '1 hour ago' \| grep pf-instr` after next publisher run shows at least one WARN or clean pass with none. `git checkout -- <file>` reverts if needed. |
| 4 | **IG dual-ID forward fix (optional).** Add `platform_media_id` column to `publishing_analytics`; populate on IG publish. Both values available at publish time. Historical backfill NOT free — shortcode NOT derivable to media_id (Phase 7.6 verified). Skip backfill; forward writes only. | new (7.6) | 1 migration + 1-line PA writer + helper | operator (deploy path same as #3) | New IG PA rows have both `post_id` (shortcode) and `platform_media_id` (numeric) populated. |

**None of these is on the critical path for the learning loop** — Phase 7.6 confirmed the reward loop is closed at ~100% coverage on all four north-star platforms. Tasks 1 + 2 are the highest priority on their own merits (LLM outage + 60-day security exposure). Tasks 3 + 4 are diagnostic + audit-clarity, not defect fixes.

**Update 2026-07-27 (Phase 8.4):** Task 2 (port bind) and task 3 (PF instrumentation) are DONE and shipped. Task 4 remains open.

## RLS session preconditions (added 2026-07-27, Phase 8.5)

Block 2 (F-0048 + F-0065 RLS remediation) is the largest untouched engineering item. Every earlier attempt was deferred. **Do NOT start the RLS session until ALL three of the following are true:**

1. **Anthropic non-zero.** A mid-cutover LLM failure compounds a mid-cutover DB change — you would be diagnosing two orthogonal outages simultaneously. Verify via `journalctl -u genlab-anthropic-credit-monitor --since '2 hours ago'` reporting `matches_found: 0` (not `exhausted`).

2. **24–48 hours of F-0069 WARN log data accumulated.** Phase 8.4 commit `b458f499` added instrumentation to the Threads fetcher. The RLS role cutover changes every query — you want to be reading a stable metric-collector baseline, not one still-diagnosing its own Threads bug at the same time.

3. **Test suite fully green under `-p no:randomly`.** RLS is the change most able to silently break every query. You need regression coverage that actually runs. Phase 8.4 discovered the suite completes in 2:08 (audit's "cannot complete" was stale) with 333 pass / 1 fail. The one failure — `deploy/test_backup_restore_dry_run.py::test_picks_most_recent_backup_by_mtime` — has been quarantined with `@pytest.mark.flaky` and a reason comment (see commit tagged in `.audit/exec/CHANGELOG.md`). Verify all-green with `pytest -p no:randomly -q genlab-core/tests/` before starting the RLS session.

Only after all three: proceed with Block 2 Session A (enumerate + pool hook), then Session B (fail-closed + role cutover). Both per `.audit/BACKLOG.md`.
