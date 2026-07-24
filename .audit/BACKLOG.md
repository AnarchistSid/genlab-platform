# GenLab — Remaining Work Backlog

**This is a backlog, not a session.** Run one block at a time in a fresh Claude Code session at `/Users/anarchistsid/GenLab/`. Prepend the STATE + RULES preamble to each. Blocks are ordered by consequence-per-hour; the dependencies noted are real.

---

## PREAMBLE — prepend to every block

```
GenLab: 5-channel video automation platform, monorepo at ~190K Python source /
199K test lines. VPS `genlab-prod` 46.224.237.56, project root /opt/genlab.
Production PostgreSQL on port 5432 (Docker `genlab-postgres`) — do NOT query
5433, that is a different project. Channels: ai_creators, gaming, sports,
movies, anime.

A 9-phase audit is complete: `.audit/SCORECARD.md`, `.audit/findings.jsonl`
(76 findings), `.audit/phase7/DECISION.md` Rev 7, `.audit/OPERATOR_TASKS.md`.
Read the relevant findings before starting; do not re-derive them.

RULES
1. Evidence or silence. A change is confirmed by execution — a DB row, a log
   line, an off-box probe — never by config reading.
2. One change at a time. Rollback written before each change is applied.
3. Read-only against production unless the block says otherwise.
4. Never write a secret value to .audit/.
5. Stop condition: if the daily publish stops, roll back immediately and
   diagnose after. The pipeline running matters more than any item here.
6. No summary until every shell has exited.
```

---

# BLOCK 0 — Operator actions (5 minutes, no session)

1. **console.anthropic.com → Billing → auto-reload.** $20 trigger, $50 top-up against ~$13.61/30d burn. Gates every recovery path below.
2. **`docker-compose.yml`:** `"5432:5432"` → `"127.0.0.1:5432:5432"`, then `ssh genlab-prod 'cd /opt/genlab && docker compose up -d postgres'`. Verify with `nc -zv 46.224.237.56 5432` from off-box — must refuse. **Your own risk acceptance on this expires 2026-07-31.**
3. **Publish the audit workspace.** `.audit/` is gitignored today. After (2) lands and 5432 is loopback-only, the F-0024/F-0045/F-0048 chain in the findings describes closed holes rather than open ones, so pushing is safe. Sequence: verify 5432 refuses off-box → remove `.audit/` from `.gitignore` → `git add .audit/ .gitignore` → `git commit -m "docs(audit): publish 9-phase audit workspace"` → `git push`. Public-repo warning per F-0032: still worth a `grep -riE "password|secret|token|webhook" .audit/` sweep first — F-0030 (a Slack webhook leak) fired inside this workspace once and the operator should confirm it hasn't recurred.

Everything below assumes 1 and 2 are done. Item 3 is optional but recommended (traceability for future maintainers).

---

# BLOCK 1 — Make the test suite runnable (prerequisite for all code work)

```
9,451 tests collected; the full suite cannot complete. Phase 7 resolved the 15
visible failures to 2 real bugs plus 8 order-dependent flakes, but no full run
has ever finished and no publish-path coverage number exists. Every code change
in Blocks 2–5 would ship without regression testing.

1. Get one complete run.
   pytest --collect-only -q 2>&1 | tail -40
   pytest -p no:randomly --timeout=120 -q 2>&1 | tail -60
   dmesg | grep -i "killed process"       # OOM?
   Report: does it complete, how long, and the real pass/fail/skip counts.

2. Fix the 2 real bugs:
   - engagement/outbound targeting returns [] (two tests, one root cause)
   - test_refit_top_creator_priors: exits 0 when the API key is missing — a
     guard that does not guard. Its weekly timer records success either way.

3. Quarantine the 8 order-dependent flakes with an explicit marker and a reason;
   do not delete them. Then confirm the suite is green under -p no:randomly.

4. Produce publish-path coverage: every module a reel traverses from ingest to
   platform post ID.
     pytest --cov --cov-report=term-missing <publish path modules>
   That number, not aggregate coverage, is the one that matters.

Output: .audit/block1/RESULT.md — completion time, counts, coverage %, the two
bugs fixed with path:line.
```

---

# BLOCK 2 — RLS remediation (F-0049 + F-0065). Two sessions.

