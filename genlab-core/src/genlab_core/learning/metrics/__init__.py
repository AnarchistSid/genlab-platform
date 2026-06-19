"""Per-platform metric fetchers — extracted from learning/metric_collector.py.

The historical 1524-LOC ``metric_collector.py`` bundled 6 per-platform fetcher
implementations alongside the orchestration layer (reward computation, bandit
update, task processing). The v2 codebase review (2026-06-19) identified this
as a refactor candidate with clear natural splits — each platform has its own
auth model, response shape, and reward-shape specialisation.

This subpackage extracts the per-platform fetchers, one module per platform.
``metric_collector.py`` re-exports the moved symbols so existing imports
(tests, scripts) continue to work unchanged — a backward-compatible split.

Phased rollout (one platform per PR to keep diffs reviewable):
  - youtube.py (this PR, P5a partial)
  - instagram.py (follow-up)
  - facebook.py (follow-up)
  - x_twitter.py (follow-up)
  - tiktok.py (follow-up)
  - threads.py (follow-up)

After all 6 land, ``metric_collector.py`` shrinks from 1524 LOC to ~500 LOC of
pure orchestration. The platform fetchers become navigable as individual
files instead of a 1500-line scroll.
"""
