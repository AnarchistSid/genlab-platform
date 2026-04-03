# A/B Testing Framework — Design Document

## Context

We generate content but don't test variants. Competitive gap: systematic optimization
through testing. Need to test hooks, visual styles, mid-reel hooks, backgrounds,
and posting times — then auto-apply winners to config.

**Constraints:**
- Cap: 1 A/B test per day (cost control)
- Small audience (1 IG follower, 14 Twitter, 1 YouTube subscriber)
- Accept noisy data now, build infrastructure for when audience grows
- Statistical significance via Mann-Whitney U (non-parametric, small-sample friendly)

**API verification (2026-02-22):**
- Instagram: `reach`, `saved`, `shares`, `total_interactions`, `like_count`, `comments_count` — all working
- Twitter: `impression_count`, `like_count`, `retweet_count`, `reply_count`, `bookmark_count` — working (needs `user_auth=True`)
- YouTube: `viewCount`, `likeCount`, `commentCount` via `videos.list?part=statistics` — working

---

## Architecture

```
Story → compose_blueprints → ABTestManager.create_test()
                                    ↓
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
              Blueprint A                     Blueprint B
              (control)                       (treatment)
                    ↓                               ↓
           write_post_content              write_post_content
           adapt_for_platforms             adapt_for_platforms
           render_visuals                  render_visuals
                    ↓                               ↓
         publish_all_platforms           publish_all_platforms
         (scheduled_for: T)              (scheduled_for: T + offset)
                    ↓                               ↓
                    └───────────────┬───────────────┘
                                    ▼
                         fetch_insights.py (cron, 48h delay)
                                    ↓
                         ABTestManager.measure_results()
                                    ↓
                         declare_winner (stats test)
                                    ↓
                         update_config (auto-apply winner)
```

---

## Data Model

### New Microsoft Lists Table: `AB_Tests`

| Field | Type | Description |
|-------|------|-------------|
| `test_id` | singleLineText (primary) | `sha256(story_id + test_type + created_date)[:16]` |
| `test_type` | singleSelect | `hook`, `visual_style`, `mid_hook`, `background`, `posting_time` |
| `story_id` | singleLineText | Source story both variants derive from |
| `variant_a_id` | singleLineText | Control blueprint `candidate_id` |
| `variant_b_id` | singleLineText | Treatment blueprint `candidate_id` |
| `variant_a_desc` | singleLineText | e.g. "Breaking hook formula" |
| `variant_b_desc` | singleLineText | e.g. "Curiosity gap hook formula" |
| `variant_a_metrics` | longText (JSON) | `{ig: {reach, likes, saves, shares}, tw: {...}, yt: {...}}` |
| `variant_b_metrics` | longText (JSON) | Same structure |
| `winner` | singleSelect | `A`, `B`, `TIE`, `INSUFFICIENT_DATA` |
| `confidence` | number | p-value (0.0 to 1.0) |
| `status` | singleSelect | `CREATED`, `PUBLISHING`, `MEASURING`, `COMPLETE`, `CANCELLED` |
| `config_updated` | checkbox | Whether winning pattern was applied |
| `created_at` | dateTime | Test creation timestamp |
| `completed_at` | dateTime | When results finalized |
| `notes` | longText | Analysis notes |

### New Fields on Blueprints Table

| Field | Type |
|-------|------|
| `ab_test_id` | singleLineText |
| `ab_variant` | singleSelect (`control`, `treatment`) |

---

## Files to Create

### 1. `execution/ab_testing.py` — Core framework

```python
@dataclass
class ABTest:
    test_id: str
    test_type: str           # hook | visual_style | mid_hook | background | posting_time
    story_id: str
    variant_a_id: str        # control candidate_id
    variant_b_id: str        # treatment candidate_id
    variant_a_config: Dict   # what makes this variant unique
    variant_b_config: Dict
    status: str              # CREATED | PUBLISHING | MEASURING | COMPLETE | CANCELLED
    winner: str              # A | B | TIE | INSUFFICIENT_DATA
    confidence: float        # p-value
    created_at: str
    completed_at: str

class ABTestManager:
    def __init__(self, backlog_client):
        self.client = backlog_client
        self.config = load_ab_config()

    # ── Test creation ──
    def create_test(story_id, test_type, variant_configs) → ABTest
    def create_hook_test(story_id) → ABTest
        # Uses generate_hooks.py to get rank #1 (control) + #2 (treatment)
        # Clones the blueprint, sets different hook on treatment
    def create_visual_test(story_id, control_color, treatment_color) → ABTest
    def create_timing_test(story_id, time_a, time_b) → ABTest
    def create_mid_hook_test(story_id) → ABTest
        # Control: with mid-hook, Treatment: without (or different timing)
    def create_background_test(story_id) → ABTest
        # Control: AI background, Treatment: gradient fallback

    # ── Blueprint cloning ──
    def _clone_blueprint(source_candidate_id, overrides, variant_label) → str
        # Fetches source from Microsoft Lists
        # New candidate_id = sha256(original + "variant_b")[:16]
        # Applies overrides (hook, scheduled_for, visual params)
        # Pushes to Microsoft Lists with ab_test_id + ab_variant set
        # Returns new candidate_id

    # ── Measurement ──
    def measure_results(test_id) → Dict
        # Queries Publishing_Analytics for both variant IDs
        # Aggregates metrics across platforms
        # Returns {variant_a: metrics, variant_b: metrics, comparison: {...}}
    def declare_winner(test_id) → str
        # Runs Mann-Whitney U test on engagement_rate
        # Minimum: 10 impressions per variant (configurable)
        # p < 0.05 → winner, else TIE or INSUFFICIENT_DATA
        # Updates AB_Tests record

    # ── Config feedback loop ──
    def update_config(test_id)
        # If hook test: adjust scoring_weights.yaml for winning formula
        # If visual test: update instagram_specs.yaml colors
        # If timing test: update publishing.yaml optimal_hours
        # If mid-hook test: toggle mid_reel_hook.enabled
        # Logs change to notes field
```

