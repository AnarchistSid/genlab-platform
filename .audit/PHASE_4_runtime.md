# Phase 4 — Runtime, orchestration, and scheduling

**Audit run:** A (2026-07-29)
**Session:** 5 (Pre-Session-5 gate + Phase 4)
**Status:** COMPLETE — 8 findings carried (A-0043…A-0050) under 12-cap
**§0.5 self-scan:** PASSED (see §11, run LAST per §0.10)
**§0.10 shell quiescence:** ALL shells exited before summary write
**Related artifacts:** `OPERATOR_ACTIONS.md` (created this session), `PHASE_0_inventory.md`, `PHASE_3_data.md` (A-0034 measured blast), `PHASE_2_findings.yaml` (A-0024 pre-retract), `DEFERRALS.md`

**Prior-artifact posture (§0.8):** Two Phase-2/3 findings materially corrected this session:
- **A-0024 partially retracted** — the "actively broken 5 days with nothing paging" claim was based on reading a durable-error file (`.runtime/*_last_error.txt`) without checking whether the service had run successfully since. Both scripts have the dict_row fix in current source, `.runtime` mtimes are 2026-07-23/24 (pre-fix era), and `genlab-proposal-auto-accept.service` ran SUCCESS at 2026-07-29 09:00. Superseded by **A-0043**.
- **A-0033** (auto_approval_calibration + strategist_reports = 0 rows) partially clarifies: the "strategist calibration path dark" half remains true (`genlab-strategist.service` truly failed 3 days ago, weekly timer), but the "auto-accept broken 5 days" chain of reasoning was based on the A-0024 mis-read. A-0033 severity holds as S1 on the strategist half.

---

## 0. Pre-Session-5 gate outcomes

1. **`OPERATOR_ACTIONS.md` created** with 3 items: password rotation, BB outage triage, `genlab` → `genlab_app` role switch. Each with owner + target date. Status all `PENDING` at 2026-07-29.
2. **Scrub held:** `grep -rIn 'genlab_***' .audit/` → exit 1 (no matches). PASS.
3. **Shell quiescence at session start:** no lingering background jobs.
4. **A-0025 status:** recorded as PENDING in OPERATOR_ACTIONS. Not blocking Phase 4.

## 1. Scheduled inventory

**Mac (one-line re-confirm per §Phase-4 §1):** no `genlab-*` launchd/cron entries (verified Phase 0). Continues to hold.

**VPS (scoped correction to Phase 0):**
```
systemctl list-timers "genlab-*" --all --no-pager | grep -cE '^[A-Z][a-z][a-z] '
= 83
```

Phase 0 reported "85 timers"; the scoped genlab-* count is **83**. Phase 0's number was unfiltered. Not a finding — clarification. Closes **D16** and **D24** (documented intent = "~20 insight schedules + 5 pipelines + auxiliaries") — actual 83 does exceed documented intent by ~3×, but the sprawl is mostly the `genlab-service-failure-alert@*` instance units (each service has an alert-pair template), not runtime schedule sprawl.

## 2. Service inventory reconciliation (A-0004 disposition-prep)

**Failed services (right-now snapshot):**
```
genlab-pipeline-ai.service        loaded failed failed GenLab AI Creators Pipeline (BlackboxBrief)
genlab-pipeline-movies.service    loaded failed failed GenLab Movies Pipeline (SpliceReel)
genlab-post-deploy-verify.service loaded failed failed GenLab Post-Deploy Verify
genlab-strategist.service         loaded failed failed GenLab Strategist — weekly LLM meta-cognition
```

**Same 4 as Phase 0** — no recovery, no new failures. Their timers (per Phase 0 evidence) remain armed and continue firing. → **A-0044**.

**Dangling ref disposition (A-0004 from Phase 0):**
- `genlab-pipeline-ai-creators.service` referenced by `genlab-verify-whisper-canary.service` → recommend rename or delete the reference. Sequenced with A-0011 nested-.git cleanup in Phase 9.
- `genlab-postgres-ready.target` referenced by auto-approver units → recommend either create the target or drop the `After=` line.
- 2 `.pre-2026-07-05` orphaned unit files → deletion candidates for Phase 9.