The largest untouched engineering item. Nine sessions of mandate arithmetic overshadowed it; only diagnosis exists.

### Session 2A — enumerate and instrument (read + small write)

```
Established: `genlab` role is rolsuper=t rolbypassrls=t. All 24 RLS policies use
current_setting('app.niche_id', true) in a fail-OPEN OR-chain — unset GUC
returns ALL rows, not zero, so the failure is invisible. tenant_context.py:5
documents 34 psycopg.connect bypass sites.

1. Enumerate every connection path and whether it sets app.niche_id:
   grep -rn "psycopg.connect\|get_conn\|pool" --include=*.py genlab-core/src
   grep -rn "app.niche_id\|set_config\|SET LOCAL" --include=*.py genlab-core/src
   Produce a table: path -> sets GUC (y/n) -> which niches it touches.

2. Add a pool-level hook that sets app.niche_id from the run context on
   connection acquire, so it cannot be forgotten per-query. This is the fix —
   the role swap alone remediates nothing.

3. Instrument: log WARN whenever a connection is acquired with no niche context.
   Deploy and run 48h. That log is the list of paths still unconverted.

Output: the path table, the hook shipped, 48h of WARN data pending.
```

### Session 2B — flip closed, then cut over (write, staged)

```
Preconditions: Block 2A's WARN log shows zero unconverted paths over 48h.

1. Flip policies fail-closed — drop the `IS NULL` permit clause. Doing this
   before step 2A completes turns every unset path into zero rows and breaks the
   pipeline.

2. Create the least-privilege role:
   CREATE ROLE genlab_app LOGIN PASSWORD '<generated, >24 chars>';
   GRANT USAGE ON SCHEMA public TO genlab_app;
   GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO genlab_app;
   GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO genlab_app;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public
     GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO genlab_app;
   -- NOT superuser, NOT bypassrls. Verify:
   SELECT rolname,rolsuper,rolbypassrls FROM pg_roles WHERE rolname='genlab_app';

3. Test in a transaction before any DSN change:
   BEGIN; SET LOCAL ROLE genlab_app; SET LOCAL app.niche_id='ai_creators';
   SELECT niche_id,count(*) FROM blueprints GROUP BY 1;  -- ai_creators ONLY
   ROLLBACK;

4. Cut over dashboard first (read-heavy, smallest blast radius), then
   collectors, then publisher LAST. Verify a full daily cycle with reels on all
   four platforms after each.

5. Keep `genlab` for migrations only, and rotate its 10-char password (F-0045).

Verification gate: rolsuper=f on the app role; cross-niche query returns
filtered rows; a full daily cycle completes. Until the last one, this is not done.
```

---

# BLOCK 3 — Security triage (one session)

```
1. CVEs — 66 across 15 packages, never triaged. Scan the ACTUAL project venv
   (Phase 1's scan hit pip-audit's own dependency tree):
     pip-audit --path <project venv> ; and separately on the VPS
   Triage by exploitability, not count. yt-dlp is on the publish path and
   processes untrusted remote content by design — start there. Report: how many
   are reachable from a live code path, and which have fixes available.

2. F-0032 — public repo + self-hosted GitHub Actions runner on the prod host.
     gh repo view --json visibility
     grep -rn "runs-on\|pull_request_target\|pull_request:" .github/workflows/
   GitHub's first-time-contributor gate covers first-timers only; one merged
   typo fix promotes an attacker to returning contributor. Decide: move the
   runner off prod, restrict triggers, or accept with a written rationale.

3. F-0056 — journald retention. 500 MB cap vacuums evidence in hours; it
   destroyed F-0047's root cause and invalidated the 30-day windows several
   findings assumed.
     ssh genlab-prod 'grep -vE "^#|^$" /etc/systemd/journald.conf; ls -la /var/log/journal'
   Set persistent storage with a sane SystemMaxUse and a retention period that
   survives a week. Also: alert dedup suppressed the Anthropic exhaustion for
   7 sessions ("unresolved alert already exists <24h"). Persistent CRITICALs
   should re-alert on a schedule, or dedup windows should reset on severity.

Output: CVE triage table, runner decision, journald config shipped.
```

---

