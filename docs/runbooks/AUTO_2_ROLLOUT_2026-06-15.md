# AUTO #2 Enforcement Rollout — Operator Runbook

> **Author**: planning agent, 2026-06-15
> **Audience**: solo operator (you), executing the 14-PR rollout starting tomorrow
> **Total wall-clock**: 3 active dev days + 5 observation days + Day-8 live flip
> **Read this in full BEFORE Day 1.** Reference it during execution.

---

## 0. About this document

This runbook is the single source of truth for shipping AUTO #2 enforcement on
the `ai_creators` (Blackbox Brief) niche. It covers 14 PRs across 3 ship days,
5 observation days, and a live flip on Day 8.

**Be humble.** Validation agents already surfaced 5 bugs in this code area
this week (S1, S6, S7, plus the calibration migration drift from PR #185 and
the rendering-layer audit). There are almost certainly more. Every step here
has a falsifiable check; if a check fails, **stop and read the failure mode
section** — do not improvise.

**The owner's vision** is autonomous publishing on 5 channels, 1 reel/channel/
day, no approval gate. This rollout proves the gate is trustworthy enough to
remove on **one** channel (`ai_creators`) first. Gaming/sports/movies/anime
follow only after `ai_creators` survives 7 days live without an operator
override.

### Conventions

| Token | Meaning |
|---|---|
| `prod` | Hetzner box `46.224.237.56` (root SSH) |
| `local` | `/Users/anarchistsid/GenLab` on your MacBook, `main` branch |
| `dashboard` | `https://review.aspirehub.ai` |
| `IST` | Indian Standard Time = UTC + 5:30 |
| `D1.x` | PR slug from the 14-PR plan |
| `gate` | `genlab_core.scheduling.auto_approval_gate.evaluate()` |
| `worker` | `genlab_core.scheduling.auto_approver.run_pass()` |
| `niche` | one of: `ai_creators`, `gaming`, `sports`, `movies`, `anime` |

### Hard rules during the rollout

| # | Rule | Why |
|---|---|---|
| R0 | Do **not** hand-edit prod source files during the rollout window | Causes the deploy-drift class of bug; see 2026-06-14 deep-dive |
| R1 | Do **not** consolidate `.env` files after token provisioning | Stale values overwrite fresh ones; see `feedback_token_ordering.md` |
| R2 | Do **not** start a PR if you cannot finish the post-merge checks the same day | Half-merged state confuses tomorrow-you |
| R3 | Do **not** skip the backfill validation (D1.5) — it is the gate to everything else | Bad calibration data poisons every downstream stat |
| R4 | Do **not** skip the 5-day observation window | The whole point is calibration-data-driven confidence |
| R5 | Do **not** flip Day-8 live if you have not slept | Live flip needs a clear head + 2 hours of operator availability |

---

## 1. Pre-flight (do this TONIGHT, before Day 1)

Target: 45 minutes. Do this once. If something fails here, **postpone Day 1
by 24 hours** — do not start a 14-PR rollout from a broken baseline.

### 1.1 Backups (10 min)

```bash
# SSH to prod
ssh root@46.224.237.56

# Backup the calibration table (it's small but it's about to get rewritten)
sudo -u postgres pg_dump -t auto_approval_calibration genlab \
  > /root/backups/aac_pre_rollout_$(date -u +%Y%m%d_%H%M).sql
ls -lh /root/backups/aac_pre_rollout_*.sql
# Expected: a file of at least a few KB (even with 0 useful rows it has schema)

# Backup the publishing schedule view of all blueprints scheduled in next 14 days
sudo -u postgres psql genlab -c "\copy (
  SELECT id, niche_id, status, scheduled_for, hook_text, created_at
  FROM blueprints
  WHERE scheduled_for IS NOT NULL
    AND scheduled_for >= NOW()
    AND scheduled_for < NOW() + INTERVAL '14 days'
  ORDER BY scheduled_for
) TO '/root/backups/scheduled_pre_rollout_$(date -u +%Y%m%d_%H%M).csv' WITH CSV HEADER"

# Backup the per-niche bandit arms snapshot (you'll need to compare after PR D3.8)
sudo -u postgres psql genlab -c "\copy (
  SELECT niche_id, arm_id, alpha, beta, total_pulls, last_updated
  FROM bandit_arms ORDER BY niche_id, arm_id
) TO '/root/backups/bandit_pre_rollout_$(date -u +%Y%m%d_%H%M).csv' WITH CSV HEADER"
```

**Signal**: 3 files exist under `/root/backups/`, each non-empty. If
`pg_dump` errors with "permission denied", run as `postgres`:
`sudo -u postgres pg_dump ...`.

### 1.2 Prod state snapshot (15 min)

```bash
# On prod
cd /opt/genlab
git status                       # MUST be clean — if not, see § 1.4
git log -1 --oneline             # Note SHA — this is your rollback target
git rev-parse HEAD > /root/backups/rollback_sha_$(date -u +%Y%m%d).txt
git branch --show-current        # Should be main
git remote -v                    # Should be origin → GitHub

# Migration version (this is the one that bit us on 2026-06-14)
sudo -u postgres psql genlab -c "SELECT version_num FROM alembic_version;"
# EXPECTED: o5j6k7l8m9n0 (or newer). If it shows n4i5j6k7l8m9 — the calibration
# table is missing on prod. STOP. Run PR #185's deploy script first.

# Calibration table exists?
sudo -u postgres psql genlab -c "\d auto_approval_calibration" | head -10
# EXPECTED: table definition prints. If "Did not find any relation" — STOP.

# Row count + spot check
sudo -u postgres psql genlab -c "
  SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE decided_at >= NOW() - INTERVAL '7 days') AS last_7d,
    COUNT(DISTINCT niche_id) AS niches,
    MIN(decided_at) AS first_row,
    MAX(decided_at) AS last_row
  FROM auto_approval_calibration;
"
# WRITE DOWN these numbers. D1.1 will purge synthetic rows; you'll
# need to compare before/after to know what survived.

# Pipeline health
systemctl list-timers | grep -E "publish|pipeline|engagement|warp"
systemctl status warp-svc        # Must be active — if not, fix THIS before anything else
curl -s --socks5 127.0.0.1:40000 https://ifconfig.me
# Expected: a Cloudflare IP, NOT the Hetzner box IP

# Disk + memory headroom (4GB box, this matters)
df -h /opt /mnt /tmp
free -h
# Expected: /opt >5GB free, /mnt >20GB free, RAM >500MB available

# Background worker count
ps aux | grep -E "auto_approver|run_pass|dramatiq" | grep -v grep
# Expected: NONE (worker doesn't exist yet — D3.9 ships it)
```

**Write all these numbers into a file** at `/root/backups/preflight_notes_$(date -u +%Y%m%d).md`.
You will refer to them on Day 8 and during any rollback.

### 1.3 Local repo state (10 min)

```bash
# On your laptop
cd /Users/anarchistsid/GenLab
git checkout main
git pull --rebase origin main
git status                        # Must be clean
git log -5 --oneline              # Note the head SHA
gh auth status                    # Must be authenticated to GitHub
gh pr list --state open --limit 10  # Note any open PRs — should be merged or closed before Day 1

# Dashboard build sanity
cd dashboard/frontend && npm run lint 2>&1 | tail -5
# Expected: clean (0 errors). If errors exist now, Day 2 UI work will be painful.

# Core tests
cd /Users/anarchistsid/GenLab/genlab-core && uv run pytest -x -q 2>&1 | tail -5
# Expected: all pass. If anything fails on `main`, fix it BEFORE starting the rollout.
```

### 1.4 Dashboard sanity (5 min)

Open `https://review.aspirehub.ai` and check:

| Surface | What to look for | If broken |
|---|---|---|
| Mission Control loads | All 5 niche cards render, no red error banners | Investigate before Day 1 |
| `CriticalAlertsBanner` | Either empty or showing real alerts (no JS errors in console) | Check `/api/v1/alerts/critical` |
| `AutoApprovalCalibrationCard` | 5 niche rows visible; sample counts present (likely all single digits) | Check `/api/v1/auto-approval/calibration-stats?niche_id=ai_creators` |
| Focus Review | At least 1 blueprint loads with `AutoApprovalBadge` showing | If badge missing, S-tier UI bug — defer Day 2 work |
| Browser console | No 500s on initial load | Anything else means rollout starts on broken base |

### 1.5 STOP conditions (do NOT start Day 1 if any of these are true)

1. `alembic_version` ≠ `o5j6k7l8m9n0` (or a known-later head)
2. `git status` on prod shows uncommitted modifications
3. WARP is dead (`systemctl status warp-svc` non-active)
4. Dashboard shows a 500 on initial Mission Control load
5. You're sick, sleep-deprived, or have <4 free hours tomorrow
6. There's an active prod incident in the last 24h that's unexplained

---

## 2. Day 1 — Cleanup + foundations (10:00 – 15:00 IST)

Day 1 ships 5 PRs. The middle one (D1.2 backfill) is the highest-risk
operation in the entire rollout. Plan: do D1.1 + D1.2 in the morning, take a
genuine 45-minute lunch break, then re-evaluate before D1.3.

