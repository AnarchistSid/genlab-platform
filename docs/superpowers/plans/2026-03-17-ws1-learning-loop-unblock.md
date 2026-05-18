# WS1: Learning Loop Unblock — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix FetchInsights returning 0 for all niches so the learning loop receives post-publish metrics and the bandit can learn.

**Architecture:** Rewrite the pipeline FetchInsights stage to query SharePoint Publishing_Analytics for previously published posts (instead of checking current-run context). Add missing 48h/168h collection plists. Wire Twitter into metric_collector. Fix Prefect cache serialization.

**Tech Stack:** Python, SharePoint Graph API (via BacklogClient), Prefect, launchd plists

**Spec:** `docs/superpowers/specs/2026-03-17-ws1-learning-loop-unblock-design.md`

---

## Chunk 1: Fix FetchInsights Pipeline Stage

### Task 1: Rewrite FetchInsights.execute()

**Files:**
- Modify: `genlab-core/src/genlab_core/pipeline/stages/fetch_insights.py`
- Test: `genlab-core/tests/pipeline/test_fetch_insights.py`

- [ ] **Step 1: Write failing test for SharePoint-based fetch**

```python
# genlab-core/tests/pipeline/test_fetch_insights.py
"""Tests for the FetchInsights pipeline stage."""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

from genlab_core.pipeline.stages.fetch_insights import FetchInsights


def _make_pub_record(post_id, platform, niche_id, hours_ago=12, metrics_fetched=""):
    pub_dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "id": f"rec_{post_id}",
        "fields": {
            "post_id": post_id,
            "platform": platform,
            "niche_id": niche_id,
            "published_at": pub_dt.isoformat(),
            "metrics_fetched": metrics_fetched,
        },
    }


class TestFetchInsightsSharePoint:
    def test_fetches_previously_published_posts(self):
        """FetchInsights should query SharePoint for posts published 6h-7d ago."""
        mock_client = MagicMock()
        mock_client.publishing_analytics.all.return_value = [
            _make_pub_record("yt_abc123", "youtube", "gaming", hours_ago=12),
            _make_pub_record("ig_def456", "instagram", "gaming", hours_ago=24),
        ]

        context = {
            "niche_id": "gaming",
            "backlog_client": mock_client,
            "stories": [],  # Empty — current run has no published posts
            "niche_config": {},
        }

        stage = FetchInsights()
        result = stage.execute(context)

        stats = result["run_stats"]["insights"]
        # Should have attempted 2 fetches, not skipped everything
        assert stats["fetched"] + stats["errors"] > 0 or stats["skipped"] < 2

    def test_skips_already_fetched(self):
        """Posts with metrics_fetched set should be skipped."""
        mock_client = MagicMock()
        mock_client.publishing_analytics.all.return_value = [
            _make_pub_record("yt_abc", "youtube", "gaming", hours_ago=12,
                             metrics_fetched="2026-03-16T12:00:00+00:00"),
        ]

        context = {
            "niche_id": "gaming",
            "backlog_client": mock_client,
            "stories": [],
            "niche_config": {},
        }

        stage = FetchInsights()
        result = stage.execute(context)
        assert result["run_stats"]["insights"]["skipped"] == 1

    def test_skips_too_young(self):
        """Posts published less than 6h ago should be skipped."""
        mock_client = MagicMock()
        mock_client.publishing_analytics.all.return_value = [
            _make_pub_record("yt_abc", "youtube", "gaming", hours_ago=2),
        ]

        context = {
            "niche_id": "gaming",
            "backlog_client": mock_client,
            "stories": [],
            "niche_config": {},
        }

        stage = FetchInsights()
        result = stage.execute(context)
        assert result["run_stats"]["insights"]["skipped"] == 1

    def test_no_backlog_client_returns_context(self):
        """Without a backlog_client, stage should be a no-op."""
        context = {"niche_id": "gaming", "stories": []}
        stage = FetchInsights()
        result = stage.execute(context)
        assert result is context
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/pipeline/test_fetch_insights.py -v --tb=short 2>&1 | tail -20
```

Expected: FAIL (current implementation checks context stories, not SharePoint)

- [ ] **Step 3: Rewrite FetchInsights.execute()**

Replace the entire `execute` method in `genlab-core/src/genlab_core/pipeline/stages/fetch_insights.py`:

