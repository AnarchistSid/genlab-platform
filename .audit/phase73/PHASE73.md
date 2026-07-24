# Phase 7.3 — Verify the Gap, Then Fix It

**Findings:** 67 (6C / 22H / 25M / 11I / 3L). F-0064 re-sized HIGH (was CRITICAL). F-0066 upgraded HIGH (was MEDIUM). New: F-0067 (reverse-direction linkage gap).

## 1. The 204 unmatched — structural, not clipping or format

Widen ±7d: 196/400 (49%) → 255/530 (48%). Match rate stable → not clipping. Both tables carry both formats (PA 954 bare, PF 535 bare) — normalize handles both. 204 unmatched = post_ids that **structurally don't appear in PA**. **F-0067 MEDIUM** filed (bidirectional gap: F-0064 = PA→PF, F-0067 = PF→PA). **F-0066 upgraded to HIGH** — no FK; every learning metric ran through a regex over a composite string; structural cause behind three revisions.

## 2. The write-gap mechanism, named

Writer path: `genlab-core/src/genlab_core/publishing/feedback_registration.py:30` `register_pending_feedback()` — called AT publish time (not a collector), non-fatal by design, swallows any exception at `logger.warning`. Loops over `outcome.platform_status` (line 64), skips non-published statuses (line 65), and at **line 83** pulls `outcome.successful_post_ids.get(plat, "")` — **falls back to empty string** for platforms in the `prior_published` set (comment 68–70). `_normalize_post_id` at line 184; `_is_published_status` at line 210.

## 3. F-0064 re-sized: Threads-specific, not systemic

B.4 decomposition of 535 PA-with-post-id-and-no-PF: **406 (76%) are `INSIGHTS_168H`** — post-publish metric-fetch rows, legitimately no PF needed. **129 (24%) are `SUCCESS`** — the real gap. Of those 129: **128 are Threads** (anime 30, movies 28, sports 27, gaming 25, ai_creators 18), plus one gaming/IG. Threads SUCCESS-without-PF spans all 5 niches uniformly — matches "Threads dispatch bug," not "PF writer bug." F-0064 re-filed HIGH (was CRITICAL); scope narrowed to "Threads-side of `outcome.successful_post_ids` not populating."

## 4. No fix shipped — Part A gate honoured

Prompt: "correctly re-sized finding is a better outcome than a fix shipped against a number still moving." Phase 7.2's F-0064 diagnosis was wrong in scope; a fix would have modified the shared PF-writer when the defect sits in the Threads dispatcher wire that feeds `successful_post_ids`. Verification gates 1–3 not run. Corrected target: grep `platforms/threads.py` + `parallel_publish.py` for the return-value shape. Real uplift: 48.9% → ~65% (Threads reward added), not the 90% Phase 7.2 claimed. Multiplier ~1.3× not ~2×. **DECISION.md B.3 "closure fix > pause channels" argument narrows but likely still beats 4× consolidation** (0 brand loss, no operator attention required to run it).

## 5. F-0066 scope (Part C)

**Interim fix (no backfill):** ADD `post_id_norm text GENERATED ALWAYS AS (regexp_replace(post_id,'^[a-z_]+:','')) STORED` + btree index on both tables. Join uses `norm`, ships stable. **Structural fix:** add `pending_feedback.publishing_analytics_id UUID REFERENCES publishing_analytics(id)`; feedback_registration.py:119 accepts PA UUID from caller. **Backfill size:** 1,190 PF rows total; ~400 fail an FK today (204 orphans + format loss). Generated-column route is safe first step.

## 6. Operator actions (D.1 / D.2)

Both unchanged since Phase 7.1. **D.1:** Anthropic still requires console.anthropic.com auto-reload ($20/$50). **D.2:** port 5432 compose bind (`127.0.0.1:5432:5432`) + `docker compose up -d postgres`; verify `nc -zv 46.224.237.56 5432` refuses.

All shells exited. Read-only. Phase 7.3 outputs limited to `.audit/`.