| Step | PR | What | Est. time | Risk |
|---|---|---|---|---|
| 2.1 | D1.1 | Purge synthetic calibration rows | 30 min | Low |
| 2.2 | D1.2 | Backfill calibration from operator history | 90 min | **HIGH** |
| 2.3 | D1.5 | Validate backfill correctness | 30 min | Low (but gate) |
| 2.4 | — | **DECISION POINT**: continue or wait 24h? | 15 min | — |
| 2.5 | D1.3 | Lower viral threshold 0.05 → 0.02 | 45 min | Medium |
| 2.6 | D1.4 | DailySloBadge on Mission Control | 60 min | Low |

### 2.1 D1.1 — Purge synthetic calibration rows (10:00, 30 min)

**Goal**: Remove rows that were inserted by the test scaffold or by the
calibration logger's source-tag bug (S1 — not yet fixed). This shrinks the
table to "real operator clicks only" so D1.2 has a clean base to backfill on.

#### Before merging

```bash
cd /Users/anarchistsid/GenLab
git checkout main && git pull
gh pr view <D1.1-PR-number> --json title,additions,deletions,files,reviewDecision

# Diff sanity: should be a SQL migration that DELETEs rows where
# blueprint_id LIKE 'test_%' OR niche_id NOT IN (5 valid niches) — and that's it.
gh pr diff <D1.1-PR-number>
```

**Abort if** the diff touches anything outside `migrations/versions/` and
maybe `tests/`. A PR that "just purges synthetic rows" should be ~50 LOC.

#### Merge + deploy

```bash
gh pr merge <D1.1-PR-number> --squash --delete-branch
# Wait for CI green
gh run watch --exit-status

# Deploy to prod
ssh root@46.224.237.56
cd /opt/genlab && git pull --ff-only
cd /opt/genlab/genlab-core && sudo -u postgres alembic upgrade head
sudo -u postgres psql genlab -c "SELECT version_num FROM alembic_version;"
```

#### Signals (do all 4)

```sql
-- 1. Total rows shrank by 0-50 (depends on how many synthetic rows existed)
SELECT COUNT(*) FROM auto_approval_calibration;
-- Compare to your § 1.2 snapshot

-- 2. No more test rows
SELECT COUNT(*) FROM auto_approval_calibration
WHERE blueprint_id LIKE 'test_%' OR blueprint_id LIKE 'synthetic_%';
-- EXPECTED: 0

-- 3. All remaining rows reference real niches
SELECT DISTINCT niche_id FROM auto_approval_calibration
WHERE niche_id NOT IN ('ai_creators','gaming','sports','movies','anime');
-- EXPECTED: 0 rows

-- 4. Per-niche distribution looks plausible
SELECT niche_id, COUNT(*), MIN(decided_at), MAX(decided_at)
FROM auto_approval_calibration GROUP BY niche_id ORDER BY niche_id;
```

#### Rollback (if signals fail)

```bash
# Restore from preflight backup
sudo -u postgres psql genlab -c "TRUNCATE auto_approval_calibration;"
sudo -u postgres psql genlab < /root/backups/aac_pre_rollout_<TS>.sql
# Revert the merge
cd /Users/anarchistsid/GenLab
git revert <SQUASH-SHA> --no-edit && git push origin main
# STOP — investigate before continuing Day 1
```

### 2.2 D1.2 — Backfill calibration from operator history (10:45, 90 min)

> **This is the highest-risk step in the rollout.** A bad backfill loads
> thousands of rows with wrong `gate_approved`, wrong `decided_at`, or
> wrong `operator_action` — and every downstream stat ("agreement rate",
> "ready_for_enforcement", D2.7a's gate-flip logic) is built on this
> table. **Slow down here. Do every check.**

**Goal**: For every historical operator review action (last N days) where
the calibration logger DIDN'T fire (because of S1 source-tag bug, or
because the logger didn't exist yet), reconstruct what the gate WOULD have
said and write a calibration row. This is what unblocks the 30-sample
threshold for AUTO #2 readiness.

#### Pre-merge sanity (read the PR yourself, do NOT just trust CI)

```bash
gh pr view <D1.2-PR-number>
gh pr diff <D1.2-PR-number> | less
```

Things to verify in the diff with your own eyes:

1. **Dry-run mode exists.** The script MUST have a `--dry-run` flag that
   prints the row-count it would insert without inserting. If not, **block
   the PR and ask the dev to add one**.
2. **Idempotency**. Running the backfill twice MUST NOT double-insert.
   Look for `INSERT ... ON CONFLICT DO NOTHING` or an explicit `WHERE NOT
   EXISTS`. If neither — block.
3. **`decided_at` source**. The reconstructed `decided_at` MUST come from
   the blueprint's `updated_at` (when status flipped to `APPROVED` or
   similar), NOT from `NOW()`. A backfill with `NOW()` poisons the
   7-day-window stat for the next 7 days.
4. **`gate_approved` reconstruction**. The backfill MUST call
   `auto_approval_gate.evaluate()` on the historical blueprint state —
   NOT just assume `True`. If the dev hardcoded `True`, the table is
   useless.
5. **Operator action mapping**. Status `APPROVED` → `approved`. Anything
   else → `rejected`/`revised`/`skipped` based on which fields changed.
   The mapping must be a documented table in the PR description.

If any of those 5 are missing — **do not merge**. Push back, even if it
delays the day by 24 hours.

#### Merge + dry run first

```bash
gh pr merge <D1.2-PR-number> --squash --delete-branch
ssh root@46.224.237.56
cd /opt/genlab && git pull --ff-only

# DRY RUN — this MUST be the first execution
sudo -u postgres GENLAB_DB_HOST=localhost python -m genlab_core.tools.backfill_calibration \
  --niche all --since "$(date -u -d '90 days ago' +%Y-%m-%d)" --dry-run \
  2>&1 | tee /root/logs/backfill_dryrun_$(date -u +%Y%m%d_%H%M).log

# Look at the output before doing anything else
tail -40 /root/logs/backfill_dryrun_*.log
```

**Dry-run signals**:

| Signal | Healthy | Suspicious |
|---|---|---|
| Total rows to insert | 100 – 5,000 | <50 (something filtered too hard); >10,000 (something looped) |
| Per-niche distribution | All 5 niches present; ai_creators non-zero | One niche has 90% of rows; ai_creators has 0 |
| `decided_at` range | Spread over the last 90 days | All within last 24h (bug — using NOW()) |
| `gate_approved=True` rate | 30% – 80% | <10% or >95% (gate is mis-reconstructing) |
| Errors logged | 0 – a few "blueprint missing extra" warnings | Any stack traces, any "DB error" |

If anything looks off — **stop**, restore the dry-run log, and message the
dev. Do NOT proceed to the live insert.

#### Live insert

```bash
# Only if dry-run signals all green
sudo -u postgres GENLAB_DB_HOST=localhost python -m genlab_core.tools.backfill_calibration \
  --niche all --since "$(date -u -d '90 days ago' +%Y-%m-%d)" \
  2>&1 | tee /root/logs/backfill_live_$(date -u +%Y%m%d_%H%M).log

# Verify row count matches dry-run prediction (±5%)
sudo -u postgres psql genlab -c "SELECT COUNT(*) FROM auto_approval_calibration;"
```

#### Post-insert validation (do all 6)

```sql
-- 1. Row count growth matches dry-run prediction
SELECT COUNT(*) FROM auto_approval_calibration;
-- vs the dry-run output

-- 2. decided_at is spread over time, NOT clustered at NOW()
SELECT DATE_TRUNC('day', decided_at) AS day, COUNT(*)
FROM auto_approval_calibration
WHERE decided_at >= NOW() - INTERVAL '90 days'
GROUP BY 1 ORDER BY 1 DESC LIMIT 30;
-- EXPECTED: rows on many distinct days, NOT just today

-- 3. Per-niche sample counts are plausible
SELECT niche_id, COUNT(*) AS total,
       COUNT(*) FILTER (WHERE gate_approved IS TRUE) AS gate_yes,
       COUNT(*) FILTER (WHERE operator_action='approved') AS op_yes
FROM auto_approval_calibration
WHERE decided_at >= NOW() - INTERVAL '7 days'
GROUP BY niche_id ORDER BY niche_id;
-- EXPECTED: 5 niches, ai_creators with at least 20-30 rows

-- 4. Gate confidence distribution isn't degenerate
SELECT
  ROUND(MIN(gate_confidence)::numeric, 2) AS min,
  ROUND(AVG(gate_confidence)::numeric, 2) AS avg,
  ROUND(MAX(gate_confidence)::numeric, 2) AS max
FROM auto_approval_calibration WHERE gate_confidence IS NOT NULL;
-- EXPECTED: a real distribution, not all 0.0 or all 1.0

-- 5. Operator action distribution
SELECT operator_action, COUNT(*) FROM auto_approval_calibration GROUP BY 1;
-- EXPECTED: mostly 'approved' (operator approves more than rejects in prod),
-- with some 'rejected' / 'revised' / 'skipped' present

-- 6. No NULL niche_ids or blueprint_ids
SELECT COUNT(*) FROM auto_approval_calibration
WHERE niche_id IS NULL OR blueprint_id IS NULL OR niche_id = '' OR blueprint_id = '';
-- EXPECTED: 0
```

