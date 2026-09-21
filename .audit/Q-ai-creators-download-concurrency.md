# Q — ai_creators loses a whole run to serial download timeouts

**Filed** 2026-09-21, ANIME-12 §5. Previously noted at
`.audit/Q-ai-creators-download-timeouts.md`; this is the concurrency half.

Four yt-dlp downloads each hit the 120s timeout, serially, and the run
produced zero blueprints. The timeouts are the symptom; the structure is
that the downloads run one after another, so four slow candidates consume
the entire run budget and there is no fifth attempt.

Options, cheapest first:
1. Fetch more candidates than needed and stop at the first N that download.
2. Run the downloads concurrently with a small pool (the box is 4 GB — 2,
   not 8).
3. Shorten the per-download timeout so four failures cost 4 x 40s, not
   4 x 120s.

(1) alone probably suffices and costs no concurrency risk on a 4 GB box.