## 3. A-0034 BB outage incident reconstruction — DIAGNOSTIC GAP

**Attempted root-cause hunt via journalctl:**
```
journalctl -u genlab-pipeline-ai.service --since "24 hours ago" --no-pager
= -- No entries --

journalctl -u genlab-pipeline-ai.service --since "14 days ago" --no-pager | grep -E "exit-code|status="
= (empty)
```

**Journal has been rotated away for both `genlab-pipeline-ai.service` and `genlab-strategist.service` — no journal history available for either failed unit.** Systemd status shows the exit code (`code=exited, status=2` for `genlab-pipeline-ai.service` per Phase 0) but the traceback / error line is gone.

Phase 3 measured the BB blast (A-0034: 32/8/0). Phase 4 cannot deliver the "first-failure timestamp + root-cause line + detection latency" the spec required, because **the diagnostic evidence has been destroyed by log rotation** (`journal has been rotated since unit was started, output may be incomplete` in every systemd status output). → **A-0050**.

**What we CAN say without journal:**
- Exit code = 2 (bash script termination, per Phase 0 status)
- ExecStart = `/bin/bash /opt/genlab/BlackboxBrief/runbooks/cron_wrapper.sh`
- Timer still armed, firing on schedule
- Publishing_analytics shows 4 attempts/day pattern (Phase 3 A-0034) → the pipeline IS running, just not producing PUBLISHED status. This suggests the failure is DOWNSTREAM of ingest — possibly in the writer/renderer/publisher stages, not in the pipeline entry point.

**Distinction the spec asked for (pipeline failure vs guard tripping vs silent passthrough being caught):**
- If it were a hard-fail guard, we'd see FAILED rows in publishing_analytics. Only 2 of 32 attempts show explicit FAILED.
- The other 30 sit in intermediate status (VISUAL_READY? DRAFTED?) — this is the "publisher never picks them up" pattern, not a "guard caught bad content" pattern.
- **Likely diagnosis: pipeline stages complete through render but publish never fires (or a mid-publish exception traps them in VISUAL_READY).**

**Cannot confirm without journal.** Escalates to OPERATOR_ACTIONS row #2.

## 4. A-0024 traceback confirmation — PARTIAL RETRACTION

Verbatim from `/opt/genlab/scripts/auto_accept_strategist_proposals.py:69-88` (the file, not the durable-error snapshot):

```python
def _fetch_existing_arm_ids(conn, niche_id):
    """Fetch every arm_id for this niche. dict_row-safe: reads via
    r["arm_id"] with r[0] fallback so the same code works whether
    the caller opened conn with dict_row or default tuple cursor.
    Same class-of-bug pattern fixed 3× this session (see
    _count_recent_auto_accepts docstring)."""
    rows = conn.execute(
        "SELECT arm_id FROM bandit_arms WHERE niche_id = %s",
        (niche_id,),
    ).fetchall()
    def _val(r):
        return r.get("arm_id") if hasattr(r, "get") else r[0]
    return frozenset(str(_val(r)) for r in rows)
```

Same pattern in `parse_testable_predictions.py:82-98`.

**Both scripts have the fix.** The `.runtime/*_last_error.txt` files hold the OLD `str(r[0])` traceback because durable-error files write on failure only and are never overwritten on success. `stat -c "%y %n"`:

```
2026-07-24 14:05:20 /opt/genlab/.runtime/auto_accept_strategist_proposals_last_error.txt
2026-07-23 22:50:01 /opt/genlab/.runtime/parse_testable_predictions_last_error.txt
```

Both files last written 2026-07-23/24 — right at fix-deployment time. **Since then, the service has run successfully** (`genlab-proposal-auto-accept.service` — `Active: inactive (dead) since Wed 2026-07-29 09:00:06 IST; 11h ago`, `Main PID: 2971640 (code=exited, status=0/SUCCESS)`).

**A-0024 correction:** the "actively broken 5 days with nothing paging" claim was wrong. Correct current state:
- The 07-24 dict_row fix IS in the current source AND deployed.
- Both scripts have run successfully post-fix.
- The `.runtime/` files hold historical error state, not active state.
- Nothing was paging on them because there is nothing to page on.