#### Rollback (if validation fails)

```bash
# Two options. Pick based on severity:

# Option A — partial: just delete rows from this backfill run
# Only works if you tagged the backfill rows with a unique marker (check PR)
sudo -u postgres psql genlab -c "
  DELETE FROM auto_approval_calibration
  WHERE id IN (SELECT id FROM auto_approval_calibration ORDER BY decided_at DESC LIMIT <COUNT>);
"

# Option B — nuclear: restore from preflight backup
sudo -u postgres psql genlab -c "TRUNCATE auto_approval_calibration;"
sudo -u postgres psql genlab < /root/backups/aac_pre_rollout_<TS>.sql

# Revert the merge
cd /Users/anarchistsid/GenLab
git revert <SQUASH-SHA> --no-edit && git push origin main
```

Then STOP the entire rollout for the day. Diagnose the backfill bug
tomorrow with a clear head.

### 2.3 D1.5 — Validate backfill correctness (12:45, 30 min)

This is a script PR (no schema change, no behavior change). Its job is to
run automated assertions on the post-backfill calibration table. If any
assertion fails, the entire rollout halts.

#### Merge + run

```bash
gh pr merge <D1.5-PR-number> --squash --delete-branch
ssh root@46.224.237.56
cd /opt/genlab && git pull --ff-only

sudo -u postgres GENLAB_DB_HOST=localhost python -m genlab_core.tools.validate_calibration \
  --niche all --strict 2>&1 | tee /root/logs/validate_calibration_$(date -u +%Y%m%d_%H%M).log
echo "Exit code: $?"
```

#### Signals

| Exit code | Meaning | Action |
|---|---|---|
| 0 | All assertions passed | Proceed to decision point (§ 2.4) |
| 1 | Soft fail (warnings only) | Read log; if only warnings about cold-start niches, proceed |
| 2+ | Hard fail | **STOP**. Rollback per § 2.2. Do NOT proceed to D1.3 |

### 2.4 Decision point — continue today or wait 24h? (13:15, 15 min)

**Default**: take lunch. Look at the numbers again at 14:00. Decide:

| If… | Then… |
|---|---|
| Backfill rows ≤ 200 AND ai_creators has ≥30 samples | Continue to D1.3 today |
| Backfill rows 200–2,000 AND ai_creators ≥30 samples | Continue, but skip D1.4 (UI) and ship it tomorrow |
| Backfill rows >2,000 | **Wait 24 hours.** Watch the calibration card for a day; a giant backfill changes every stat at once and you want to see the new baseline before stacking D1.3 (which changes thresholds) on top |
| ai_creators has <30 samples post-backfill | Continue D1.3 (threshold lowering will help), but expect ai_creators to NOT be ready_for_enforcement by Day 8 |
| You feel uneasy about ANYTHING | Wait 24h. The rollout doesn't care about a 1-day slip |

### 2.5 D1.3 — Lower viral threshold (14:30, 45 min)

**Goal**: Lower `_DEFAULT_MIN_VIRALITY_SCORE` from 0.05 to 0.02 in
`auto_approval_gate.py`. The 0.05 floor was set off a single 2026-06-13
probe and is too strict — multiple deep-dives showed it rejects healthy
blueprints in cold-start niches.

> **WARNING**: lowering this threshold AFTER the backfill is intentional.
> The backfill ran with the OLD threshold (0.05), so historical
> `gate_approved` values reflect the old strictness. Going forward,
> calibration rows will reflect the NEW threshold. **Stats over
> overlapping windows are noisy for ~24h after this PR ships.** This is
> expected; do not panic if agreement-rate dips temporarily.

#### Merge + deploy

```bash
gh pr view <D1.3-PR-number>           # one-line constant change
gh pr diff <D1.3-PR-number>            # should touch auto_approval_gate.py + a test
gh pr merge <D1.3-PR-number> --squash --delete-branch
gh run watch --exit-status

ssh root@46.224.237.56
cd /opt/genlab && git pull --ff-only

# No restart needed — the gate is imported per-request by the dashboard.
# But verify the new threshold is live:
sudo -u postgres GENLAB_DB_HOST=localhost python -c "
from genlab_core.scheduling.auto_approval_gate import _DEFAULT_MIN_VIRALITY_SCORE
print(f'min_virality_score = {_DEFAULT_MIN_VIRALITY_SCORE}')
"
# EXPECTED: 0.02
```

#### Signals (over the next 30 min)

```bash
# Hit the preview endpoint for an existing VISUAL_READY blueprint
curl -s https://review.aspirehub.ai/api/v1/blueprints/<BP_ID>/auto-approval-preview | jq

# In the calibration card on Mission Control:
# - Sample count should NOT jump (no new rows yet — operator hasn't clicked anything)
# - Agreement rate may flicker as next few operator clicks land — expected
```

#### Rollback

```bash
git revert <SQUASH-SHA> --no-edit && git push origin main
# Wait for CI, then `git pull` on prod
# No data corruption — the constant just flips back
```

### 2.6 D1.4 — DailySloBadge on Mission Control (15:00, 60 min)

**Goal**: Surface "X / 5 reels published today" in the top bar of Mission
Control so the operator sees the daily SLO state at a glance.

#### Pre-merge

```bash
gh pr view <D1.4-PR-number>
gh pr diff <D1.4-PR-number>
```

Verify in the diff:

1. New component lives at `dashboard/frontend/src/views/mission-control/DailySloBadge.tsx`
2. Mounted ONCE in `MissionControl.tsx` (grep for `<DailySloBadge`)
3. Uses an EXISTING endpoint (probably `/api/v1/publishing/today-summary`) — no new backend
4. Has a Vitest snapshot or a render test

#### Merge + verify

```bash
gh pr merge <D1.4-PR-number> --squash --delete-branch
gh run watch --exit-status

# Frontend redeploys via dashboard CI — wait for the deploy to land
# Then hard-refresh the dashboard (Cmd+Shift+R) and look at top bar
```

**Signals**:

| Surface | Healthy | Broken |
|---|---|---|
| Top bar of Mission Control | Badge visible, shows "X / 5" where X is today's published count | Badge missing OR shows "?/?" OR grid layout broken |
| Browser dev console | No 4xx/5xx | Any error from the badge's endpoint |
| Mobile width (resize to 375px) | Badge collapses gracefully | Pushes other elements off-screen |

#### Rollback

```bash
git revert <SQUASH-SHA> --no-edit && git push origin main
# Dashboard auto-redeploys on next CI run
```

### Day 1 end-of-day checklist

- [ ] 5 PRs merged, each with green CI
- [ ] `alembic_version` on prod = current head
- [ ] Calibration table row count matches expectations
- [ ] D1.5 validator exited 0
- [ ] DailySloBadge visible on dashboard
- [ ] No 5xx alerts in `pipeline_alerts` from the last 4 hours
- [ ] Backups from § 1.1 still present at `/root/backups/`
- [ ] You've written a 1-paragraph EOD note to yourself at `/root/logs/day1_eod.md`

---

## 3. Day 2 — Diagnostics, gate logic, bug fixes (10:00 – 17:00 IST)

**Recommendation: split Day 2 across 2 days.**

6 PRs in one day is too many — especially because D2.7a is the make-or-
break gate-logic PR that needs careful review. Recommended split:

| Sub-day | PRs | Why |
|---|---|---|
| Day 2a (10–14 IST) | S1 + S7 + D3.10 + S6 | Easy/independent. Get them in early so Day 2b can focus on D2.7a |
| Day 2b (10–16 IST, next day) | D2.5 + D2.6 + **D2.7a** | D2.7a is the gate-logic change; the diagnostics in D2.5/D2.6 should land first so you can SEE what D2.7a does |

If you ignore the split recommendation, you can ship all 6 today — but
shift Day 3 to Day 4 and the live flip from Day 8 to Day 9. **The
calendar is yours; the dependency order is not.**

### Day 2 PRs

| Step | PR | What | Est. time | Risk |
|---|---|---|---|---|
| 3.1 | S1 | calibration_logger source-tag filter | 30 min | Low |
| 3.2 | S7 | BlackboxBrief publishing.yaml (file create) | 20 min | Low |
| 3.3 | D3.10 | Kill switch button on Mission Control | 45 min | Low |
| 3.4 | S6 | bandit_arms snapshot job | 30 min | Low |
| 3.5 | D2.5 | Bandit residual diagnostic | 45 min | Low |
| 3.6 | D2.6 | ScoringExplainer UI | 60 min | Low |
| 3.7 | **D2.7a** | **Strategy B+E gate code** | **120 min** | **HIGH** |

### 3.1 S1 — calibration_logger source-tag filter (30 min)

**Goal**: Fix the bug where calibration_logger writes rows for non-
operator review actions (auto-archives, system-driven status flips,
etc.). Without this fix, the calibration table will keep growing with
junk and D1.2's backfill effort gets diluted.

#### Pre-merge

```bash
gh pr diff <S1-PR-number>
```

