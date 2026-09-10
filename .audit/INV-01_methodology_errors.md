# INV-01 — Methodology errors
Every place I overturned my own finding or could not meet the evidence standard.

## Overturned within this audit

| # | claim | correction | how caught |
|---|---|---|---|
| E-01 | `pending_feedback.reward` / `audience_snapshots.follower_count` | Neither column exists (`reward_48h`, `metric_value`). Two queries errored before I confirmed the schema | psql error, then `information_schema` |
| E-02 | Filtered follower metrics on `metric_name ILIKE '%follow%'` | Excluded `subscribers` and `fans` — i.e. all YouTube and all Facebook. The first follower table I produced was missing the only two cells with movement | enumerated `DISTINCT metric_name` |

## Could not meet the evidence standard

| # | item | reason | recorded as |
|---|---|---|---|
| U-01 | Phase 1 per-stage last successful/failed execution × 5 niches | Journal retention is hours; `genlab-pipeline-ai` has **no entries at all**. I read it successfully at 02:47 UTC today and it had rotated by 10:00 | `UNMEASURABLE` — not inferred |
| U-02 | VO tier per published reel | `audio_provider` never persisted; enumerated across all 158 blueprints in window, zero carry the key | `UNMEASURABLE` (T-01) |
| U-03 | anime reward mean | 4 rows, 0 with `reward_48h`. Not zero — no observations | `UNMEASURABLE` |
| U-04 | Engagement zero decomposition (polled-empty vs not-polled) | requires journal for the polling window; rotated | `UNMEASURABLE` cause (T-05) |
| U-05 | YouTube quota used/remaining | not exposed in a form the codebase reads | `UNMEASURABLE` |
| U-06 | Phase 2.2/2.3/2.4 render-layer inspection (captions, ducking, cuts-per-second, transitions, attribution slate) | Requires per-render ffprobe/frame sampling across ≥3 renders × 5 niches. **Not performed** — the render artifacts live in per-run temp dirs, most of which have rotated. Reporting these from code inspection would violate the standard | **NOT AUDITED**, declared |
| U-07 | Phase 2.5 per-platform attempt/success/failure classes | `publishing_analytics` carries no publish-outcome rows since 2026-08-14 — statuses are all `INSIGHTS_*`. The 15%/20% IG/Threads failure rates cannot be restated from current data | `UNMEASURABLE` |

## Standing-rule compliance notes

- No `FUNCTIONING-VERIFIED` in this inventory rests on tests or code reading;
  each cites a post ID, file with byte count, DB row count, or measured probe.
- Detection probes enumerated rather than sampled (158 blueprints, 502 arms,
  105 flags, 329 units, all non-empty tables).
- `#218` recorded as verdict-given, not as PASS-by-inspection — the listen was
  the operator's and is cited as such.
- **U-06 is the largest gap in this inventory.** Sections 2.2, 2.3 and 2.4 of
  the prompt are substantially unaudited. They should be re-run against a live
  render immediately after the 2026-09-11T07:00Z publish, while the artifacts
  still exist.
