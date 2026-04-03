# Phase 4 Decision: LEARN_PERFORMANCE — Inline vs Separate Cron Job

## Decision: Separate Scheduled Job (NOT inline with pipeline)

## Rationale

### 1. Analytics Delay Makes Inline Impossible

Platform analytics APIs have 24-48 hour delays before engagement data is available:
- YouTube Analytics API: ~48h delay for completion_rate, engagement metrics
- TikTok Content Stats: ~24h delay
- Instagram Insights: ~24h delay
- Facebook Insights: ~24h delay

The publishing pipeline runs, publishes, and exits. There is nothing to learn from yet — the engagement data does not exist at publish time.

### 2. Both Source Projects Already Use Separate Jobs

**Content Scraper** (`performance_learner.py`):
- Standalone CLI: `python execution/performance_learner.py --lookback-days 7`
- Weekly cadence, fetches analytics from Microsoft Lists, computes weight adjustments
- Thompson Sampling update is part of the same weekly job

**Gaming Clips** (`process_feedback.py` + `check_engagement.py`):
- Step 19 in daily pipeline: `check_engagement.py --fetch-due` (fetch metrics for posts in measurement window)
- Step 20: `process_feedback.py --bandit-update` (update per-platform bandits)
- Step 21: `process_feedback.py --decay-check` (decay detection)
- These run at the END of the daily pipeline, processing engagement from PREVIOUS runs, not the current one

### 3. genlab-core Interface Already Specifies This

`genlab_core.strategies.PerformanceLearner` docstring:
> "Runs as a separate scheduled job, not inline with the publishing pipeline, because platform analytics have 24-48 hour delays."

### 4. Two Separate Concerns to Wire

| Concern | When | What |
|---------|------|------|
| **Arm selection** (at publish time) | Inline, during PUBLISH stage | `PlatformBanditManager.select(platform)` returns arm choices for hook_type, posting_window, etc. Store selection in publish metadata. |
| **Posterior update** (after analytics) | Scheduled job, 24-48h+ later | Fetch engagement → compute reward → `PlatformBanditManager.update(platform, selection, reward)` → save state |

## Implementation Plan

1. **GamingPerformanceLearner** — implements `PerformanceLearner.execute()`, callable as CLI:
   ```
   python -m niches.gaming.stages.learn_performance --lookback-days 7
   ```
2. **NOT added to PipelineRunner's stage list** — it is not a pipeline stage
3. **Bandit arm selection** wired into `PublishGamingContent` stage at publish time (read-only, no posterior update)
4. **State files** persisted to `niches/gaming/config/bandit_states/`

## Dimension Name Mismatch Bug (Known Bug #1)

`bandit_dimensions.yaml` uses `hook_style`, `caption_style`, `posting_time`, `hashtag_strategy`.
`platform_bandits.yaml` uses `hook_type`, `caption_keyword_style`/`caption_style`, `posting_window`, `thumbnail_style`, `audio_type`, `cta_type`.

The per-platform config (`platform_bandits.yaml`) is the correct source of truth — it has platform-specific dimensions that vary per platform. The global `bandit_dimensions.yaml` is a legacy artifact from before per-platform bandits were added.

**Fix:** CriticalRush will use only `platform_bandits.yaml` (per-platform dimensions). The global `bandit_dimensions.yaml` will not be transplanted.

## Discount Timing Bug

In `bandit_optimizer.py`, `_apply_discount()` is called INSIDE the update loop, before each posterior update. This is correct — discount happens before the new observation is incorporated, preventing the new data from being immediately decayed.