Verify: the change is in `genlab_core/scheduling/calibration_logger.py`
`log()` function. It should add a `source` parameter (default
`"operator"`), and the function should early-return when `source !=
"operator"`. Test should cover both branches.

#### Merge + deploy

```bash
gh pr merge <S1-PR-number> --squash --delete-branch
gh run watch --exit-status
ssh root@46.224.237.56 'cd /opt/genlab && git pull --ff-only'
# Dashboard server picks up on next gunicorn worker recycle (within minutes)
```

#### Signal

```sql
-- Watch this over the next 60 min. The table should ONLY grow when an
-- operator clicks something on the dashboard. If it grows without an
-- operator action, the filter isn't working.
SELECT decided_at, blueprint_id, niche_id, operator_action
FROM auto_approval_calibration
WHERE decided_at >= NOW() - INTERVAL '1 hour'
ORDER BY decided_at DESC LIMIT 20;
```

#### Rollback: standard `git revert`.

### 3.2 S7 — BlackboxBrief publishing.yaml (20 min)

**Goal**: Create the file `BlackboxBrief/config/publishing.yaml` because
the auto_approver worker looks for it (line 130 of `auto_approver.py`)
and silently no-ops when it's missing.

#### Pre-merge

```bash
gh pr diff <S7-PR-number>
```

Verify the file content. It MUST have:

```yaml
auto_publish:
  enabled: false        # ← starts off; flips true on Day 8
  min_confidence: 0.85
  max_approvals_per_pass: 1
```

If `enabled: true` ships in this PR — **block and reject**. The flip is
a Day-8 manual step, not a Day-2 code commit.

#### Merge + deploy

```bash
gh pr merge <S7-PR-number> --squash --delete-branch
ssh root@46.224.237.56 'cd /opt/genlab && git pull --ff-only && cat BlackboxBrief/config/publishing.yaml'
```

#### Signal

```bash
ssh root@46.224.237.56 'cd /opt/genlab && sudo -u postgres GENLAB_DB_HOST=localhost python -c "
from genlab_core.scheduling.auto_approver import _load_policy
p = _load_policy(\"ai_creators\")
print(f\"enabled={p.enabled}, min_confidence={p.min_confidence}, max_per_pass={p.max_approvals_per_pass}\")
"'
# EXPECTED: enabled=False, min_confidence=0.85, max_per_pass=1
```

### 3.3 D3.10 — Kill switch button on Mission Control (45 min)

**Goal**: Add a big red "DISABLE AUTO-APPROVE GLOBALLY" button at the top
of Mission Control. Clicking it sets `GENLAB_AUTO_APPROVE_DISABLED=1` in
the worker's environment (via a config file the worker reads on each
pass). This is the panic button the operator hits if AUTO #2 misbehaves
live.

#### Pre-merge

```bash
gh pr diff <D3.10-PR-number>
```

Verify:
1. Button is RED, top of page, hard to miss
2. Click triggers a POST to `/api/v1/auto-approval/kill-switch` (or
   similar). Backend writes a file or env-config.
3. UI shows current state ("ENABLED" vs "DISABLED globally")
4. Requires a confirm dialog ("Are you sure? This stops auto-approval
   on ALL niches.")

#### Merge + smoke test

```bash
gh pr merge <D3.10-PR-number> --squash --delete-branch
# After dashboard redeploys, hit the button in dev:
# 1. Click kill switch → confirm dialog → confirm
# 2. Verify the API responds 200
# 3. Verify the button now shows "DISABLED"
# 4. Click again → re-enable
# 5. Verify state restores
```

> **Important**: until the worker exists (D3.9), this button does nothing
> functionally. That's fine. We want the button IN PLACE before the
> worker ships, not after.

### 3.4 S6 — bandit_arms snapshot job (30 min)

**Goal**: Daily snapshot of `bandit_arms` table to a CSV in `/mnt/genlab-
media/snapshots/`. This is the audit trail for "did D3.8 (per-platform
multipliers) depress an arm permanently?"

#### Pre-merge

Verify:
1. Adds a systemd timer (or cron) firing at 03:00 IST daily
2. Writes to `/mnt/genlab-media/snapshots/bandit_arms_YYYYMMDD.csv`
3. Rotates: keeps last 30 days

#### Merge + verify

```bash
gh pr merge <S6-PR-number> --squash --delete-branch
ssh root@46.224.237.56 'cd /opt/genlab && git pull --ff-only && systemctl daemon-reload && systemctl enable --now bandit-snapshot.timer'

# Trigger manually once to verify it works
ssh root@46.224.237.56 'systemctl start bandit-snapshot.service && sleep 5 && ls -lh /mnt/genlab-media/snapshots/'
# EXPECTED: today's CSV exists, non-empty
```

### 3.5 D2.5 — Bandit residual diagnostic (45 min)

**Goal**: SQL/report tool that lists arms where `(observed_reward -
predicted_reward)` is large in absolute value. Surfaces "the bandit
thinks this arm is great but observed performance disagrees".

#### Merge + run

```bash
gh pr merge <D2.5-PR-number> --squash --delete-branch
ssh root@46.224.237.56 'cd /opt/genlab && git pull --ff-only && sudo -u postgres GENLAB_DB_HOST=localhost python -m genlab_core.tools.bandit_residuals --niche all'
# Expected: a table of (niche, arm_id, predicted, observed, residual). Save
# the output to /root/logs/bandit_residuals_baseline.txt — you'll diff against
# this after D3.8 ships.
```

### 3.6 D2.6 — ScoringExplainer UI (60 min)

**Goal**: New panel on the blueprint detail view that breaks down how
`composite_score` was computed (per-feature contribution). Operator sees
WHY a blueprint scored 0.4 vs 0.8.

#### Merge + smoke test

```bash
gh pr merge <D2.6-PR-number> --squash --delete-branch
# After dashboard redeploys:
# Open a VISUAL_READY blueprint in Focus Review → look for new panel
# Verify the breakdown numbers sum approximately to the composite_score
```

### 3.7 D2.7a — Strategy B+E gate code (120 min, make-or-break)

> **This is the highest-risk code change in the rollout.** This PR
> modifies the gate's decision logic (Strategy B + Strategy E — read the
> PR description for the spec). A bug here makes the gate either too
> strict (auto-approves nothing) or too loose (auto-approves garbage).
> The Day-8 live flip depends on this being correct. **Read this PR
> twice. Run its tests locally. Do not merge before lunch.**

#### Pre-merge — full local verification

```bash
cd /Users/anarchistsid/GenLab
gh pr checkout <D2.7a-PR-number>

# Run the gate tests
cd genlab-core
uv run pytest tests/scheduling/test_auto_approval_gate.py -v 2>&1 | tail -30
# EXPECTED: all green. If any test is skipped, investigate why.

# Run the calibration tests too — gate changes can break calibration assumptions
uv run pytest tests/scheduling/test_calibration_logger.py -v 2>&1 | tail -10

# Hand-trace the new logic on 5 real blueprints
sudo -u postgres GENLAB_DB_HOST=46.224.237.56 python -c "
from genlab_core.scheduling.auto_approval_gate import evaluate
import json, subprocess
# Pull 5 real ai_creators blueprints from prod
out = subprocess.check_output(['ssh','root@46.224.237.56','sudo -u postgres psql genlab -t -c \"SELECT row_to_json(b) FROM blueprints b WHERE niche_id=\\'ai_creators\\' AND status=\\'VISUAL_READY\\' LIMIT 5\"'])
for line in out.decode().strip().split('\n'):
    if not line.strip(): continue
    bp = json.loads(line)
    d = evaluate(bp)
    print(f'{bp[\"id\"][:8]}: approved={d.approved} conf={d.confidence:.2f} failed={d.failed_checks}')
"
```

#### Pre-merge — read the diff with intent

Look for:
1. **No new untested code paths.** Every new `if` branch should have a test.
2. **No silent fallbacks.** A `try/except: pass` that swallows the gate's
   error is a footgun.
3. **Default thresholds preserved.** This PR should add new strategies,
   not change the existing thresholds (D1.3 did that).
4. **Documented in the PR body.** The PR description must explain WHY
   Strategy B + Strategy E (and why not C/D/F). If it doesn't, push back.

#### Merge + deploy

```bash
gh pr merge <D2.7a-PR-number> --squash --delete-branch
gh run watch --exit-status
ssh root@46.224.237.56 'cd /opt/genlab && git pull --ff-only'

# Restart dashboard gunicorn so the gate code reloads
ssh root@46.224.237.56 'systemctl restart genlab-dashboard'
sleep 5
curl -sI https://review.aspirehub.ai/api/v1/health
# EXPECTED: 200
```

#### Signals — watch for 60 min

