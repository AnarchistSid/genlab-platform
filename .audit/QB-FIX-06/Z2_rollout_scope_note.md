# QB-FIX-06 Z2 — Rollout Scope Note

**Date:** 2026-08-07 15:05 IST
**Purpose:** Record the actual state of `rollout_pct` and `auto_publish` per niche so the Aug 10 gate is understood correctly.

## The stated framing

QB-FIX-02 §1 established: "Do not flip `rollout_pct: 0.1` for any niche until all four F4 posts have survived 48 hours with audio confirmed playing." This was drafted with movies + anime in mind — F4 was the movies + anime batch.

## The actual state

| niche | auto_publish.enabled | rollout_pct | governed by Aug 10 gate? |
|-------|---------------------|-------------|--------------------------|
| ai_creators | **true** | **1.0** | NO — publishes daily |
| gaming | (unknown; not audited this session) | (unknown) | NO — nightly_scheduler queues, publisher rarely reaches |
| sports | **true** | **1.0** (per F-QB-0803) | NO — published today unwatched |
| movies | false | 0.0 | YES — post Aug 10 gate |
| anime | false | 0.0 | YES — post Aug 10 gate |

**ai_creators** and **sports** have been at rollout 1.0 throughout this cycle. They publish whatever the auto-approver + publisher select without passing through the batch-1 / batch-2 gating discipline that was applied to movies + anime.

## Consequences

- The Aug 10 gate ONLY governs **movies** and **anime**. The other three niches (ai_creators, gaming, sports) are on their own trajectory.
- **ai_creators fired today** (`1636b3d1` Disney Plus, pre-fix — see W-Aug07 report). No gate stopped it.
- **Sports fired today** (`284a885f` Sainz F1 clip, post-fix — see Z1). No gate stopped it.
- Both published cleanly per DB signal. Audio-plays confirmation pending operator listen.
- Both are structurally identical to the "flip rollout_pct 0.1" scenario the Aug 10 gate is designed to authorize. **The gate is asking permission for a posture two niches already have.**

## Not an argument for panic

Both ai_creators and sports published without incident today (Threads timeout aside, which is infra). Content ID matches, if any, would surface first as YT Studio warnings or IG restrictions — those are the same signals the Aug 10 gate is watching for.

## Argument for accurate posture

The watch documentation should state:
- Aug 10 gate governs movies + anime specifically
- ai_creators + sports are at rollout 1.0 already and publishing daily; the same watch checks (audio-plays, REMOVED_BY_META, YT Studio) apply
- gaming at rollout unknown; Y1's (c) defect means gaming rarely publishes anyway

## No action

Per prompt: "No action; record it."

This memo IS the record. Aug 10 gate remains the authorized flip point for movies + anime specifically. The other three niches continue on their existing posture.

## Commit

Bundled with Z0 + Z1 or committed alongside as `docs(watch): scope rollout_pct gate to movies + anime — record actual per-niche posture`.