```python
def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
    niche_id = context.get("niche_id", "")
    client = context.get("backlog_client")
    config = context.get("niche_config", {})

    if not client:
        logger.info("[FetchInsights] No backlog_client — skipping")
        return context

    now = datetime.now(timezone.utc)
    fetched = 0
    skipped = 0
    errors = 0
    platform_stats: Dict[str, Dict[str, int]] = {}

    # Query Publishing_Analytics for posts in this niche
    try:
        formula = f"AND({{niche_id}}='{niche_id}')"
        records = client.publishing_analytics.all(formula=formula)
    except Exception:
        logger.exception("[FetchInsights] Failed to query Publishing_Analytics")
        context.setdefault("run_stats", {})["insights"] = {
            "fetched": 0, "skipped": 0, "errors": 1, "platforms": {},
        }
        return context

    for record in records:
        fields = record.get("fields", {})
        post_id = fields.get("post_id", "")
        platform = fields.get("platform", "")
        published_at = fields.get("published_at", "")
        metrics_fetched = fields.get("metrics_fetched", "")

        # Skip already fetched
        if metrics_fetched:
            skipped += 1
            continue

        # Skip if no post_id or platform
        if not post_id or not platform:
            skipped += 1
            continue

        # Parse publish time and check age
        try:
            if isinstance(published_at, str):
                pub_dt = datetime.fromisoformat(
                    published_at.replace("Z", "+00:00")
                )
            else:
                pub_dt = published_at
        except (ValueError, TypeError):
            skipped += 1
            continue

        age_hours = (now - pub_dt).total_seconds() / 3600
        if age_hours < MIN_DELAY_HOURS:
            skipped += 1
            continue
        if age_hours > MAX_WARM_DAYS * 24:
            skipped += 1
            continue

        # Fetch metrics
        stats = platform_stats.setdefault(
            platform, {"fetched": 0, "errors": 0},
        )
        try:
            metrics = self._fetch_platform(platform, post_id, config)
            if metrics:
                # Mark as fetched in SharePoint
                try:
                    client.publishing_analytics.update(
                        record["id"],
                        {"metrics_fetched": now.isoformat()},
                    )
                except Exception:
                    logger.warning("[FetchInsights] Failed to mark %s as fetched", post_id)
                stats["fetched"] += 1
                fetched += 1
            else:
                skipped += 1
        except Exception:
            logger.exception(
                "[FetchInsights] %s fetch failed for post %s",
                platform, post_id,
            )
            stats["errors"] += 1
            errors += 1

    logger.info(
        "[FetchInsights] %d fetched, %d skipped, %d errors | %s",
        fetched, skipped, errors,
        {k: v for k, v in platform_stats.items() if v["fetched"] or v["errors"]},
    )
    context.setdefault("run_stats", {})["insights"] = {
        "fetched": fetched,
        "skipped": skipped,
        "errors": errors,
        "platforms": platform_stats,
    }
    return context
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/pipeline/test_fetch_insights.py -v --tb=short
```

Expected: 4 PASSED

- [ ] **Step 5: Run existing tests to verify no regressions**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/ -x -q --tb=short 2>&1 | tail -10
```

- [ ] **Step 6: Commit**

```bash
git add genlab-core/src/genlab_core/pipeline/stages/fetch_insights.py genlab-core/tests/pipeline/test_fetch_insights.py
git commit -m "fix(insights): rewrite FetchInsights to query SharePoint instead of current-run context

The stage was checking context['stories'] for published_platforms, but
current-run stories haven't been published yet. Now queries
Publishing_Analytics for posts published 6h-7d ago."
```

---

### Task 2: Add _fetch_twitter to metric_collector + fix Prefect cache

**Files:**
- Modify: `genlab-core/src/genlab_core/learning/metric_collector.py`
- Test: `genlab-core/tests/learning/test_metric_collector.py`

- [ ] **Step 1: Write failing test for Twitter metrics**

```python
# Add to genlab-core/tests/learning/test_metric_collector.py
from unittest.mock import patch, MagicMock

def test_fetch_twitter_returns_metrics():
    """_fetch_twitter should return impression/retweet/reply counts."""
    from genlab_core.learning.metric_collector import _fetch_twitter
    # _fetch_twitter should exist and be callable
    assert callable(_fetch_twitter)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/learning/test_metric_collector.py::test_fetch_twitter_returns_metrics -v --tb=short