```sql
-- Watch the next 5 operator clicks on ai_creators
SELECT decided_at, blueprint_id, gate_approved, gate_confidence,
       operator_action,
       (gate_approved IS TRUE) = (operator_action = 'approved') AS agrees
FROM auto_approval_calibration
WHERE niche_id = 'ai_creators' AND decided_at >= NOW() - INTERVAL '1 hour'
ORDER BY decided_at DESC LIMIT 5;
-- EXPECTED: a mix of agree=true and agree=false. If all rows show
-- agree=false → the gate is mis-aligned with the operator — push back the
-- Day-8 flip until you understand why.

-- Check the agreement rate hasn't crashed
SELECT
  ROUND(100.0 * COUNT(*) FILTER (
    WHERE (gate_approved IS TRUE) = (operator_action = 'approved')
  ) / NULLIF(COUNT(*), 0), 1) AS agreement_pct,
  COUNT(*) AS samples
FROM auto_approval_calibration
WHERE niche_id = 'ai_creators' AND decided_at >= NOW() - INTERVAL '7 days';
```

If agreement_pct drops by >15 percentage points from where it was at end
of Day 1 — **revert D2.7a** and rethink.

#### Rollback

```bash
# Standard revert
git revert <SQUASH-SHA> --no-edit && git push origin main
gh run watch --exit-status
ssh root@46.224.237.56 'cd /opt/genlab && git pull --ff-only && systemctl restart genlab-dashboard'
# Calibration rows written under bad gate logic stay — they'll show as
# disagreement, which is fine; they age out of the 7-day window naturally.
```

### Day 2 EOD checklist

- [ ] All 6 (or 4 if split) PRs merged with green CI
- [ ] S7 created the publishing.yaml file with `enabled: false`
- [ ] S1 source-tag filter verified live (no junk calibration rows)
- [ ] Kill switch button visible on Mission Control
- [ ] D2.7a gate change deployed AND agreement rate didn't crash
- [ ] Bandit residuals baseline saved to `/root/logs/`
- [ ] No 5xx alerts in last 4h

---

## 4. Day 3 — Bandit + worker dry-run (10:00 – 15:00 IST)

| Step | PR | What | Est. time | Risk |
|---|---|---|---|---|
| 4.1 | D3.8 | Per-platform reward multipliers | 60 min | Medium |
| 4.2 | D3.9 | AUTO #2 dry-run worker for ai_creators | 90 min | Medium |
| 4.3 | — | Start observation window | — | — |

### 4.1 D3.8 — Per-platform reward multipliers (60 min)

**Goal**: Each platform's reward gets a multiplier (e.g. YouTube views
weight more than X retweets, because YouTube traffic is more valuable
for monetization). The bandit's reward function uses these multipliers.

> **Risk**: a bad multiplier permanently depresses an arm. The
> snapshots from S6 are your safety net.

#### Pre-merge

```bash
gh pr diff <D3.8-PR-number>
```

Verify:
1. Multipliers live in a config file (YAML), not hardcoded
2. Multipliers are documented (a comment or PR body explaining the
   weights chosen)
3. There's a unit test verifying the reward math
4. There's a BACKWARDS-COMPAT path — old rewards in
   `publishing_analytics` aren't retroactively multiplied (only new
   rewards going forward)

#### Merge + verify

```bash
gh pr merge <D3.8-PR-number> --squash --delete-branch
ssh root@46.224.237.56 'cd /opt/genlab && git pull --ff-only'

# Take a fresh bandit snapshot immediately
ssh root@46.224.237.56 'systemctl start bandit-snapshot.service && cp /mnt/genlab-media/snapshots/bandit_arms_$(date -u +%Y%m%d).csv /root/backups/bandit_post_d38.csv'

# Wait 24h before deciding if D3.8 is healthy. Then compare:
# ssh root@46.224.237.56 'diff /root/backups/bandit_pre_rollout_*.csv /root/backups/bandit_post_d38.csv'
```

### 4.2 D3.9 — AUTO #2 dry-run worker for ai_creators (90 min)

**Goal**: Background worker that runs the AUTO #2 logic in
**observation-only mode** — it logs what it WOULD approve but doesn't
actually approve anything. This is the 5-day dry-run that validates the
gate before the Day-8 live flip.

#### Pre-merge

```bash
gh pr diff <D3.9-PR-number>
```