# BLOCK 4 — Throughput defects (+18/wk, one or two sessions)

```
Preconditions: Block 0 done (empty Anthropic balance stalls scoring upstream),
Block 1 green.

Named clusters over 14 days:
  Layer-4 attribution gate  ~5   <- start here, likeliest to be your own logic
  IG container processing   ~5
  Meta 368 soft-block       ~5
  Threads container/timeout ~3
  CDN preflight             ~3
Plus F-0069 — Threads/FB metric collector stall at metric_collector.py:810.
IG and YT show zero stuck rows; find the branch they take that Threads and FB
do not.

One at a time, each with its own rollback and verification. Verify by:
  SELECT niche_id, count(DISTINCT blueprint_id) reels, count(*) posts,
         round(count(*)::numeric/nullif(count(DISTINCT blueprint_id),0),2) ppr
  FROM publishing_analytics
  WHERE published_at > now()-interval '7 days'
    AND status IN ('SUCCESS','INSIGHTS_6H','INSIGHTS_24H','INSIGHTS_48H','INSIGHTS_168H')
  GROUP BY 1;
Baseline platforms_per_reel = 3.05; target 4.0.

Also ship while in the code:
  - the 37 lines of prepared PF-writer instrumentation (Phase 7.5, uncommitted)
  - the per-fire counter on the auto-approver (Phase 8.1 STEP 2) — it is the
    only thing that can tell you whether lowering min_confidence is worth
    anything
  - the IG platform_media_id column (Phase 7.6) — retires the regex join that
    produced F-0071. Forward-only; the shortcode is not derivable from the
    media ID.
```

---

# BLOCK 5 — Code health and the kill list (one or two sessions)

```
1. push_to_backlog.py — CC=223 on a single `execute`, MI=0.0, 99 commits, on the
   live publish path. The worst single object in the repo. Read the commit
   subjects in order and state in one sentence what problem keeps being
   re-solved; that sentence should drive the refactor. Do not refactor without
   Block 1 green.

2. F-0051 — pipeline_alerts at 22k seq scans / 36M rows read; bandit_arms 9.6k /
   3.3M. Add the indexes; verify with pg_stat_user_tables before and after.

3. F-0054 — gaming source-title passthrough at 51.2%. The writer falls through
   to the source headline. Find the fallback path and give it a real generation
   step or a hard failure; publishing the source headline verbatim is close to
   the failure mode the video-first mandate exists to prevent.

4. The 107 silent-in-prod except sites — convert those on the publish path from
   logger.debug to WARN. Not all 107; the publish path only.

5. The kill list (.audit/kill_list.csv, 15 candidates) — verify each before
   deleting. Static reachability was never determined, so these are candidates,
   not confirmed-dead. Start with the 4 never-fired systemd units and
   _hooks_legacy.py. Also reclaim the 17 GB of local .tmp scratch and fix
   whatever retention should have reaped it.
```

---

# BLOCK 6 — Decisions (yours, not a session)

- **Auto-approval on gaming/anime/movies** — ~+12 posts/wk, reaches ~80% mandate. Priced at a 26% operator rejection rate: enabling it publishes roughly a quarter of content you currently reject. Cheap input first — classify 10 recently archived blueprints as "wrong niche fit" (recoverable by scoring) vs "would embarrass the brand" (not recoverable by config).
- **Movies content supply** — expand sources / lower the scoring threshold / cut its mandate from 7 to 3 reels a week / pause the channel.
- **Cadence and channel count** — deferred pending day-30 velocity data that does not exist yet. Do not decide these until Blocks 0 and 4 have run for 30 days.

---

# NOT RECOMMENDED — Phase 5, the subsystem deep read

Nine batches (pipeline stages, media/render, research/scoring, writing/LLM, publishing/storage, learning/monetization, the five niche packages, dashboard, scripts/tests) producing PURPOSE / REALITY / GAP / FAILURE MODE / VERDICT cards. It is the only planned phase never run, and the only one that would have judged whether the code is *good* rather than whether it *runs*.

**Skip it.** ~9 sessions to produce qualitative judgements about code the runtime evidence has already characterised, and the audit's marginal yield has been falling for four phases. If a specific subsystem later behaves in a way the findings do not explain, run that one batch then — not the set.
