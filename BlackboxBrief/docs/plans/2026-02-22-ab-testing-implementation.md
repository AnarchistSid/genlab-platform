# A/B Testing Framework — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a full A/B testing framework that creates variant blueprints, fetches platform insights, runs statistical significance tests, and auto-applies winners to config.

**Architecture:** Dedicated AB_Tests Microsoft Lists table + cloned blueprints. Each test creates a control (rank #1 hook / current style) and treatment (rank #2 hook / alternate style) as separate blueprints that flow through the normal pipeline. `fetch_insights.py` pulls metrics from Instagram/Twitter/YouTube after 48h, and `ABTestManager.declare_winner()` runs a Mann-Whitney U test.

**Tech Stack:** Python 3.13, microsoft-graph-sdk, tweepy, scipy (Mann-Whitney U), requests, pyyaml

**Design doc:** `docs/plans/2026-02-22-ab-testing-framework-design.md`

---

### Task 1: Config — `config/ab_tests.yaml`

**Files:**
- Create: `config/ab_tests.yaml`

**Step 1: Create the config file**

```yaml
# A/B Testing Configuration
#
# Controls test creation, measurement, and auto-application of results.
# Imported by: execution/ab_testing.py, execution/fetch_insights.py
#
# Cost: ~$0.01 per non-background test, ~$0.05 per background test
# Cap: max_tests_per_day (default 1) to control costs + feed quality

ab_testing:
  enabled: true
  max_tests_per_day: 1
  measurement_delay_hours: 48       # wait before fetching insights
  min_impressions_per_variant: 10   # below this → INSUFFICIENT_DATA
  significance_threshold: 0.05     # p-value for declaring winner

  # Auto-create: rotate test types daily
  auto_create:
    enabled: true
    test_type_rotation:
      - hook
      - visual_style
      - posting_time
      - mid_hook
      - background

  # Per-type variant definitions
  hook_variants:
    control: "top_scored"           # rank #1 from generate_hooks.py
    treatment: "second_scored"      # rank #2 (different formula category preferred)

  visual_variants:
    control:
      highlight_color: "#FFD700"    # gold (current)
    treatment:
      highlight_color: "#06b6d4"    # cyan

  timing_variants:
    control_hour: 10
    treatment_hour: 14
    offset_hours: 4

  mid_hook_variants:
    control:
      enabled: true
      appear_at_ratio: 0.45
    treatment:
      enabled: false

  background_variants:
    control: "ai_generated"
    treatment: "gradient_fallback"
```

**Step 2: Validate config loads**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && venv/bin/python -c "import yaml; c=yaml.safe_load(open('config/ab_tests.yaml')); print(c['ab_testing']['max_tests_per_day']); print('OK')"`
Expected: `1` then `OK`

**Step 3: Commit**

```bash
git add config/ab_tests.yaml
git commit -m "feat: add A/B testing config (ab_tests.yaml)"
```

---

### Task 2: Microsoft Lists Schema — `setup/create_ab_tests_table.py`

**Files:**
- Create: `setup/create_ab_tests_table.py`
- Reference: `setup/create_publishing_analytics_table.py` (follow same pattern)

**Step 1: Create the setup script**

Follow the exact pattern from `create_publishing_analytics_table.py`:
- Same `api_call()` helper with retry on 429
- Same two-pass approach (create table, then add linked record fields)
- Same `--dry-run` flag support

Table: `AB_Tests` with fields:
- `test_id` (singleLineText, primary key)
- `test_type` (singleSelect: hook, visual_style, mid_hook, background, posting_time)
- `story_id` (singleLineText)
- `variant_a_id` (singleLineText)
- `variant_b_id` (singleLineText)
- `variant_a_desc` (singleLineText)
- `variant_b_desc` (singleLineText)
- `variant_a_metrics` (multilineText — stores JSON)
- `variant_b_metrics` (multilineText — stores JSON)
- `winner` (singleSelect: A, B, TIE, INSUFFICIENT_DATA)
- `confidence` (number, precision=4)
- `status` (singleSelect: CREATED, PUBLISHING, MEASURING, COMPLETE, CANCELLED)
- `config_updated` (checkbox)
- `created_at` (dateTime)
- `completed_at` (dateTime)
- `notes` (multilineText)

Also add two fields to the **existing Blueprints table** (via PATCH to Meta API):
- `ab_test_id` (singleLineText)
- `ab_variant` (singleSelect: control, treatment)

**Step 2: Verify it compiles**

Run: `venv/bin/python -m py_compile setup/create_ab_tests_table.py`

**Step 3: Commit** (do NOT run the script yet — that happens manually)

```bash
git add setup/create_ab_tests_table.py
git commit -m "feat: add AB_Tests Microsoft Lists table setup script"
```

---

### Task 3: Core Framework — `execution/ab_testing.py`

**Files:**
- Create: `execution/ab_testing.py`
- Create: `tests/test_ab_testing.py`
- Reference: `execution/utils/backlog_client.py:138-206` (create_blueprint, find_blueprint)
- Reference: `execution/generate_hooks.py:1169-1188` (best_hook_for_story, generate_hook_variants, score_hooks)
- Reference: `execution/utils/stable_ids.py` (sha256-based ID generation)

This is the largest task. Build incrementally with tests.

**Step 1: Write test for ABTest dataclass**

File: `tests/test_ab_testing.py`

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution.ab_testing import ABTest

def test_ab_test_dataclass():
    t = ABTest(
        test_id="abc123",
        test_type="hook",
        story_id="story_001",
        variant_a_id="cand_a",
        variant_b_id="cand_b",
    )
    assert t.test_id == "abc123"
    assert t.status == "CREATED"
    assert t.winner == ""
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_ab_testing.py::test_ab_test_dataclass -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'execution.ab_testing'`

**Step 3: Write ABTest dataclass + config loader**

File: `execution/ab_testing.py`

```python
#!/usr/bin/env python3
"""A/B Testing Framework for content optimization.

Creates variant blueprints, tracks tests in Microsoft Lists AB_Tests table,
measures results via fetch_insights.py, declares winners with statistical
significance, and auto-applies winning patterns to config.

Usage:
    python execution/ab_testing.py --auto-create --run-id RUN_ID
    python execution/ab_testing.py --measure-all
    python execution/ab_testing.py --create-hook-test --story-id STORY_ID
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────

def _load_ab_config() -> Dict:
    """Load config/ab_tests.yaml → ab_testing section."""
    config_path = PROJECT_ROOT / "config" / "ab_tests.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        full = yaml.safe_load(f) or {}
    return full.get("ab_testing", {})

# ── Data model ──────────────────────────────────────────────

@dataclass
class ABTest:
    test_id: str
    test_type: str                          # hook | visual_style | mid_hook | background | posting_time
    story_id: str
    variant_a_id: str = ""                  # control candidate_id
    variant_b_id: str = ""                  # treatment candidate_id
    variant_a_desc: str = ""
    variant_b_desc: str = ""
    variant_a_config: Dict = field(default_factory=dict)
    variant_b_config: Dict = field(default_factory=dict)
    status: str = "CREATED"
    winner: str = ""
    confidence: float = 0.0
    created_at: str = ""
    completed_at: str = ""
    notes: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_ab_testing.py::test_ab_test_dataclass -v`
Expected: PASS

**Step 5: Write test for generate_test_id**

```python
from execution.ab_testing import generate_test_id

def test_generate_test_id_deterministic():
    id1 = generate_test_id("story_001", "hook", "2026-02-22")
    id2 = generate_test_id("story_001", "hook", "2026-02-22")
    assert id1 == id2
    assert len(id1) == 16

def test_generate_test_id_different_inputs():
    id1 = generate_test_id("story_001", "hook", "2026-02-22")
    id2 = generate_test_id("story_002", "hook", "2026-02-22")
    assert id1 != id2
```

**Step 6: Implement generate_test_id**

```python
def generate_test_id(story_id: str, test_type: str, date_str: str) -> str:
    raw = f"{story_id}:{test_type}:{date_str}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

**Step 7: Run and verify PASS**

**Step 8: Write test for generate_variant_id**

```python
from execution.ab_testing import generate_variant_id

def test_variant_id_differs_from_source():
    source = "original_candidate_id"
    variant = generate_variant_id(source, "treatment")
    assert variant != source
    assert len(variant) == 16
```

**Step 9: Implement generate_variant_id**

```python
def generate_variant_id(source_candidate_id: str, variant_label: str) -> str:
    raw = f"{source_candidate_id}:{variant_label}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

**Step 10: Write test for declare_winner (significant result)**

```python
from execution.ab_testing import declare_winner

def test_winner_significant():
    """When variant B has clearly higher engagement, B wins."""
    metrics_a = {"engagement_rates": [0.01, 0.02, 0.01, 0.03, 0.02, 0.01, 0.02, 0.01, 0.03, 0.02]}
    metrics_b = {"engagement_rates": [0.08, 0.10, 0.09, 0.12, 0.07, 0.11, 0.09, 0.08, 0.10, 0.11]}
    result = declare_winner(metrics_a, metrics_b, min_samples=5, threshold=0.05)
    assert result["winner"] == "B"
    assert result["p_value"] < 0.05

def test_winner_insufficient_data():
    """Below min_samples → INSUFFICIENT_DATA."""
    metrics_a = {"engagement_rates": [0.01, 0.02]}
    metrics_b = {"engagement_rates": [0.08]}
    result = declare_winner(metrics_a, metrics_b, min_samples=5)
    assert result["winner"] == "INSUFFICIENT_DATA"

def test_winner_tie():
    """Similar engagement → TIE."""
    metrics_a = {"engagement_rates": [0.05, 0.04, 0.06, 0.05, 0.04]}
    metrics_b = {"engagement_rates": [0.05, 0.05, 0.04, 0.06, 0.05]}
    result = declare_winner(metrics_a, metrics_b, min_samples=5)
    assert result["winner"] in ("TIE", "A", "B")  # could go either way
    if result["winner"] == "TIE":
        assert result["p_value"] >= 0.05
```

**Step 11: Implement declare_winner**

```python
def declare_winner(
    metrics_a: Dict,
    metrics_b: Dict,
    min_samples: int = 10,
    threshold: float = 0.05,
) -> Dict:
    """Run Mann-Whitney U test on engagement rates.

    Returns: {winner: "A"|"B"|"TIE"|"INSUFFICIENT_DATA", p_value: float, ...}
    """
    rates_a = metrics_a.get("engagement_rates", [])
    rates_b = metrics_b.get("engagement_rates", [])

    if len(rates_a) < min_samples or len(rates_b) < min_samples:
        return {
            "winner": "INSUFFICIENT_DATA",
            "p_value": 1.0,
            "samples_a": len(rates_a),
            "samples_b": len(rates_b),
            "min_samples": min_samples,
        }

    from scipy.stats import mannwhitneyu

    try:
        stat, p_value = mannwhitneyu(rates_a, rates_b, alternative="two-sided")
    except ValueError:
        # All values identical
        return {"winner": "TIE", "p_value": 1.0, "note": "identical distributions"}

    median_a = sorted(rates_a)[len(rates_a) // 2]
    median_b = sorted(rates_b)[len(rates_b) // 2]

    if p_value < threshold:
        winner = "A" if median_a > median_b else "B"
    else:
        winner = "TIE"

    return {
        "winner": winner,
        "p_value": round(p_value, 6),
        "median_a": round(median_a, 6),
        "median_b": round(median_b, 6),
        "samples_a": len(rates_a),
        "samples_b": len(rates_b),
    }
```

**Step 12: Run tests and verify PASS**

Run: `venv/bin/python -m pytest tests/test_ab_testing.py -v`

**Step 13: Write test for daily cap enforcement**

```python
from unittest.mock import MagicMock, patch
from execution.ab_testing import ABTestManager

def test_max_tests_per_day_enforced():
    """Refuses to create test when daily cap reached."""
    mock_client = MagicMock()
    manager = ABTestManager(mock_client)
    # Simulate 1 test already created today
    mock_client.ab_tests = MagicMock()
    mock_client.ab_tests.all.return_value = [{"fields": {"status": "CREATED"}}]
    with patch.object(manager, '_load_todays_tests', return_value=1):
        result = manager.can_create_test()
    assert result is False
```

**Step 14: Implement ABTestManager class**

The full ABTestManager with:
- `__init__(self, backlog_client)` — loads config, stores client
- `can_create_test()` → bool — checks daily cap
- `_load_todays_tests()` → int — counts today's tests from Microsoft Lists
- `_clone_blueprint(source_candidate_id, overrides, variant_label, ab_test_id)` → str — clones a blueprint, returns new candidate_id
- `create_test(story_id, test_type, variant_a_config, variant_b_config)` → ABTest
- `create_hook_test(story_id)` → ABTest — uses `generate_hook_variants()` + `score_hooks()`
- `create_visual_test(story_id)` → ABTest
- `create_timing_test(story_id)` → ABTest
- `create_mid_hook_test(story_id)` → ABTest
- `create_background_test(story_id)` → ABTest
- `measure_results(test_id)` → Dict
- `measure_all_running()` — measures all MEASURING-status tests
- `auto_create_daily(run_id)` — picks today's test type from rotation + top story

**Key implementation detail for `_clone_blueprint`:**
```python
def _clone_blueprint(self, source_candidate_id, overrides, variant_label, ab_test_id):
    source = self.client.find_blueprint_by_candidate_id(source_candidate_id)
    if not source:
        raise ValueError(f"Blueprint {source_candidate_id} not found")

    new_id = generate_variant_id(source_candidate_id, variant_label)
    fields = dict(source["fields"])

    # Remove Microsoft Lists-internal fields
    for key in ("id", "createdTime", "story", "template", "blueprint"):
        fields.pop(key, None)

    # Apply overrides
    fields["candidate_id"] = new_id
    fields["ab_test_id"] = ab_test_id
    fields["ab_variant"] = variant_label
    fields["status"] = "INTEL_READY"
    fields.update(overrides)

    # Re-link story by story_id lookup
    story_id = fields.get("story_id") or source["fields"].get("story_id", "")
    story_record = self.client.find_story_by_story_id(story_id) if story_id else None

    # Create as new blueprint
    self.client.blueprints.create(fields, typecast=True)
    return new_id
```

**Key implementation detail for `create_hook_test`:**
```python
def create_hook_test(self, story_id):
    from execution.generate_hooks import generate_hook_variants, score_hooks

    source = self.client.find_blueprint_by_candidate_id(
        # Find the first blueprint for this story
        ...
    )
    story = {"title": source["fields"].get("topic", ""), "summary": ""}
    variants = generate_hook_variants(story, count=5)
    scored = score_hooks(variants)

    if len(scored) < 2:
        raise ValueError("Need at least 2 hook variants")

    control_hook = scored[0]  # rank #1
    treatment_hook = scored[1]  # rank #2

    return self.create_test(
        story_id=story_id,
        test_type="hook",
        variant_a_config={"hook": control_hook["hook"]},
        variant_b_config={"hook": treatment_hook["hook"]},
        variant_a_desc=f"{control_hook.get('category', 'unknown')} hook",
        variant_b_desc=f"{treatment_hook.get('category', 'unknown')} hook",
    )
```

**Step 15: Add CLI**

```python
def main():
    parser = argparse.ArgumentParser(description="A/B Testing Framework")
    parser.add_argument("--auto-create", action="store_true")
    parser.add_argument("--measure-all", action="store_true")
    parser.add_argument("--create-hook-test", action="store_true")
    parser.add_argument("--story-id", type=str)
    parser.add_argument("--run-id", type=str, default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(override=True)
    from execution.utils.backlog_client import BacklogClient
    client = BacklogClient()
    manager = ABTestManager(client)

    if args.auto_create:
        manager.auto_create_daily(args.run_id)
    elif args.measure_all:
        manager.measure_all_running()
    elif args.create_hook_test and args.story_id:
        test = manager.create_hook_test(args.story_id)
        print(f"Created hook test: {test.test_id}")

if __name__ == "__main__":
    main()
```

**Step 16: Run all tests**

Run: `venv/bin/python -m pytest tests/test_ab_testing.py -v`

**Step 17: Commit**

```bash
git add execution/ab_testing.py tests/test_ab_testing.py
git commit -m "feat: add ABTest + ABTestManager with statistical analysis"
```

---

### Task 4: Insights Fetcher — `execution/fetch_insights.py`

**Files:**
- Create: `execution/fetch_insights.py`
- Create: `tests/test_fetch_insights.py`
- Reference: `execution/utils/backlog_client.py:477-581` (log_publish_result, get_publishing_analytics)
- Reference: Instagram API: `GET /{post_id}/insights?metric=reach,saved,shares,total_interactions`
- Reference: Twitter API: `client.get_tweets([id], tweet_fields=["public_metrics"], user_auth=True)`
- Reference: YouTube API: `GET youtube/v3/videos?part=statistics&id={video_id}`

**API details verified on 2026-02-22:**
- Instagram token: `IGAAUH...` (IG User Token) — insights work for VIDEO (Reels)
- Twitter: needs `user_auth=True` on tweepy Client
- YouTube: `youtube.force-ssl` scope works for `videos.list?part=statistics`

**Step 1: Write test for Instagram insights fetcher**

File: `tests/test_fetch_insights.py`

```python
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution.fetch_insights import fetch_instagram_insights

def test_instagram_insights_parses_response():
    """Correctly parses IG insights API response."""
    mock_basic = MagicMock()
    mock_basic.json.return_value = {"like_count": 5, "comments_count": 2}
    mock_basic.status_code = 200

    mock_insights = MagicMock()
    mock_insights.json.return_value = {
        "data": [
            {"name": "reach", "values": [{"value": 150}]},
            {"name": "saved", "values": [{"value": 3}]},
            {"name": "shares", "values": [{"value": 1}]},
            {"name": "total_interactions", "values": [{"value": 11}]},
        ]
    }
    mock_insights.status_code = 200

    with patch("execution.fetch_insights.requests.get", side_effect=[mock_basic, mock_insights]):
        result = fetch_instagram_insights("18099278755903863", "fake_token")

    assert result["likes"] == 5
    assert result["comments"] == 2
    assert result["reach"] == 150
    assert result["saves"] == 3
    assert result["shares"] == 1
    assert result["total_interactions"] == 11
    assert result["engagement"] == 11  # total_interactions
    assert result["engagement_rate"] > 0  # engagement / reach
```

**Step 2: Run test to verify FAIL**

**Step 3: Implement fetch_instagram_insights**

```python
def fetch_instagram_insights(post_id: str, ig_token: str) -> Dict:
    """Fetch Instagram post insights.

    Makes 2 API calls:
    1. GET /{post_id}?fields=like_count,comments_count
    2. GET /{post_id}/insights?metric=reach,saved,shares,total_interactions

    Returns dict with: likes, comments, reach, saves, shares,
    total_interactions, engagement, engagement_rate.
    """
    import requests

    result = {"post_id": post_id, "platform": "instagram"}

    # Basic metrics (likes, comments)
    r = requests.get(
        f"https://graph.instagram.com/{post_id}",
        params={"fields": "like_count,comments_count", "access_token": ig_token},
    )
    if r.status_code == 200:
        data = r.json()
        result["likes"] = data.get("like_count", 0)
        result["comments"] = data.get("comments_count", 0)

    # Insights (reach, saved, shares, total_interactions)
    r2 = requests.get(
        f"https://graph.instagram.com/{post_id}/insights",
        params={
            "metric": "reach,saved,shares,total_interactions",
            "access_token": ig_token,
        },
    )
    if r2.status_code == 200:
        for item in r2.json().get("data", []):
            name = item["name"]
            value = item["values"][0]["value"] if item.get("values") else 0
            result[name] = value
            if name == "saved":
                result["saves"] = value

    # Computed
    engagement = result.get("total_interactions", 0)
    reach = result.get("reach", 1)
    result["engagement"] = engagement
    result["engagement_rate"] = round(engagement / max(reach, 1), 6)

    return result
```

**Step 4: Write test for Twitter insights**

```python
def test_twitter_insights_parses_response():
    mock_client = MagicMock()
    mock_tweet = MagicMock()
    mock_tweet.public_metrics = {
        "impression_count": 200,
        "like_count": 8,
        "retweet_count": 2,
        "reply_count": 1,
        "quote_count": 0,
        "bookmark_count": 3,
    }
    mock_client.get_tweets.return_value = MagicMock(data=[mock_tweet])

    from execution.fetch_insights import fetch_twitter_insights
    result = fetch_twitter_insights("2025470863089566075", mock_client)
    assert result["impressions"] == 200
    assert result["likes"] == 8
    assert result["engagement"] == 14  # likes + retweets + replies + quotes + bookmarks
```

**Step 5: Implement fetch_twitter_insights**

```python
def fetch_twitter_insights(tweet_id: str, tweepy_client) -> Dict:
    result = {"post_id": tweet_id, "platform": "twitter"}
    response = tweepy_client.get_tweets(
        [tweet_id],
        tweet_fields=["public_metrics"],
        user_auth=True,
    )
    if response.data:
        pm = response.data[0].public_metrics or {}
        result["impressions"] = pm.get("impression_count", 0)
        result["likes"] = pm.get("like_count", 0)
        result["retweets"] = pm.get("retweet_count", 0)
        result["replies"] = pm.get("reply_count", 0)
        result["quotes"] = pm.get("quote_count", 0)
        result["bookmarks"] = pm.get("bookmark_count", 0)
        result["engagement"] = (
            result["likes"] + result["retweets"] + result["replies"]
            + result["quotes"] + result["bookmarks"]
        )
        result["reach"] = result["impressions"]
        result["engagement_rate"] = round(
            result["engagement"] / max(result["reach"], 1), 6
        )
    return result
```

**Step 6: Write test for YouTube insights**

```python
def test_youtube_insights_parses_response():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "items": [{"statistics": {
            "viewCount": "500",
            "likeCount": "20",
            "commentCount": "5",
        }}]
    }
    mock_response.status_code = 200

    with patch("execution.fetch_insights.requests.get", return_value=mock_response):
        from execution.fetch_insights import fetch_youtube_insights
        result = fetch_youtube_insights("dQw4w9WgXcQ", "fake_token")

    assert result["views"] == 500
    assert result["likes"] == 20
    assert result["engagement"] == 25
```

**Step 7: Implement fetch_youtube_insights**

```python
def fetch_youtube_insights(video_id: str, access_token: str) -> Dict:
    import requests
    result = {"post_id": video_id, "platform": "youtube"}
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "statistics", "id": video_id},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if r.status_code == 200:
        items = r.json().get("items", [])
        if items:
            stats = items[0].get("statistics", {})
            result["views"] = int(stats.get("viewCount", 0))
            result["likes"] = int(stats.get("likeCount", 0))
            result["comments"] = int(stats.get("commentCount", 0))
            result["engagement"] = result["likes"] + result["comments"]
            result["reach"] = result["views"]
            result["engagement_rate"] = round(
                result["engagement"] / max(result["reach"], 1), 6
            )
    return result
```

**Step 8: Write test for fetch_all_pending_insights**

```python
def test_fetch_all_filters_by_age_and_null_engagement():
    """Only fetches for posts > 48h old with null engagement."""
    mock_client = MagicMock()
    # Simulate one SUCCESS record from 3 days ago with no engagement
    mock_client.get_publishing_analytics.return_value = [{
        "id": "rec123",
        "fields": {
            "post_id": "18099278755903863",
            "platform": "instagram",
            "status": "SUCCESS",
            "published_at": "2026-02-19T07:23:21.000Z",
            "candidate_id": "cand_001",
        }
    }]

    from execution.fetch_insights import fetch_all_pending_insights
    with patch("execution.fetch_insights.fetch_instagram_insights") as mock_ig:
        mock_ig.return_value = {"engagement": 5, "reach": 100, "engagement_rate": 0.05}
        results = fetch_all_pending_insights(mock_client, dry_run=True)

    assert len(results) == 1
```

**Step 9: Implement fetch_all_pending_insights + CLI**

The function:
1. Queries `Publishing_Analytics` where `status=SUCCESS`
2. Filters for records where `published_at` is > 48h ago AND `engagement` field is empty/0
3. For each, dispatches to the appropriate platform fetcher
4. Writes results back to Microsoft Lists via `publishing_analytics.update()`

CLI:
```
python execution/fetch_insights.py                  # fetch all pending
python execution/fetch_insights.py --dry-run        # preview what would be fetched
python execution/fetch_insights.py --platform ig    # instagram only
```

**Step 10: Run all tests**

Run: `venv/bin/python -m pytest tests/test_fetch_insights.py -v`

**Step 11: Commit**

```bash
git add execution/fetch_insights.py tests/test_fetch_insights.py
git commit -m "feat: add cross-platform insights fetcher (IG/Twitter/YouTube)"
```

---

### Task 5: Pipeline Integration — `daily_intel.sh`

**Files:**
- Modify: `runbooks/daily_intel.sh` (after line ~308, add 3 new steps)

**Step 1: Add A/B testing steps to daily_intel.sh**

After step 23 (the last `run_step`), add:

```bash
# ═══════════════════════════════════════════════════════════════
# Phase 5: A/B Testing + Insights
# ═══════════════════════════════════════════════════════════════
run_step 24 "Fetching platform insights"    false "$VENV_PYTHON" execution/fetch_insights.py --run-id "$RUN_ID"
run_step 25 "A/B: creating daily test"      false "$VENV_PYTHON" execution/ab_testing.py --auto-create --run-id "$RUN_ID"
run_step 26 "A/B: measuring results"        false "$VENV_PYTHON" execution/ab_testing.py --measure-all --run-id "$RUN_ID"
```

**Step 2: Verify script is still valid bash**

Run: `bash -n runbooks/daily_intel.sh`
Expected: no output (valid syntax)

**Step 3: Commit**

```bash
git add runbooks/daily_intel.sh
git commit -m "feat: add insights + A/B testing steps to daily pipeline"
```

---

### Task 6: Microsoft Lists Client Extension

**Files:**
- Modify: `execution/utils/backlog_client.py`

**Step 1: Add AB_Tests table to BacklogClient.__init__**

Near the existing table initializations (around line 75), add:

```python
# AB Testing
try:
    self.ab_tests = self.api.table(self.base_id, "AB_Tests")
except Exception:
    self.ab_tests = None  # Table may not exist yet
```

**Step 2: Add helper methods**

```python
def create_ab_test(self, test: Dict) -> Optional[str]:
    """Create an AB_Tests record. Returns record ID."""
    if not self.ab_tests:
        logger.warning("AB_Tests table not configured")
        return None
    try:
        record = self.ab_tests.create(test, typecast=True)
        return record["id"]
    except Exception as exc:
        logger.warning("Failed to create AB test: %s", exc)
        return None

def get_ab_tests(self, status: Optional[str] = None) -> List[Dict]:
    """Query AB_Tests with optional status filter."""
    if not self.ab_tests:
        return []
    formula = f"{{status}}='{status}'" if status else None
    return self.ab_tests.all(formula=formula, max_records=50)

def update_ab_test(self, test_id: str, fields: Dict):
    """Update an AB_Tests record by test_id."""
    if not self.ab_tests:
        return
    records = self.ab_tests.all(
        formula=f"{{test_id}}='{test_id}'", max_records=1,
    )
    if records:
        self.ab_tests.update(records[0]["id"], fields, typecast=True)
```

**Step 3: Run py_compile to verify**

Run: `venv/bin/python -m py_compile execution/utils/backlog_client.py`

**Step 4: Commit**

```bash
git add execution/utils/backlog_client.py
git commit -m "feat: add AB_Tests table methods to BacklogClient"
```

---

### Task 7: Install scipy dependency

**Files:**
- Modify: `requirements.txt`

**Step 1: Add scipy**

Add `scipy>=1.11.0` to requirements.txt (needed for Mann-Whitney U test).

**Step 2: Install**

Run: `venv/bin/pip install scipy`

**Step 3: Verify**

Run: `venv/bin/python -c "from scipy.stats import mannwhitneyu; print('OK')"`

**Step 4: Commit**

```bash
git add requirements.txt
git commit -m "deps: add scipy for A/B statistical significance testing"
```

---

### Task 8: Final Verification

**Step 1: Compile all new files**

```bash
venv/bin/python -m py_compile execution/ab_testing.py && \
venv/bin/python -m py_compile execution/fetch_insights.py && \
venv/bin/python -m py_compile setup/create_ab_tests_table.py
```

**Step 2: Run all new tests**

```bash
venv/bin/python -m pytest tests/test_ab_testing.py tests/test_fetch_insights.py -v
```

**Step 3: Run regression (existing tests still pass)**

```bash
venv/bin/python -m pytest tests/ -v --timeout=60
```

**Step 4: Config validation**

```bash
venv/bin/python -c "import yaml; c=yaml.safe_load(open('config/ab_tests.yaml')); ab=c['ab_testing']; assert ab['max_tests_per_day']==1; assert ab['significance_threshold']==0.05; print('OK')"
```

**Step 5: Import chain check**

```bash
venv/bin/python -c "from execution.ab_testing import ABTestManager, ABTest, declare_winner; print('ab_testing OK')"
venv/bin/python -c "from execution.fetch_insights import fetch_instagram_insights, fetch_twitter_insights, fetch_youtube_insights; print('fetch_insights OK')"
```

---

## Files Summary

| File | Action | Purpose |
|------|--------|---------|
| `config/ab_tests.yaml` | Create | Test config: caps, variants, rotation |
| `setup/create_ab_tests_table.py` | Create | Microsoft Lists AB_Tests table + Blueprint fields |
| `execution/ab_testing.py` | Create | ABTest + ABTestManager + statistical analysis |
| `execution/fetch_insights.py` | Create | Instagram/Twitter/YouTube insights fetcher |
| `tests/test_ab_testing.py` | Create | Unit tests for A/B framework |
| `tests/test_fetch_insights.py` | Create | Unit tests for insights fetcher |
| `execution/utils/backlog_client.py` | Modify | Add AB_Tests table methods |
| `runbooks/daily_intel.sh` | Modify | Add steps 24-26 |
| `requirements.txt` | Modify | Add scipy |
