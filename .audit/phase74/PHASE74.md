# Phase 7.4 — Status Contamination, One Bug or Two, No Ship

**Findings:** 68 (7C / 22H / 24M / 12I / 3L). New **F-0068 CRITICAL** (audit-wide status-filter shape error). F-0050 downgraded INFO (invalidated). F-0064 re-scoped (systemic, not Threads-specific). F-0067 clarified (IG-heavy, not Threads-heavy).

## 1. Status decomposition — every volume metric moved materially

30d publishing_analytics distribution: INSIGHTS_168H=245, FAILED=66, SKIPPED=32, **SUCCESS=28 (transient, stuck rows dated 2026-06-25→07-14)**, INSIGHTS_48H=27, INSIGHTS_24H=21, INSIGHTS_6H=17, REMOVED_BY_META=1. **The prompt's `status='SUCCESS'` filter is wrong** — SUCCESS is the initial state before the metric collector transitions to INSIGHTS_6H → INSIGHTS_24H → INSIGHTS_48H → INSIGHTS_168H. Correct "was-published" filter = `IN ('SUCCESS','INSIGHTS_6H','INSIGHTS_24H','INSIGHTS_48H','INSIGHTS_168H')`.

Recomputed on the correct filter:

| Metric | Prior | Phase 7.4 | Direction |
|---|---|---|---|
| 7d mandate compliance (4 platforms) | 86% → 61.4% | **41.4%** (58/140) | DOWN |
| Per-channel avg survival | 20–38% | **79–88%** | UP substantially |
| IG missing post_id (F-0050) | 17–37% | **0%** | INVALIDATED |
| F-0064 write-gap platform mix | Threads-specific 128 | **systemic 505** (IG 171, Threads 128, YT 106, FB ~105, TW 25) | REVERSED |
| Per-post closure | gaming 33.6% > sports 28.8% > ai_c 26.8% > movies 21.9% > anime 21.7% | unchanged | SAME |

Per-post closure is unchanged because that query already had `post_id IS NOT NULL AND post_id <> ''` which effectively excluded failed rows. **DECISION.md's pause list depended on Phase 7.2 "ai_creators 20% worst" survival — that number is invalid.** All channels now sit at 79–88% survival with narrow spread (7 points best-to-worst). No strong pause signal remains. **F-0068 CRITICAL filed** — eighth audit methodology error, third of shape "query written without first checking what the table holds."

## 2. One bug or two? Two.

**B1:** ZERO PF rows have empty/null post_id across all platforms. Phase 7.3's "falls back to empty string" hypothesis is falsified. **B3 reverse-direction:** 204 PF-orphans = 174 IG + 30 Threads (IG-heavy, not Threads-heavy). Combined with corrected F-0064 (systemic, IG top): **different mechanisms on different platforms** — IG has both directions elevated; Threads has PA→PF only; YT/FB have PA→PF near-zero PF→PA. F-0067 does not collapse into F-0064.

## 3. Threads mechanism — nothing to name

`parallel_publish.py:279` writes `outcome.successful_post_ids[platform] = result.post_id`; `feedback_registration.py:83` reads it. All PF rows have non-empty post_ids → Phase 7.3 diagnosis wrong. Real mechanism is not "Threads drops the ID" — it is some systemic PF-writer condition failing silently ~30% across all platforms. **Not a `path:line` this session can produce.** Requires instrumenting the writer with WARN on every early-return branch (imports L47–52, status-skip L65, all `except` blocks) and observing 24–48h.

## 4. Verification gate — no fix shipped

Fifth revision of the write-gap number. Every session's methodology exposes the prior session's. Shipping a fix against the current numbers would be the same class of error the audit repeatedly filed against the codebase. **F-0064 re-scoped in-place; no code changed; no rows written to prod.**

## 5. Operator actions

**D.1 Anthropic:** unchanged since Phase 7.1 — console click required. **D.2 Port 5432:** unchanged since Phase 7.1 — compose bind + docker restart required.

All shells exited. Read-only against prod. **DECISION.md now requires a fifth revision** — Phase 7.2 survival-based pause list built on the F-0068 shape error is superseded by uniform 79–88% survival across all 5 channels.