```

Expected: ImportError or AttributeError (_fetch_twitter doesn't exist)

- [ ] **Step 3: Add _fetch_twitter to metric_collector.py**

Add after `_fetch_threads` function (around line 293):

```python
def _fetch_twitter(post_id: str, niche_id: str = "") -> dict:
    """Fetch X/Twitter metrics for a tweet."""
    bearer = os.environ.get("X_BEARER_TOKEN", "")
    if not bearer:
        logger.debug("[metric_collector] X_BEARER_TOKEN not set — skipping")
        return {}
    try:
        url = f"https://api.twitter.com/2/tweets/{post_id}"
        params = "tweet.fields=public_metrics"
        req = urllib.request.Request(
            f"{url}?{params}",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        metrics = data.get("data", {}).get("public_metrics", {})
        return {
            "impressions": metrics.get("impression_count", 0),
            "retweets": metrics.get("retweet_count", 0),
            "replies": metrics.get("reply_count", 0),
            "likes": metrics.get("like_count", 0),
            "quotes": metrics.get("quote_count", 0),
        }
    except Exception:
        logger.exception("[metric_collector] X/Twitter fetch failed for %s", post_id)
        return {}
```

Also add `"twitter": _fetch_twitter` to the platform fetcher dispatch dict (around line 75-81):

```python
    "twitter": _fetch_twitter,
    "x": _fetch_twitter,
```

- [ ] **Step 4: Add persist_result=False to tasks using BacklogClient**

On `process_pending_task` (line 338), change decorator:

```python
@task(name="process_pending_task", persist_result=False, **_TASK_DEFAULTS)
```

- [ ] **Step 5: Run tests to verify**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/learning/test_metric_collector.py -v --tb=short 2>&1 | tail -15
```

- [ ] **Step 6: Commit**

```bash
git add genlab-core/src/genlab_core/learning/metric_collector.py genlab-core/tests/learning/test_metric_collector.py
git commit -m "feat(metrics): add Twitter fetcher to metric_collector + fix Prefect cache

- Add _fetch_twitter() for X/Twitter public_metrics
- Wire into platform dispatch dict (twitter + x keys)
- Add persist_result=False to process_pending_task (BacklogClient RLock not serializable)"
```

---

## Chunk 2: Add 48h/168h Collection Plists

### Task 3: Create 10 new launchd plists

**Files:**
- Create: 10 plist files in `~/Library/LaunchAgents/`

- [ ] **Step 1: Read existing 6h plist as template**

```bash
cat ~/Library/LaunchAgents/com.genlab.fetch-insights-gaming-6h.plist
```

- [ ] **Step 2: Generate 48h plists for all 5 niches**

Create plists following the same pattern but with `--window 48` arg. Schedule staggered at +3min intervals starting at 14:00 UTC (publish+48h for 12:00 IST publish):

| Niche | 48h Plist | Hour:Min UTC |
|---|---|---|
| ai_creators | com.genlab.fetch-insights-ai-creators-48h.plist | 14:15 |
| gaming | com.genlab.fetch-insights-gaming-48h.plist | 14:18 |
| sports | com.genlab.fetch-insights-sports-48h.plist | 14:21 |
| movies | com.genlab.fetch-insights-movies-48h.plist | 14:24 |
| anime | com.genlab.fetch-insights-anime-48h.plist | 14:27 |

- [ ] **Step 3: Generate 168h plists for all 5 niches**

Same pattern with `--window 168`, scheduled at 20:00 UTC:

| Niche | 168h Plist | Hour:Min UTC |
|---|---|---|
| ai_creators | com.genlab.fetch-insights-ai-creators-168h.plist | 20:15 |
| gaming | com.genlab.fetch-insights-gaming-168h.plist | 20:18 |
| sports | com.genlab.fetch-insights-sports-168h.plist | 20:21 |
| movies | com.genlab.fetch-insights-movies-168h.plist | 20:24 |
| anime | com.genlab.fetch-insights-anime-168h.plist | 20:27 |

- [ ] **Step 4: Load all new plists**

```bash
for f in ~/Library/LaunchAgents/com.genlab.fetch-insights-*-48h.plist ~/Library/LaunchAgents/com.genlab.fetch-insights-*-168h.plist; do
  launchctl load "$f" 2>/dev/null
done
launchctl list | grep fetch-insights | wc -l
```

Expected: 20 total (was 10)

- [ ] **Step 5: Commit plist sources**

```bash
git add genlab-core/runbooks/com.genlab.fetch-insights-*
git commit -m "feat(metrics): add 48h and 168h insight collection plists for all niches

10 new plists (5 niches x 2 windows). Staggered 3min apart.
48h at ~14:00 UTC, 168h at ~20:00 UTC."
```
