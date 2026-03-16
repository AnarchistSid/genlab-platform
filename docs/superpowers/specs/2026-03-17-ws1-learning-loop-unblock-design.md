# WS1: Learning Loop Unblock

**Goal**: G3 Learning Loop 65% → 85%
**Effort**: ~3h
**Dependencies**: None (can run first or in parallel)

## Problem

`FetchInsights` pipeline stage returns `fetched=0, skipped=all` for every run across all 5 niches.

**Root cause**: The stage iterates `context['stories']` and checks `story.get("published_platforms")` — but stories in the current run haven't been published yet. They were just fetched, composed, and rendered in this pipeline execution. The stage is structurally incapable of finding published posts because it looks at the wrong data source.

The separate launchd-based `run_fetch_insights.py` correctly queries SharePoint `Publishing_Analytics` for previously published posts — this is the right approach. The pipeline stage needs to do the same.

Additionally:
- Only 6h and 24h collection plists exist (missing 48h and 168h)
- Twitter is not in `metric_collector.py` (only YT, IG, FB, TikTok, Threads)
- Prefect cache fails on BacklogClient (contains `_thread.RLock`, not serializable)

## Changes

### 1. Rewrite `FetchInsights.execute()` — `genlab-core/src/genlab_core/pipeline/stages/fetch_insights.py`

Replace current logic (iterating context stories) with SharePoint query:

```python
def execute(self, context):
    niche_id = context.get("niche_id", "")
    client = context.get("backlog_client")
    if not client:
        return context

    # Query Publishing_Analytics for posts published 6h-7d ago
    # that haven't had metrics fetched yet
    records = client.publishing_analytics.all(
        formula=f"AND({{niche_id}}='{niche_id}', {{metrics_fetched}}='')"
    )

    for record in records:
        platform = record["fields"].get("platform", "")
        post_id = record["fields"].get("post_id", "")
        published_at = record["fields"].get("published_at", "")
        # ... age check, fetch, update
```

This makes the pipeline stage query historical data, not current-run data.

### 2. Add 48h and 168h collection plists

Create 10 new plists (5 niches × 2 windows):
- `com.genlab.fetch-insights-{niche}-48h.plist` — runs at publish_time + 48h
- `com.genlab.fetch-insights-{niche}-168h.plist` — runs at publish_time + 168h

Schedule: stagger by 3 minutes per niche (same pattern as 6h/24h plists).

### 3. Add `_fetch_twitter()` to `metric_collector.py`

Wire Twitter metrics fetcher using the existing X/Twitter bearer token. Fetch: impressions, retweets, replies, likes, quote_tweets.

### 4. Fix Prefect cache serialization

In `metric_collector.py`, add `persist_result=False` to the Prefect task decorators that use BacklogClient:

```python
@task(persist_result=False)
def process_pending_task(task_record, shaper, ...):
```

## Files Modified

| File | Change |
|---|---|
| `genlab-core/src/genlab_core/pipeline/stages/fetch_insights.py` | Rewrite execute() to query SharePoint |
| `genlab-core/src/genlab_core/learning/metric_collector.py` | Add _fetch_twitter(), persist_result=False |
| `genlab-core/runbooks/` | 10 new plist files (48h + 168h × 5 niches) |

## Validation

- Run pipeline for any niche → FetchInsights should now report `fetched > 0` (if published posts exist)
- `launchctl list | grep fetch-insights` shows all 20 plists loaded
- `pytest genlab-core/tests/learning/test_metric_collector.py` passes
- Check `.logs/metric_collector.log` for successful Twitter fetches

## Risks

- If no posts have been published to SharePoint yet, FetchInsights will still return 0 (correct behavior — nothing to fetch)
- Twitter API rate limits (300 requests/15 min) — existing TokenBucket handles this