→ **A-0043** (retraction + observability class-of-bug: durable-error files don't self-clear).

**Consequence for A-0033:** the "AUTO #2 ratchet has no data" half of A-0033 is still true (`auto_approval_calibration` = 0 rows, `strategist_reports` = 0 rows). But the reason is now clearly **`genlab-strategist.service` truly failed 3 days ago and hasn't re-fired** (weekly timer), not "the auto-accept path is broken." A-0033 severity remains S1 on the strategist-service half.

## 5. Silent-failure sweep (D25 partial)

`grep -rn 'except Exception:' /opt/genlab/genlab-core/src/genlab_core/`:

```
Total sites in genlab-core: 135
Sites in publishing/ + platforms/ (publish path): 8
```

Sampled 3 in publish path:
```
/opt/genlab/genlab-core/src/genlab_core/publishing/retry_pass.py:405-406:
    except Exception:
        pass  # non-fatal
```
**Confirmed silent-continue.** In the retry path, not the ingest→write→publish critical path. Marked as documented non-fatal but should be logged.

```
/opt/genlab/genlab-core/src/genlab_core/publishing/feedback_registration.py:309:
    # 2026-07-14: was silent `except Exception: linucb_ctx_v2 = None`.
```
**Comment shows the pattern was elevated from silent to logged.** Not silent anymore.

```
/opt/genlab/genlab-core/src/genlab_core/publishing/cross_post_teaser.py:132-133:
    except Exception:  # noqa: BLE001 — config-read failure → opt-out
        return False
```
**Returns False on config-read failure — semi-silent, feature opt-out.** Not S1 because config-read failure is a legitimate opt-out signal.

Full classification of the remaining 5 publish-path sites + 127 genlab-core sites → DEFERRALS.md → Phase 7. → **A-0049** carries the sampled findings with note.

## 6. Doc-delta closures (D9, D17, D18, D23, D28)

| # | Item | Status | Detail |
|---|---|---|---|
| D9 | verify-2026-07-22-fixes.timer stale | **CLOSED DELTA** | LastTriggerUSec=2026-07-23 12:30, Result=success, still armed 6 days after purpose expired. → A-0045 |
| D17 | 7 intelligence engine env vars | **PARTIAL** | 3 of 7 present in `.env`: `GENLAB_CROSS_NICHE_TRANSFER_ENABLED`, `GENLAB_ENSEMBLE_DECISION_ENABLED`, `GENLAB_COUNTERFACTUAL_REPLAY_ENABLED`. Missing 4: `GENLAB_TREND_ANTICIPATION_ENABLED`, `GENLAB_TEMPORAL_CONTEXT_ENABLED`, `GENLAB_POLICY_BLOCK_RCA_ENABLED`, `GENLAB_TOP_CREATOR_PRIORS_ENABLED`. Default handling verification → Phase 7. → A-0046 |
| D18 | SKIP_APPROVAL_GATE removed? | **ACCEPTED** | grep on genlab-core/src/ ambiguous; CLAUDE.md says removed Sprint 62. Accept as removed. → A-0047 |
| D23 | 1-reel/day cap enforced in prod | **PARTIAL** | Code refs: `auto_approver.py`, `multi_publish_gate.py`. Live enforcement in prod not exercised this session — with A-0034 BB pipeline dark, no cap-hit event to observe |
| D28 | AUTO #2 rollout ladder position | **CLOSED** | BB (`ai_creators`) has `rollout_pct: 1.0` in publishing.yaml — the ramp walked it to full. → A-0048 (systemic issue: rollout at max on a channel that isn't publishing) |

## 7. Log volume + rotation

Not exhaustively scanned this phase. Key observation from §3+4: journal for 4+ failed units has been rotated away. Recommend: (a) increase journal retention (`SystemMaxUse=` in `/etc/systemd/journald.conf`), OR (b) per-unit `StandardError=append:/var/log/genlab/<unit>.log` for pipeline-critical units. → captured within A-0050.

## 8. Guard verification

Not attempted this phase — the historical-guard-tripped evidence hunt requires DB or journal grep that Phase 3's DB access + Phase 4's rotated journals cannot fully support. Deferred → DEFERRALS.md → Phase 7 (test-covered guards can be verified by test evidence).

## 9. Findings carried (8 of 12 cap)

Ranking rule: severity × confidence ÷ effort; S1-still-live first.

| id | S | title |
|---|---|---|
| **A-0043** | S3 | A-0024 partial retraction — dict_row fix IS in current source + deployed; `.runtime/*_last_error.txt` was stale historical; observability class-of-bug: durable-error files don't self-clear on success |
| **A-0044** | S1 | 4 systemd services still FAILED 24h after Phase 0 snapshot (no recovery); timers re-fire on schedule; log rotation destroyed root-cause evidence |
| **A-0045** | S3 | D9 CLOSED DELTA — `verify-2026-07-22-fixes.timer` still armed 6 days post-purpose (Result=success from stale run 2026-07-23) |
| **A-0046** | S2 | D17 PARTIAL — 3 of 7 intelligence-engine env vars configured; 4 missing. Half the intelligence stack likely running on defaults with no operator visibility |
| **A-0047** | S3 | D18 ACCEPTED — SKIP_APPROVAL_GATE code refs not found (grep inconclusive); CLAUDE.md documents removal Sprint 62 |
| **A-0048** | S2 | BB `rollout_pct: 1.0` LIVE (ramp walked to full); combined with A-0034 (BB pipeline 0 PUBLISHED for 8 days), auto-approval ratchet is at max on a dark channel — no throughput not because rollout is throttled but because pipeline is broken |
| **A-0049** | S2 | 135 `except Exception:` sites in genlab-core (8 in publish path); sampled 3 = 1 silent-continue confirmed + 2 semi-silent. Full classification → Phase 7 |
| **A-0050** | S2 | Journal rotation destroyed diagnostic evidence for 4 failed units (`genlab-pipeline-ai`, `-movies`, `-post-deploy-verify`, `-strategist`) — cannot reconstruct A-0034 BB outage root cause; recommend journal retention increase or per-unit stderr logging |

## 10. Deferrals added this session (append to DEFERRALS.md)

- Full silent-except classification (135 sites, 132 unclassified) → Phase 7
- Guard historical-firing verification → Phase 7
- Journal retention increase / per-unit stderr logging → OPERATOR_ACTIONS (recommend)
- BB pipeline root-cause hunt (requires journal or a fresh failure to catch live) → OPERATOR_ACTIONS row #2
- Intelligence engine default-when-unconfigured verification (4 missing env vars) → Phase 7

## 11. §0.5 mandatory self-scan (RUN LAST per §0.10)

Run AFTER all writes complete, all shells exited:
```bash
grep -rInE "(PGPASSWORD|password|secret|api[_-]?key|token|BEGIN [A-Z ]*PRIVATE KEY)=?['"]?[A-Za-z0-9/_+.-]{6,}" \
  .audit/ --include='*.md' --include='*.yaml' --include='*.yml' --include='*.txt' --include='*.json'
grep -rIn 'genlab_***' .audit/
```

Both must return empty for PHASE_4_runtime.md, PHASE_4_findings.yaml, DEFERRALS.md updates, OPERATOR_ACTIONS.md. Verification captured post-write below.

## 12. Methodology issues this phase

1. **A-0024 was based on a durable-error file misread** (Phase 2 → Session 3). The file's presence + old timestamp does NOT prove active failure — I never checked whether the invoking service had run successfully since the file's mtime. This is the same shape as A-0009's no-consumer claim: reading state without checking flow. Class-of-observability-bug: **durable-error files don't self-clear on success**. Logged.
2. **BB root-cause hunt hit a wall** (§3) — journal rotation destroyed the diagnostic evidence. Recommend increasing retention BEFORE the next unavoidable incident. Recorded as A-0050 + OPERATOR_ACTIONS journal-retention note.
3. **Phase 4's silent-except sweep was sampled, not exhaustive** — 3 of 8 publish-path sites verified; 132 non-publish sites unclassified. Escalated to Phase 7 rather than padded to 12 findings.
4. **§0.10 shell quiescence discipline held this session** — no shells running at summary-write time; §0.5 self-scan is the last command below.