Verify:
1. Worker reads `publishing.yaml` and respects `enabled: false` (no-op)
2. There's a separate `--dry-run` flag that overrides `enabled: true`
   (so we can dry-run on niches that have `enabled: true` once
   ai_creators flips Day-8 and we're prepping gaming)
3. Worker writes structured logs with `event="auto_approval_dryrun"`
4. Worker writes to a dedicated dry-run audit table OR to a logfile
   that's retained for 30 days
5. Worker has a systemd timer (NOT cron — we want journalctl visibility)
6. Timer interval is NOT shorter than 15 min (don't hammer the DB)

#### Merge + deploy + start dry-run

```bash
gh pr merge <D3.9-PR-number> --squash --delete-branch
ssh root@46.224.237.56 'cd /opt/genlab && git pull --ff-only'

# Install the timer
ssh root@46.224.237.56 'systemctl daemon-reload && systemctl enable --now auto-approver-dryrun.timer'
ssh root@46.224.237.56 'systemctl list-timers | grep auto-approver'

# Manually trigger first run
ssh root@46.224.237.56 'systemctl start auto-approver-dryrun.service'
sleep 5
ssh root@46.224.237.56 'journalctl -u auto-approver-dryrun.service -n 30 --no-pager'
```

#### Signals

| Signal | Healthy | Suspicious |
|---|---|---|
| Worker logs `policy disabled` for all niches except ai_creators | ✓ | If logs other niches as "would-approve" — bug, S7 missing for other niches |
| Worker logs `would_approve` count for ai_creators between 0-3 per pass | ✓ | Worker would-approves >3 per pass — too aggressive, lower `max_approvals_per_pass` |
| Worker doesn't crash on any iteration | ✓ | Any traceback in journalctl |
| No new rows in `blueprints` show status=APPROVED with `approved_by='auto'` | ✓ (worker is dry-run!) | If you see auto-approved blueprints — STOP the timer immediately |

```bash
# Sanity SQL: verify dry-run hasn't actually approved anything
sudo -u postgres psql genlab -c "
SELECT COUNT(*) FROM blueprints
WHERE niche_id='ai_creators'
  AND status IN ('APPROVED','SCHEDULED','PUBLISHED')
  AND updated_at >= NOW() - INTERVAL '1 hour'
  AND approved_by = 'auto';
"
-- EXPECTED: 0
```

If the count is >0 — **kill the timer immediately**:
`systemctl stop auto-approver-dryrun.timer && systemctl disable auto-approver-dryrun.timer`,
revert D3.9, and diagnose.

### 4.3 Start observation window

After D3.9 is verified healthy:

- Note the time you started the dry-run timer:
  `echo "DRY-RUN START: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> /root/logs/auto2_rollout.log`
- Day 8 = Day 3 + 5 calendar days from this timestamp.
- Set a calendar reminder for the live-flip checklist (§ 8).

---

## 5. Day 3–8 — Observation period (5 days)

**Your job for 5 days**: don't ship anything new. Watch.

### 5.1 Daily 10-minute morning check (do this at 09:00 IST every day)

```bash
# 1. Worker liveness
ssh root@46.224.237.56 'systemctl status auto-approver-dryrun.timer auto-approver-dryrun.service'
# EXPECTED: timer "active (waiting)", service "inactive (dead)" between runs

# 2. Most recent worker run
ssh root@46.224.237.56 'journalctl -u auto-approver-dryrun.service -n 50 --since "12 hours ago" --no-pager'
# Look for: "would_approve" counts, no tracebacks

# 3. Calibration health
ssh root@46.224.237.56 'sudo -u postgres psql genlab -c "
SELECT niche_id, COUNT(*) AS samples_7d,
       ROUND(100.0 * COUNT(*) FILTER (
         WHERE (gate_approved IS TRUE) = (operator_action = \"approved\")
       ) / NULLIF(COUNT(*), 0), 1) AS agreement_pct,
       MAX(decided_at) AS last_review
FROM auto_approval_calibration
WHERE decided_at >= NOW() - INTERVAL \"7 days\"
GROUP BY niche_id ORDER BY niche_id;
"'

# 4. Dry-run vs operator alignment for ai_creators
ssh root@46.224.237.56 'sudo -u postgres psql genlab -c "
SELECT
  COUNT(*) FILTER (WHERE dryrun_decision = operator_action) AS aligned,
  COUNT(*) FILTER (WHERE dryrun_decision != operator_action) AS divergent
FROM auto_approval_dryrun_log
WHERE niche_id = \"ai_creators\" AND created_at >= NOW() - INTERVAL \"24 hours\";
"'
# (table name depends on what D3.9 actually shipped — adjust)

# 5. Daily SLO state (look at dashboard)
# Open https://review.aspirehub.ai → look at DailySloBadge top-bar
# EXPECTED: 5/5 by EOD IST
```

### 5.2 What's healthy

| Metric | Healthy range | Red flag |
|---|---|---|
| ai_creators sample count | grows by 1-3/day | flat (operator not reviewing) OR jumps by 20+ (bug) |
| ai_creators agreement pct | 75% – 95% | <70% (gate misaligned) OR exactly 100% (gate too permissive — every operator review approves) |
| Worker runs per day | matches timer interval (e.g. 96 if every 15min) | gaps >2h (timer dead) |
| `would_approve` count per pass | 0-3 | >3 (lower max_per_pass) |
| Disk free | >5GB on /opt | <2GB (clean .tmp) |
| Critical alerts | 0 | any (investigate before continuing) |
| Daily SLO | 5/5 published by EOD IST | any niche stuck at 0 or skipped |

### 5.3 Red flags — abort the Day-8 flip if you see any

1. **Worker silently no-ops for >24h** — usually means S7 publishing.yaml
   was edited or the file is unparseable. `grep enabled /opt/genlab/BlackboxBrief/config/publishing.yaml`
2. **Agreement rate drops >10 percentage points in a single day** — gate
   logic has a bug. Check if D2.7a introduced a regression.
3. **`would_approve` count explodes** — gate is too permissive, possibly
   because D1.3 threshold change combined with D2.7a strategy change.
4. **Any blueprint shows `approved_by='auto'`** — dry-run mode is broken;
   kill the timer immediately.
5. **Calibration rows appear with `source != 'operator'`** — S1 filter
   regressed.
6. **WARP outage** (see § 7.8 below).

### 5.4 Re-evaluate previously-rejected PRs

The session that produced the 14-PR plan deferred a few PRs because of
external blockers. Check during this window if any unblock:

| Deferred PR | Unblocks when… | Check daily |
|---|---|---|
| Threads engagement poller revival | All 5 Threads tokens re-provisioned (see PR #198) | `ls /var/lib/genlab/threads_tokens/*.json` |
| YT engagement poller revival | 5 YT channel ID env vars set | `grep YOUTUBE_CHANNEL_ID /etc/genlab/.env \| wc -l` (target: 5) |
| Twitter engagement poller revival | Operator decision on Twitter API tier | none |
| DPO trainer revival | preference_data table has >100 rows | `psql -c "SELECT COUNT(*) FROM preference_data;"` |

If anything unblocks during the observation window — **do NOT ship it
during the window**. Note it in `/root/logs/auto2_rollout.log` and ship
after Day 8 is stable.

### 5.5 Bookmarks for the operator

| URL | What |
|---|---|
| `https://review.aspirehub.ai/mission-control` | Top-level health |
| `https://review.aspirehub.ai/api/v1/auto-approval/calibration-stats?niche_id=ai_creators` | Raw stats JSON |
| `https://review.aspirehub.ai/focus-review?niche=ai_creators` | Where you'll review ai_creators blueprints |
| `https://review.aspirehub.ai/bulk-review?niche=ai_creators` | Faster review surface (from PR #192) |

---

## 6. Day 8 — The live flip

> **Do this only between 09:00 and 13:00 IST.** Publisher fires at
> 12:05 IST; you need to be present for at least 2 hours before AND
> after the first auto-approved post would publish.

### 6.1 Pre-flip checklist — ALL must be ✓

See § 9 — the readiness checklist is its own section. Do not proceed
without ticking every box.

### 6.2 The flip itself (09:30 IST, 15 min)

```bash
# 1. Take a fresh snapshot of everything
ssh root@46.224.237.56
sudo -u postgres pg_dump -t auto_approval_calibration genlab > /root/backups/aac_pre_flip_$(date -u +%Y%m%d_%H%M).sql
sudo -u postgres pg_dump -t blueprints genlab > /root/backups/bp_pre_flip_$(date -u +%Y%m%d_%H%M).sql
cp /opt/genlab/BlackboxBrief/config/publishing.yaml /root/backups/publishing_ai_creators_pre_flip.yaml

# 2. Edit the publishing.yaml
cd /opt/genlab
$EDITOR BlackboxBrief/config/publishing.yaml
# Change `enabled: false` → `enabled: true`
# Leave `min_confidence: 0.85` and `max_approvals_per_pass: 1`

# 3. Verify the edit
cat BlackboxBrief/config/publishing.yaml | grep -A3 auto_publish
# EXPECTED: enabled: true, min_confidence: 0.85, max_approvals_per_pass: 1

# 4. Commit + push (config goes through git, not hand-edits)
git checkout -b ops/flip-ai-creators-auto
git add BlackboxBrief/config/publishing.yaml
git commit -m "ops(ai_creators): flip auto_publish.enabled=true

Day-8 live flip per AUTO_2_ROLLOUT_2026-06-15.md.
Pre-flip calibration: <FILL IN agreement_pct AND samples>.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin ops/flip-ai-creators-auto
gh pr create --fill --base main
gh pr merge --squash --delete-branch
gh run watch --exit-status

# 5. Pull on prod
ssh root@46.224.237.56 'cd /opt/genlab && git pull --ff-only && cat BlackboxBrief/config/publishing.yaml | grep enabled'
# EXPECTED: enabled: true
```

> **Why git + PR instead of editing on prod**: prevents the 2026-06-14
> drift class of bug. Every prod change goes through git, every time.

### 6.3 Switch the dry-run worker to LIVE mode

```bash
# If D3.9 shipped a single worker with --dry-run flag:
ssh root@46.224.237.56 '
systemctl stop auto-approver-dryrun.timer
systemctl disable auto-approver-dryrun.timer
systemctl enable --now auto-approver.timer   # the live version
systemctl list-timers | grep auto-approver
'

# If D3.9 shipped a unified worker that reads enabled from yaml:
# (just verify the timer is firing)
ssh root@46.224.237.56 'systemctl status auto-approver.timer && journalctl -u auto-approver.service -n 20 --no-pager'
```

### 6.4 First-hour monitoring (10:00 – 11:00 IST)

**Set a 10-minute timer. Every 10 min, run:**

```bash
ssh root@46.224.237.56 '
echo "=== timer status ==="
systemctl status auto-approver.timer --no-pager | head -5
echo
echo "=== last worker run ==="
journalctl -u auto-approver.service -n 20 --since "15 minutes ago" --no-pager
echo
echo "=== auto-approved in last hour ==="
sudo -u postgres psql genlab -c "
SELECT id, hook_text, scheduled_for, updated_at
FROM blueprints
WHERE niche_id=\"ai_creators\"
  AND approved_by=\"auto\"
  AND updated_at >= NOW() - INTERVAL \"1 hour\"
ORDER BY updated_at DESC;"
'
```

**Healthy first hour**:
- Worker fired its scheduled passes without errors
- 0 or 1 auto-approval (more = check the gate isn't too permissive)
- Any auto-approval has a corresponding entry in worker logs with
  reasons/confidence

**Red flags (kill switch immediately)**:
- More than 2 auto-approvals in an hour
- Auto-approval on a blueprint with QC failures
- Auto-approval on a blueprint without a video
- Any traceback in worker logs

#### Kill switch procedure

```bash
# Option A — UI: click the red button on Mission Control (added by D3.10)

# Option B — env var (defense in depth):
ssh root@46.224.237.56 'echo "GENLAB_AUTO_APPROVE_DISABLED=1" >> /etc/genlab/.env && systemctl restart auto-approver.timer'

# Option C — YAML flip back:
ssh root@46.224.237.56 'cd /opt/genlab && git checkout HEAD~1 -- BlackboxBrief/config/publishing.yaml'
# (then commit + push to keep prod = git)
```

### 6.5 First-day monitoring (11:00 IST onwards, every hour)

After 12:05 IST publisher fires:

```sql
-- Did the auto-approved blueprint publish?
SELECT id, status, platform, published_at, error_message
FROM publishing_analytics
WHERE niche_id='ai_creators' AND created_at >= NOW() - INTERVAL '6 hours'
ORDER BY created_at DESC;
-- EXPECTED: 4 rows (one per platform: IG, YT, FB, X — Threads optional)
```

**Healthy first day**:
- 1 blueprint auto-approved
- Published to ≥3 platforms successfully
- No operator override required
- Calibration row written with `gate_approved=true, operator_action='approved'`
  (or NULL if operator didn't touch it)

### 6.6 Week-1 monitoring (daily check, 10 min)

| Day | Check |
|---|---|
| Day 9 | First post engagement at 24h — within typical range? Hook didn't embarrass us? |
| Day 10 | Second auto-approval landed today? Both posts performed similarly? |
| Day 11 | Any operator interventions needed? If yes, why? |
| Day 12 | Cumulative agreement rate still ≥85%? |
| Day 13 | Any deferred PRs (Threads/YT pollers) ready to ship? |
| Day 14 | Decision point — extend to gaming next? |

---

## 7. Failure modes — exact recovery for each

### 7.1 Backfill loaded wrong timestamps → bad calibration stats

**Detect**: All backfilled rows have `decided_at` within last 24h (`SELECT
COUNT(*) FROM auto_approval_calibration WHERE decided_at >= NOW() -
INTERVAL '24 hours';` returns a huge number).

**Recover**:

```bash
ssh root@46.224.237.56 'sudo -u postgres psql genlab -c "TRUNCATE auto_approval_calibration;" && sudo -u postgres psql genlab < /root/backups/aac_pre_rollout_<TS>.sql'
```

Then revert D1.2, fix the timestamp source in the backfill script, re-run
dry-run, re-run live.

### 7.2 Lower viral threshold deployed before backfill → calibration mismatch

**Detect**: D1.3 merged before D1.2 ran. Calibration stats show a sudden
~20pp jump in agreement rate the day D1.3 lands, because the historical
rows reflect the OLD threshold and new rows reflect the NEW.

**Recover**: not a hard problem — just wait 7 days for the 7-day window to
roll forward past the threshold change. Don't flip Day 8 until the 7-day
window is fully post-D1.3. Push Day 8 to Day 9 or Day 10.

### 7.3 DailySloBadge breaks Mission Control grid layout

**Detect**: Open dashboard on phone → top bar pushes content off-screen.
Or desktop → niche cards are squished.

**Recover**:

```bash
# Quick: revert D1.4
git revert <D1.4-SHA> --no-edit && git push origin main
```

Don't try to hot-fix the CSS; the badge isn't critical for Day-8 flip.

### 7.4 Dry-run worker silently no-ops because policy file missing

**Detect**: Worker logs `policy disabled` for ai_creators, but you
expected `enabled: true` (during dry-run, you might have a `--dry-run`
override).

**Recover**:

```bash
ssh root@46.224.237.56 '
ls -la /opt/genlab/BlackboxBrief/config/publishing.yaml
cat /opt/genlab/BlackboxBrief/config/publishing.yaml
# If file missing: S7 didn't deploy. Re-merge S7.
# If file exists but enabled=false: that\'s correct for pre-Day-8; check
# that you\'re passing --dry-run on the CLI invocation.
'
```

### 7.5 Strategy B+E flips ai_creators ready prematurely

**Detect**: After D2.7a merges, the calibration card on Mission Control
suddenly shows ai_creators as "READY" with very high agreement. Then you
notice the sample count is small (e.g. 30 samples but all are last-hour).

**Recover**: this is a calibration logger bug — gate decisions in the
last hour all matched operator because operator only approved obvious
cases. Wait 7 days for the window to fill; don't flip Day 8 based on a
1-hour sample.

### 7.6 Calibration table gets new synthetic-looking rows

**Detect**:

```sql
SELECT * FROM auto_approval_calibration
WHERE decided_at >= NOW() - INTERVAL '6 hours'
  AND (blueprint_id LIKE 'test_%'
       OR niche_id NOT IN ('ai_creators','gaming','sports','movies','anime')
       OR operator_action NOT IN ('approved','rejected','revised','skipped'));
```

**Recover**: this means S1's source-tag filter regressed. Find the source
(`SELECT blueprint_id, niche_id FROM ... ORDER BY decided_at DESC LIMIT
20` and grep code), revert the offending change, manually delete the bad
rows.

### 7.7 Per-platform bandit multipliers depress an arm permanently

**Detect**:

```bash
diff <(sort /root/backups/bandit_pre_rollout_*.csv) \
     <(sort /mnt/genlab-media/snapshots/bandit_arms_$(date +%Y%m%d).csv)
# If an arm's alpha/beta is dramatically lower (>50% reduction) and
# total_pulls hasn't grown — D3.8 over-penalized it.
```

**Recover**:

```bash
# 1. Revert D3.8
git revert <D3.8-SHA> --no-edit && git push origin main

# 2. Restore the affected arm's alpha/beta from snapshot
ssh root@46.224.237.56 'sudo -u postgres psql genlab -c "
UPDATE bandit_arms SET alpha=<OLD>, beta=<OLD>
WHERE niche_id=<NICHE> AND arm_id=<ARM>;
"'
```

### 7.8 WARP outage repeats during the rollout

**Detect**:

```bash
ssh root@46.224.237.56 'systemctl status warp-svc'
# OR critical alert in dashboard: "WARP DOWN"
```

**Recover** (from prior session memory):

```bash
ssh root@46.224.237.56 'systemctl restart warp-svc && sleep 5 && curl -s --socks5 127.0.0.1:40000 https://ifconfig.me'
# If still broken, reboot the box via HCLOUD API:
curl -X POST -H "Authorization: Bearer $HCLOUD_TOKEN" \
  https://api.hetzner.cloud/v1/servers/125881055/actions/reboot
```

**Impact on rollout**: pause everything. WARP outage means yt-dlp can't
fetch, which means new blueprints don't render, which means the daily
SLO breaks — which means the dry-run/live worker has nothing to do, but
also nothing to validate. Don't flip Day 8 if WARP went down in the last
48h without root cause.

### 7.9 Operator gets sick for 3 days mid-rollout

**During Day 1-2**: just pause. Nothing is live. Calibration logger still
writes when you DO review, just not as fast.

**During observation window (Day 3-8)**: extend the window. Day 8 becomes
Day 8 + N where N = days you were out. The 5-day observation needs to be
5 days of YOUR REVIEW ACTIVITY, not 5 calendar days, because the gate
calibrates against operator clicks.

**Post Day-8 flip**: hit the kill switch (§ 6.4) before going dark. Live
auto-approval without operator presence violates R5. Re-enable when back.

### 7.10 A new latent bug surfaces from this session

**Probable form**: a deep-dive surfaces yet another "half-wired
infrastructure" pattern that affects something in the AUTO #2 flow.
Examples I'd half-expect:
- Calibration stats endpoint doesn't filter by `source='operator'` even
  after S1, because the endpoint was already merged with hardcoded
  filters.
- Dry-run worker writes to a table that doesn't exist on prod (yet
  another alembic drift).
- Kill switch button's POST endpoint isn't actually wired to the worker.

**Generic recovery**:
1. Read `docs/architecture/infrastructure-half-wired.md` (PR #196) —
   the integration probe is usually what's missing.
2. Don't ship a new PR mid-rollout to fix it unless the bug blocks
   Day 8.
3. If it blocks Day 8, treat the fix as an addition to this runbook —
   write a new section explaining the deferral.

---

## 8. The decision matrix

Default = "normal, don't panic". Read this when something looks off.

| If you see… | Then… | Severity |
|---|---|---|
| Calibration sample count flat for 24h | Operator didn't review — go review | normal |
| Worker logs `policy disabled` pre-Day-8 | Correct, do nothing | normal |
| Agreement rate 80%–95% | Healthy | normal |
| `would_approve` count = 0 most passes | Gate is being conservative, fine pre-Day-8 | normal |
| 1 auto-approval per day post-Day-8 | Exactly what we want | normal |
| WARP IP showing Hetzner IP | WARP died — § 7.8 | **HIGH** |
| Agreement rate drops 10pp+ | Gate misaligned — § 7.5 | **HIGH** |
| Auto-approval count >2/day post-Day-8 | Gate too permissive — kill switch, lower `min_confidence` to 0.92 | **HIGH** |
| Calibration table grew by 100+ rows in an hour | S1 regressed — § 7.6 | **HIGH** |
| Dry-run actually approved something | Dry-run mode broken — kill timer | **CRITICAL** |
| Bandit arm alpha/beta dropped >50% | § 7.7 | **HIGH** |
| Disk free <2GB | Clean .tmp: `find /opt/genlab/.tmp -mtime +3 -delete` | medium |
| DailySloBadge shows 4/5 by 18:00 IST | One niche stuck — investigate which | medium |
| DailySloBadge shows 5/5 by 14:00 IST | Healthy | normal |
| Threads tokens expired again | Run `python -m genlab_core.tools.refresh_threads_token` | medium |
| Pipeline alert table has CRITICAL row | Read it; act per its `suggested_action` field | **HIGH** |
| Operator review queue >20 items for >24h | Review marathon: use bulk-review surface | medium |
| Backfill prediction off by >10% from actual insert | Backfill has an idempotency bug — § 7.1 | **HIGH** |
| `gate_confidence` always exactly 0.5 | Gate is returning a degenerate value — read gate code | **HIGH** |
| New blueprint's auto-approval-preview returns 500 | Read traceback in gunicorn log; usually a missing field on the blueprint | medium |
| `auto_approval_calibration` row count > 5000 | Likely backfill double-ran — TRUNCATE + restore | **HIGH** |
| Mission Control card shows "0/5 ready" 7 days post-flip | Operator hasn't reviewed enough — review marathon | medium |
| Mission Control card shows "5/5 ready" pre-Day-8 | Premature — re-read § 7.5 | **HIGH** |
| Kill switch button click does nothing | API endpoint broken — use env-var fallback (§ 6.4 option B) | **HIGH** |
| `auto_publish.enabled` reverted to false on prod | Someone hand-edited prod — STOP, fix via git, audit `git status` | **CRITICAL** |

---

## 9. Pre-Day-8 readiness checklist

> Do this on the morning of Day 8 at 08:00 IST. If ANY box can't be
> ticked — defer the flip 24h. There's no prize for hitting Day 8 on
> the dot.

```
[ ]  1. Today's date is at least 5 calendar days after the dry-run timer started.
[ ]  2. ai_creators calibration sample count ≥ 30 in the last 7 days.
       SQL: SELECT COUNT(*) FROM auto_approval_calibration
            WHERE niche_id='ai_creators' AND decided_at >= NOW() - INTERVAL '7 days';
[ ]  3. ai_creators agreement rate ≥ 90% in the last 7 days.
       SQL: see § 5.1 step 3
[ ]  4. AutoApprovalCalibrationCard on Mission Control shows ai_creators row as "READY".
[ ]  5. Dry-run worker logs show 0 tracebacks in the last 5 days.
       CMD: ssh root@46.224.237.56 'journalctl -u auto-approver-dryrun.service --since "5 days ago" | grep -i "traceback\|error" | head -20'
[ ]  6. Dry-run worker's predicted approvals over the last 5 days roughly match what
       you actually approved manually.
[ ]  7. No critical alerts in `pipeline_alerts` in the last 48h.
       SQL: SELECT * FROM pipeline_alerts WHERE severity='CRITICAL'
            AND created_at >= NOW() - INTERVAL '48 hours';
[ ]  8. WARP healthy: `ssh root@46.224.237.56 'curl -s --socks5 127.0.0.1:40000 https://ifconfig.me'`
       returns a Cloudflare IP (not the Hetzner IP).
[ ]  9. Daily SLO has been 5/5 published for the last 3 consecutive days.
[ ] 10. You have at least 4 free hours today (publish fires 12:05 IST; you need
        09:00–14:00 IST clear).
[ ] 11. You are not sick, hung over, or sleep-deprived.
[ ] 12. The S7 publishing.yaml file exists on prod with `enabled: false` (will
        flip to true today via git commit).
        CMD: ssh root@46.224.237.56 'cat /opt/genlab/BlackboxBrief/config/publishing.yaml | grep enabled'
[ ] 13. Kill switch button on Mission Control works (test it once in advance,
        confirm dialog appears, then cancel without confirming).
[ ] 14. `/root/backups/` has a snapshot dated within last 24h (you'll take a
        fresh one at flip-time too).
[ ] 15. You have this runbook open in another tab.
```

If you tick all 15 — proceed to § 6.2.
If you tick 12+ but #2 or #3 fails — postpone 7 days, do a review
marathon, retry.
If anything else fails — postpone 24h and fix.

---

## 10. The "fuck it, I'm out" rollback (single page)

> Read this if it's 03:00 IST and you need to disable EVERYTHING from
> this rollout RIGHT NOW. This restores prod to the state at the end
> of the pre-flight check (§ 1). Calibration data is preserved as
> backups; nothing is deleted forever.

### 10.1 Three-command panic stop

```bash
ssh root@46.224.237.56 '
# 1. Stop the worker (if it was started)
systemctl stop auto-approver.timer auto-approver-dryrun.timer 2>/dev/null
systemctl disable auto-approver.timer auto-approver-dryrun.timer 2>/dev/null

# 2. Set global kill switch
echo "GENLAB_AUTO_APPROVE_DISABLED=1" >> /etc/genlab/.env
systemctl restart genlab-dashboard

# 3. Verify nothing is going to auto-approve
sudo -u postgres psql genlab -c "
SELECT id, status, scheduled_for FROM blueprints
WHERE niche_id=\"ai_creators\" AND status=\"APPROVED\"
  AND approved_by=\"auto\" AND scheduled_for >= NOW();
"
# Any rows here are PENDING auto-approved publishes. To cancel:
# UPDATE blueprints SET status="DRAFTED", approved_by=NULL, scheduled_for=NULL WHERE id IN (...);
'
```

### 10.2 Revert the YAML flip

```bash
cd /Users/anarchistsid/GenLab
git log --oneline | grep "flip ai-creators"     # find the flip commit
git revert <FLIP-SHA> --no-edit
git push origin main
gh run watch --exit-status
ssh root@46.224.237.56 'cd /opt/genlab && git pull --ff-only && cat BlackboxBrief/config/publishing.yaml | grep enabled'
# EXPECTED: enabled: false
```

### 10.3 Optional — revert other rollout PRs

Only if you suspect a specific PR is the cause:

```bash
# In order of safety (safest first):
git revert <D3.9-SHA> --no-edit && git push origin main    # worker
git revert <D3.8-SHA> --no-edit && git push origin main    # bandit multipliers
git revert <D2.7a-SHA> --no-edit && git push origin main   # gate strategy
git revert <D1.3-SHA> --no-edit && git push origin main    # threshold lower
# Do NOT revert D1.2 (backfill) blindly — that's a data revert, not code
```

### 10.4 Calibration data preservation

Calibration data is NOT deleted by any of the above. The
`auto_approval_calibration` table stays. If you need to restore
pre-rollout state:

```bash
ssh root@46.224.237.56 'sudo -u postgres psql genlab -c "
ALTER TABLE auto_approval_calibration RENAME TO auto_approval_calibration_rollout_$(date +%Y%m%d);
" && sudo -u postgres psql genlab < /root/backups/aac_pre_rollout_<TS>.sql'
```

### 10.5 Communication

```bash
# Log what you did
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) PANIC ROLLBACK by operator: <reason>" >> /root/logs/auto2_rollout.log

# Update CLAUDE.md memory in next session so we don't lose context
# (operator does this at next session start)
```

### 10.6 Next morning

1. Read journalctl for the worker and gunicorn logs to understand why
   you panicked
2. Read the calibration rows from the failure window
3. Decide: was the panic justified? If yes, write a postmortem section
   into this runbook. If no, file a "things that looked scarier than
   they were" note so next time you don't lose sleep.

---

## Appendix A — Files and endpoints quick reference

| Resource | Path / URL |
|---|---|
| Gate code | `genlab-core/src/genlab_core/scheduling/auto_approval_gate.py` |
| Worker code | `genlab-core/src/genlab_core/scheduling/auto_approver.py` |
| Calibration logger | `genlab-core/src/genlab_core/scheduling/calibration_logger.py` |
| Migration (calibration) | `genlab-core/migrations/versions/o5j6k7l8m9n0_auto_approval_calibration.py` |
| Publishing config (ai_creators) | `BlackboxBrief/config/publishing.yaml` (created by S7) |
| Publishing config (gaming) | `CriticalRush/niches/gaming/config/publishing.yaml` |
| Calibration stats API | `GET /api/v1/auto-approval/calibration-stats?niche_id=ai_creators` |
| Gate preview API | `GET /api/v1/blueprints/<id>/auto-approval-preview` |
| Calibration card UI | `dashboard/frontend/src/views/mission-control/AutoApprovalCalibrationCard.tsx` |
| Critical alerts banner | `dashboard/frontend/src/views/mission-control/CriticalAlertsBanner.tsx` |
| Focus review | `dashboard/frontend/src/components/review/focus-mode.tsx` |
| Bulk review | `dashboard/frontend/src/components/review/bulk-review.tsx` |
| Auto-approval badge | `dashboard/frontend/src/components/review/auto-approval-badge.tsx` |
| Prod box | `ssh root@46.224.237.56` (id 125881055) |
| Dashboard | `https://review.aspirehub.ai` |
| Backup dir (prod) | `/root/backups/` |
| Log dir (prod) | `/root/logs/` |
| Snapshot dir (prod) | `/mnt/genlab-media/snapshots/` |

## Appendix B — Common SQL snippets

```sql
-- ai_creators calibration health (7d)
SELECT
  COUNT(*) AS samples,
  COUNT(*) FILTER (WHERE gate_approved IS TRUE) AS gate_yes,
  COUNT(*) FILTER (WHERE operator_action='approved') AS op_yes,
  ROUND(100.0 * COUNT(*) FILTER (
    WHERE (gate_approved IS TRUE) = (operator_action = 'approved')
  ) / NULLIF(COUNT(*), 0), 1) AS agreement_pct
FROM auto_approval_calibration
WHERE niche_id='ai_creators' AND decided_at >= NOW() - INTERVAL '7 days';

-- All auto-approved blueprints in last 24h
SELECT id, niche_id, hook_text, scheduled_for, updated_at
FROM blueprints
WHERE approved_by='auto' AND updated_at >= NOW() - INTERVAL '24 hours'
ORDER BY updated_at DESC;

-- Critical alerts in last 48h
SELECT created_at, severity, source, message, suggested_action
FROM pipeline_alerts
WHERE severity='CRITICAL' AND created_at >= NOW() - INTERVAL '48 hours'
ORDER BY created_at DESC;

-- Daily publish counts last 7 days
SELECT DATE(published_at AT TIME ZONE 'Asia/Kolkata') AS day_ist,
       niche_id, COUNT(*) FILTER (WHERE status='PUBLISHED') AS published
FROM publishing_analytics
WHERE published_at >= NOW() - INTERVAL '7 days'
GROUP BY 1, 2 ORDER BY 1 DESC, 2;
```

## Appendix C — What NOT to do during the rollout

1. Don't ship feature PRs in parallel. Only AUTO #2 rollout PRs land
   this week.
2. Don't hand-edit prod files. Every change through git.
3. Don't consolidate `.env` files. Tokens are sacred during the window.
4. Don't disable the worker silently — always log why in
   `/root/logs/auto2_rollout.log`.
5. Don't flip Day 8 without all 15 checklist boxes.
6. Don't extend to gaming/sports/movies/anime until ai_creators has
   survived 7 days live without an operator override.
7. Don't trust this runbook blindly. The validation agents missed 5
   bugs; they probably missed more. If a check fails, **investigate
   before improvising**.

---

*End of runbook. Total length: ~830 lines. Read time: ~25 minutes.*
*Last updated: 2026-06-15 by planning agent.*