### 2. `execution/fetch_insights.py` — Platform insights fetcher

```python
def fetch_instagram_insights(post_id, ig_token) → Dict
    # GET /{post_id}?fields=like_count,comments_count
    # GET /{post_id}/insights?metric=reach,saved,shares,total_interactions
    # Returns: {likes, comments, reach, saves, shares, total_interactions}

def fetch_twitter_insights(tweet_id, tweepy_client) → Dict
    # GET /2/tweets/{id}?tweet.fields=public_metrics (user_auth=True)
    # Returns: {impressions, likes, retweets, replies, bookmarks, quotes}

def fetch_youtube_insights(video_id, access_token) → Dict
    # GET youtube/v3/videos?part=statistics&id={video_id}
    # Returns: {views, likes, comments}

def fetch_all_pending_insights()
    # Query Publishing_Analytics WHERE status=SUCCESS AND published_at > 48h ago
    #   AND engagement IS NULL (not yet fetched)
    # For each record: dispatch to platform-specific fetcher
    # Write results back to Publishing_Analytics fields
    # Also compute engagement_rate = engagement / max(reach, 1)

# CLI:
#   python execution/fetch_insights.py               # fetch all pending
#   python execution/fetch_insights.py --test-id X   # fetch for specific test
#   python execution/fetch_insights.py --dry-run      # show what would be fetched
```

### 3. `config/ab_tests.yaml` — Active test configuration

```yaml
ab_testing:
  enabled: true
  max_tests_per_day: 1
  measurement_delay_hours: 48
  min_impressions_per_variant: 10
  significance_threshold: 0.05    # p-value

  # Auto-create test from today's top story
  auto_create:
    enabled: true
    test_type_rotation:           # rotate daily
      - hook
      - visual_style
      - posting_time
      - mid_hook
      - background

  # Variant configs per test type
  hook_variants:
    control: "top_scored"         # rank #1 from generate_hooks.py
    treatment: "second_scored"    # rank #2 (different formula category)

  visual_variants:
    control:
      highlight_color: "#FFD700"  # gold (current)
    treatment:
      highlight_color: "#06b6d4"  # cyan

  timing_variants:
    control_hour: 10              # 10am
    treatment_hour: 14            # 2pm
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

### 4. `setup/create_ab_tests_table.py` — Microsoft Lists schema

Creates the `AB_Tests` table and adds `ab_test_id` + `ab_variant` fields
to the existing Blueprints table.

### 5. Pipeline integration — `daily_intel.sh`

```bash
# Step 21: A/B Testing (after publish, non-fatal)
run_step "ab_create"  "$PYTHON execution/ab_testing.py --auto-create" "non-fatal"
run_step "ab_fetch"   "$PYTHON execution/fetch_insights.py" "non-fatal"
run_step "ab_measure" "$PYTHON execution/ab_testing.py --measure-all" "non-fatal"
```

---

## Test Plan

### Unit tests (`tests/test_ab_testing.py`)
- `test_clone_blueprint_generates_unique_id` — new candidate_id differs from source
- `test_hook_test_uses_top_two_hooks` — control gets #1, treatment gets #2
- `test_winner_declaration_significant` — p < 0.05 declares correct winner
- `test_winner_declaration_insufficient` — < 10 impressions → INSUFFICIENT_DATA
- `test_winner_declaration_tie` — similar engagement → TIE
- `test_max_tests_per_day_enforced` — refuses to create if daily cap hit

### Integration tests (`tests/test_fetch_insights.py`)
- `test_instagram_insights_real_post` — fetches metrics for an existing post
- `test_twitter_insights_real_tweet` — fetches metrics with user_auth=True
- `test_youtube_insights_channel_stats` — at least connects and gets channel info
- `test_writes_to_lists` — mock Microsoft Lists, verify fields written

### Verification
```bash
python -m py_compile execution/ab_testing.py
python -m py_compile execution/fetch_insights.py
python -m pytest tests/test_ab_testing.py tests/test_fetch_insights.py -v
```

---

## Cost Impact

| Component | Cost per test |
|-----------|--------------|
| Blueprint clone + LLM content | ~$0.01 |
| Render visuals | $0.00 (FFmpeg) |
| AI background (if bg test) | $0.04 |
| Platform API publish | $0.00 |
| Insights fetch | $0.00 |
| **Total (non-bg test)** | **~$0.01** |
| **Total (bg test)** | **~$0.05** |
| **Monthly (1 test/day)** | **~$0.30-$1.50** |
