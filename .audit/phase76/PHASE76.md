# Phase 7.6 — Was There Ever a Gap?

**Findings:** 71 (8C / 20H / 26M / 14I / 3L). **F-0071 CRITICAL** filed (ninth methodology error, largest). F-0064 CLOSED INVALID. F-0066 downgraded MEDIUM. F-0069 downgraded MEDIUM.

## 1. No closure gap. Two independent counts + IG trace.

60d window, exclude last 3d, correct status filter:

| Platform | PA publishes | PF rows | Δ | reward_48h coverage |
|---|--:|--:|--:|---|
| youtube | 161 | 157 | -4 | 157/157 |
| facebook | 157 | 157 | **0** | 157/157 |
| instagram | 136 | 136 | **0 (exact)** | 136/136 |
| threads | 44 | 41 | -3 | 41/41 |
| twitter | 21 | 21 | 0 | 7/21 (out of scope) |

**PF ≈ PA on every north-star platform, ~100% reward coverage.** Grain: PF is one row per (post_id, platform), 1190 total = 1188 distinct + 2 dupes. Bandit active: 68 arms updated today, 67 yesterday. **IG trace:** bp `867ff0bf` (anime, `instagram:18071214047438640`) → arm `transform__hook_framing__jump_cut_visual` reward `0.0417`. Two more IG rewards traced.

The reward loop uses `pending_feedback.post_id` to fetch metrics from platform APIs — it **never** joins to `publishing_analytics`. The audit invented that join at Phase 6 §0.3 and every subsequent revision debugged the audit's own SQL, not a real bug.

## 2. What B changes

- **F-0064 CLOSED INVALID.** All five sizes (505 → 128 → dissolved) were fictional.
- **INTELLIGENCE 5 → 6** (SCORECARD updated). Closed loop with near-100% coverage on all four north-star platforms is above "wouldn't ship internally" and at "customer wouldn't notice." Not 7+ because Neural-LinUCB still absent (F-0057) caps the ceiling. Prior citations rested on an audit-invented artifact.
- **DECISION.md Rev 7 banner** filed. The "closure fix > pause channels" argument in Rev 6 loses its subject — no closure to fix. Full body needs fresh write against Rev 7 evidence.
- **"What audit got wrong" +1:** F-0071 is the ninth methodology error and the largest. Sibling to F-0061 (Threads-out-of-scope from misread CLAUDE.md rule #23), F-0062 (SQL scope shadow), F-0068 (status-filter shape). All four: query written without checking what the codebase actually does. F-0071 drove a strategic recommendation across five sessions.

## 3. Shortcode NOT derivable — backfill needs Graph API

Tested Instagram base64 transform on 5 paired rows. Decoded shortcodes yield ~3.94×10¹⁸ range values; PF media IDs are ~1.8×10¹⁶ range. Related identifiers (Instagram permalink pk vs Graph API fbid) but not a pure transform. **Historical backfill requires one Graph API lookup per shortcode.** Forward fix (store both at publish time) is free. Details in F-0066.

## 4. F-0069 mechanism location

`metric_collector.py:74 fetch_platform_metrics` dispatches via dict at 127-134 (all six platforms wired). Threads fetcher `learning/metrics/threads.py:_fetch_threads` (import 194); FB `learning/metrics/facebook.py:_fetch_facebook` (import 182). Stall (17 Threads + 10 FB at status=SUCCESS aged 20+ days) is a conditional failure inside `process_pending_task` at line 810 — likely early-return on Meta API 400 / token-expiry / F-0039-shape silent except. Journal grep returned no matching lines; needs Phase 7.5 instrumentation deployed to name. **F-0069 downgraded MEDIUM** — analytics-status only, not reward-loop; PF for Threads/FB works (41/41, 157/157 rewards).

## 5. Operator tasks consolidated

`.audit/OPERATOR_TASKS.md` written. Four items, one owner (operator), no further audit analysis. **Tasks 1 (Anthropic) + 2 (5432) are on their own merits high-priority; tasks 3 (instrumentation) + 4 (dual-ID) are audit-clarity, not defect fixes.** Nothing in the list is on the critical path for the learning loop.

**Read-only measurement has reached its limit.** Everything remaining needs either a deploy or a decision. Next session should read logs (if instrumentation deployed) or address strategic questions the audit did not answer (revenue attribution, monetisation).

All shells exited. Only `.audit/` writes made this session.
