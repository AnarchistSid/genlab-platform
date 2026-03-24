# Shared Content Ingestion + Intelligent Routing

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-niche content fetching with a shared ingestion layer that fetches from ALL sources once, classifies each story against all 5 niche profiles simultaneously, and routes content to the correct niche(s) — enabling cross-niche discovery, 60% YouTube API quota savings, and config-driven niche creation for SaaS.

**Date:** 2026-03-24

---

## 1. Problem Statement

### 1.1 Current Architecture (Per-Niche Fetching)

Each of the 5 channels runs its own pipeline independently:
- **BB** (08:00 IST): fetches from 38 sources (3 YT channels, 18 Reddit, 22 RSS)
- **CR** (09:30 IST): fetches from 12 sources (5 YT channels, 5 RSS)
- **CW** (15:30 IST): fetches from 10 sources (5 YT channels, 10 RSS)
- **SR** (13:30 IST): fetches from 18 sources (4 YT channels, 11 RSS)
- **FD** (11:30 IST): fetches from 13 sources (4 YT channels, 11 RSS)

Total: 91 source URLs, **zero overlap** — each channel has entirely unique sources.

Each pipeline also calls YouTube's trending API for its category (gaming:20, sports:17, movies:1, tech:28) and optionally runs YouTube search.list (100 units/call).

### 1.2 Problems

1. **Niche leakage**: AI subreddits (r/aivideo, r/AiVideos) post gaming/sports/entertainment content that's AI-generated. This content passes BB's relevance filter (it's from an "AI" source) but belongs in CriticalRush or ClutchWire. We just added 25 negative keywords as a band-aid, but the real fix is intelligent routing.

2. **No cross-niche discovery**: A "Minecraft AI mod" video on YouTube trending under category 20 (Gaming) would only be seen by CriticalRush. But it's equally relevant to BlackboxBrief (AI angle). With per-niche fetching, BB never sees it.

3. **Reddit is BB-exclusive**: Only BB fetches from Reddit (18 feeds). The other 4 channels have zero Reddit sources. If a viral anime clip is posted to r/AiVideos, only BB sees it — FrameDrift misses it entirely.

4. **YouTube API quota waste**: 5 niches × YouTube trending + search = ~5,000 units/day (50% of 10,000 daily quota). A shared pass would use ~2,000 units (20%).

5. **SaaS blocker**: Adding a new niche requires creating an entire sources.yaml, pipeline config, and strategy classes. With shared ingestion, a new niche only needs a keyword profile — the router handles discovery automatically.

### 1.3 What's NOT Shared

Each niche has **domain-specific API fetchers** that are niche-locked:
- **CriticalRush**: `FetchTwitchClips` (Twitch API for gaming streams)
- **ClutchWire**: `FetchScoreBatHighlights` (ScoreBat API for football)
- **SpliceReel**: `FetchTMDBTrailers` (TMDB API for movie trailers)
- **FrameDrift**: `FetchAnimePromos` (Jikan + AniList for anime)

These CANNOT be shared — they return content that's inherently single-niche. They stay as per-niche exclusive fetchers.

## 2. Architecture

### 2.1 Three-Layer Design

```
Layer 1: SHARED INGESTION (05:00 UTC daily, runs once)
├── Load shared_sources.yaml (merged + deduplicated source list)
├── Fetch YouTube trending for ALL categories (gaming, sports, movies, tech)
├── Fetch ALL YouTube channel RSS feeds (21 unique channels)
├── Fetch ALL Reddit RSS feeds (18 BB feeds, shared for routing)
├── Fetch ALL RSS/news feeds (~50 unique feeds)
├── Fetch Google Trends for all niche seed keywords
├── Optional: YouTube search.list with cross-niche keywords
├── Dedup by URL hash
└── Write raw stories to content_pool table

Layer 2: INTELLIGENT ROUTING (immediately after Layer 1)
├── Load niche profiles (positive_keywords, negative_keywords, threshold)
├── Score each story against ALL 5 niche profiles simultaneously
├── Multi-label routing: story can go to 1+ niches
├── Source affinity boost (source tagged for specific niche gets +0.2)
├── Conflict resolution for ambiguous keywords (minecraft, roblox etc.)
├── Dedup against content_memory table (cross-run URL dedup)
└── Update content_pool with niche_scores + routed_niches

Layer 3: PER-NICHE PIPELINES (existing staggered schedule)
├── Read pre-routed stories from content_pool
├── Also run niche-specific fetchers (Twitch, TMDB, AniList, ScoreBat)
├── Also fetch from exclusive sources (if any)
├── Merge: pool stories + niche-specific + exclusive → combined list
├── Continue existing pipeline: score → download → write → render → publish
└── Mark claimed stories in content_pool (prevents double-use)
```

### 2.2 Hybrid Model: Shared + Exclusive

Sources are classified as either **shared** (go through the router) or **exclusive** (bypass the router, go directly to one niche):

| Source Type | Shared? | Routing | Example |
|-------------|---------|---------|---------|
| YouTube trending charts | YES | Router classifies by content | mostPopular for all categories |
| YouTube channel RSS | YES | Router classifies + source affinity | Two Minute Papers → ai_creators affinity |
| Reddit feeds | YES | Router classifies + source affinity | r/aivideo → ai_creators affinity, but gaming content routes to gaming |
| RSS/news feeds | YES | Router classifies | TechCrunch, IGN, etc. |
| YouTube search.list | YES | Router classifies by keywords | Shared keyword set |
| Google Trends | YES | Enrichment for routing | All niches' seed keywords |
| Twitch clips | NO (exclusive) | Direct to gaming | CriticalRush only |
| TMDB trailers | NO (exclusive) | Direct to movies | SpliceReel only |
| AniList/Jikan promos | NO (exclusive) | Direct to anime | FrameDrift only |
| ScoreBat highlights | NO (exclusive) | Direct to sports | ClutchWire only |

A niche can also mark specific sources in its own sources.yaml as `exclusive: true` to skip the router (e.g., BB might want certain AI-only Reddit subs to never route to other niches).

## 3. Components

### 3.1 SharedSourceRegistry

**File:** `genlab-core/config/shared_sources.yaml`

Unified source config. Generated by merging all 5 per-niche sources.yaml files with dedup + affinity tags:

```yaml
# Shared sources — fetched once by SharedIngestionPipeline
# Each source has affinity tags indicating which niches it's most relevant to.
# The router uses these as a prior but can override based on content analysis.

youtube_categories:
  - id: "20"    # Gaming
    affinity: [gaming]
  - id: "17"    # Sports
    affinity: [sports]
  - id: "1"     # Film & Animation
    affinity: [movies]
  - id: "24"    # Entertainment
    affinity: [movies, gaming]
  - id: "28"    # Science & Technology
    affinity: [ai_creators]

youtube_channels:
  # BB channels
  - url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCbfYPyITQ-7l4upoX8nvctg"
    name: "Two Minute Papers"
    affinity: [ai_creators]
  - url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCsBjURrPoezykLs9EqgamOA"
    name: "Fireship"
    affinity: [ai_creators]
  # CR channels
  - url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCKy1dAqELo0zrOtPkf0eTMw"
    name: "IGN"
    affinity: [gaming, movies]  # Multi-niche
  # ... all 21 unique YouTube channels with affinity tags

reddit_feeds:
  # Currently BB-exclusive, but shared ingestion opens them to routing
  - url: "https://www.reddit.com/r/aivideo/new/.rss"
    name: "r/aivideo"
    affinity: [ai_creators]
  - url: "https://www.reddit.com/r/aivideo/top/.rss?t=week"
    name: "r/aivideo (top)"
    affinity: [ai_creators]
  # ... all 18 Reddit feeds

rss_feeds:
  # All unique RSS/news feeds from all 5 channels
  # ... ~50 unique feeds with affinity tags

google_trends:
  niche_seeds:
    ai_creators: ["AI", "ChatGPT", "artificial intelligence", "machine learning"]
    gaming: ["gaming", "video games", "esports", "game release"]
    sports: ["sports", "NBA", "cricket", "football highlights"]
    movies: ["movie", "trailer", "Netflix", "box office"]
    anime: ["anime", "manga", "crunchyroll", "new anime"]
```

### 3.2 NicheClassifier

**File:** `genlab-core/src/genlab_core/intelligence/niche_classifier.py`

Multi-label classifier that scores stories against all niche profiles simultaneously.

**Scoring algorithm:**

For each niche, the classifier computes a score using:

1. **Keyword relevance** (0.0-0.6): Existing `RelevanceFilter.score()` logic — positive keyword overlap with negative keyword hard-reject. But with a modification: since all niches have large keyword lists (73-169 keywords) and the denominator is capped at 3, even 1 hit gives ~33%. For multi-niche routing, we need to **compare relative scores across niches**, not just threshold each independently.

2. **Source affinity** (0.0-0.2): If the source is tagged with this niche's affinity, add 0.2 boost. This is the strongest prior — a video from "Two Minute Papers" is almost certainly ai_creators content.

3. **Category match** (0.0-0.15): If the YouTube category matches this niche's category ID, add 0.15. This only applies to YouTube videos with category metadata.

4. **Trending keyword bonus** (0.0-0.05): If the story title contains a Google Trends keyword that's trending in this niche's seed set, add 0.05.

**Total score** = keyword + affinity + category + trending (capped at 1.0)

**Routing rules:**
- Story routes to every niche where score >= niche's `relevance_threshold`
- If a story scores >= threshold for ZERO niches, it's marked `status='unrouted'` (logged for monitoring)
- If a story scores >= threshold for 3+ niches, it routes to the **top 2** by score (prevents flood)
- Ambiguous keyword handling: when a keyword like "minecraft" matches 4 niches, the **source affinity** breaks the tie (a YouTube gaming channel posting Minecraft → gaming wins)

**Handling ambiguous keywords** (minecraft, roblox, fortnite appear in 4-5 niches):

The current keyword lists have these in multiple niches as NEGATIVE keywords (e.g., BB's negative list now includes "minecraft"). The classifier respects negative keywords as hard-reject per niche. So:
- "Minecraft AI mod" from r/aivideo: ai_creators positive (AI keyword) + gaming positive (minecraft keyword) → routes to both
- "Minecraft gameplay" from YouTube gaming: gaming positive (minecraft + gameplay) + ai_creators REJECTED (minecraft is negative for BB now) → routes to gaming only

### 3.3 ContentPool Table

```sql
CREATE TABLE content_pool (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Dedup key
    content_hash TEXT NOT NULL,

    -- Story data (from fetcher)
    title TEXT,
    summary TEXT,
    source_url TEXT,
    source_name TEXT,
    source_platform TEXT,            -- youtube, reddit, rss
    video_url TEXT,
    video_id TEXT,                   -- YouTube video ID
    thumbnail_url TEXT,
    published_at TIMESTAMPTZ,
    duration_seconds INT,
    view_count BIGINT,
    view_velocity FLOAT,

    -- Routing metadata
    source_affinity TEXT[],          -- from shared_sources.yaml affinity tags
    youtube_category_id TEXT,        -- if from YouTube
    niche_scores JSONB NOT NULL DEFAULT '{}',
    routed_niches TEXT[] NOT NULL DEFAULT '{}',
    routing_reason TEXT,             -- human-readable routing explanation

    -- Lifecycle
    status TEXT NOT NULL DEFAULT 'available',
    claimed_by TEXT,                 -- niche_id that claimed it
    claimed_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '48 hours',

    -- Metadata
    extra JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_content_hash UNIQUE (content_hash)
);

-- Indexes for efficient niche pipeline reads
CREATE INDEX idx_cp_routed_status ON content_pool USING GIN(routed_niches) WHERE status = 'available';
CREATE INDEX idx_cp_status ON content_pool(status);
CREATE INDEX idx_cp_expires ON content_pool(expires_at) WHERE status = 'available';
CREATE INDEX idx_cp_niche_scores ON content_pool USING GIN(niche_scores);
```

### 3.4 SharedIngestionPipeline

**File:** `genlab-core/src/genlab_core/pipeline/shared_ingestion.py`

Standalone pipeline that runs once before all niche pipelines.

**Execution flow:**

```python
class SharedIngestionPipeline:
    def run(self) -> dict:
        # 1. Load shared sources config
        sources = load_shared_sources()

        # 2. YouTube trending charts (1 unit per category × 5 = 5 units)
        yt_trending = []
        for cat in sources["youtube_categories"]:
            videos = fetch_most_popular(cat["id"])
            for v in videos:
                v["source_affinity"] = cat["affinity"]
                v["youtube_category_id"] = cat["id"]
            yt_trending.extend(videos)

        # 3. YouTube channel RSS (0 API units — RSS is free)
        yt_channels = []
        for ch in sources["youtube_channels"]:
            videos = fetch_channel_rss(ch["url"])
            for v in videos:
                v["source_affinity"] = ch["affinity"]
                v["source_name"] = ch["name"]
            yt_channels.extend(videos)

        # 4. Reddit RSS feeds (0 API units)
        reddit = []
        for feed in sources["reddit_feeds"]:
            stories = fetch_rss(feed["url"])
            for s in stories:
                s["source_affinity"] = feed["affinity"]
                s["source_name"] = feed["name"]
                s["source_platform"] = "reddit"
            reddit.extend(stories)

        # 5. RSS/news feeds (0 API units)
        rss = []
        for feed in sources["rss_feeds"]:
            stories = fetch_rss(feed["url"])
            for s in stories:
                s["source_affinity"] = feed.get("affinity", [])
                s["source_name"] = feed["name"]
                s["source_platform"] = "rss"
            rss.extend(stories)

        # 6. Google Trends enrichment
        trends = fetch_all_trends(sources["google_trends"])

        # 7. Merge + dedup by URL hash
        all_stories = deduplicate(yt_trending + yt_channels + reddit + rss)

        # 8. Classify + route
        classifier = NicheClassifier()
        for story in all_stories:
            scores = classifier.classify(
                story["title"],
                story.get("summary", ""),
                source_affinity=story.get("source_affinity", []),
                youtube_category=story.get("youtube_category_id"),
            )
            story["niche_scores"] = scores
            story["routed_niches"] = classifier.route(scores)

        # 9. Write to content_pool (upsert by content_hash)
        written = write_to_content_pool(all_stories)

        # 10. Expire old entries
        expire_old_entries(hours=48)

        # 11. Log routing report
        routed = [s for s in all_stories if s["routed_niches"]]
        unrouted = [s for s in all_stories if not s["routed_niches"]]

        return {
            "total_fetched": len(all_stories),
            "total_routed": len(routed),
            "total_unrouted": len(unrouted),
            "by_niche": {nid: sum(1 for s in routed if nid in s["routed_niches"]) for nid in NICHE_IDS},
            "multi_niche": sum(1 for s in routed if len(s["routed_niches"]) > 1),
        }
```

### 3.5 Modified FetchTrendingVideos

**File:** `genlab-core/src/genlab_core/media/trending_video_fetcher.py`

The existing `FetchTrendingVideos` stage is modified to read from the content pool:

```python
def execute(self, context):
    niche_id = context["niche_id"]
    stories = context.get("stories", [])

    # Source 1: Pre-routed stories from shared content pool
    pool_stories = read_from_content_pool(niche_id)
    stories.extend(pool_stories)

    # Source 2: Niche-specific API fetchers (already in pipeline as separate stages)
    # These run as their own stages: FetchTwitchClips, FetchTMDBTrailers, etc.
    # They add to context["stories"] independently.

    # Source 3: Exclusive sources from this niche's sources.yaml
    exclusive_sources = load_exclusive_sources(niche_id)
    if exclusive_sources:
        exclusive_stories = fetch_from_sources(exclusive_sources)
        stories.extend(exclusive_stories)

    # Source 4: Fallback — if content pool is empty (shared ingestion didn't run),
    # fall back to the original per-niche fetching behavior
    if not pool_stories:
        logger.warning("Content pool empty for %s — falling back to per-niche fetch", niche_id)
        stories.extend(self._legacy_fetch(niche_id, context))

    context["stories"] = stories
    return context
```

### 3.6 LaunchAgent Schedule

**New plist:** `com.genlab.shared-ingestion`
- Runs at **05:00 UTC** (10:30 IST) — 2.5 hours before the first niche pipeline (BB at 08:00 IST)
- Timeout: 600s (10 min) — sufficient for ~100 source fetches
- Exit code handling: if it fails, niche pipelines fall back to per-niche fetching

**Existing niche plists:** unchanged. They continue to run at their existing times. The only difference is that `FetchTrendingVideos` now reads from the content pool instead of fetching independently.

## 4. Niche Profiles

Niche profiles are loaded from each channel's existing `sources.yaml` — no new config file needed. The classifier reads `positive_keywords`, `negative_keywords`, and `relevance_threshold` from each niche's config.

For SaaS, a new niche only needs:
1. A `sources.yaml` with `positive_keywords` and `negative_keywords`
2. A `niche.yaml` with `niche_id` and pipeline stages
3. The shared ingestion router starts routing content to it automatically

## 5. Quota Impact

| API Call | Current | Shared | Savings |
|----------|---------|--------|---------|
| YouTube mostPopular (1 unit each) | 5 calls | 5 calls (same categories) | 0% |
| YouTube channel RSS (free) | 21 calls across 5 niches | 21 calls once | 0% (no overlap, but only called once) |
| Reddit RSS (free) | 18 calls (BB only) | 18 calls once (now shared) | 0% (same calls, but content shared) |
| YouTube search.list (100 units each) | ~10 calls × 5 niches = 50 calls = 5,000 units | ~15 calls shared = 1,500 units | **70%** |
| **Total YouTube API units** | **~5,050** | **~1,555** | **69%** |

The main saving is in `search.list` — instead of each niche searching for its own keywords, a shared pass searches with cross-niche keywords and the router assigns results.

## 6. Cross-Niche Discovery

| Story | Source | Current | After (Shared Router) |
|-------|--------|---------|----------------------|
| "AI-generated Last of Us parody" | r/aivideo | BB only (wrong — gaming content) | BB (0.6) + Gaming (0.7) → Gaming claims |
| "Minecraft AI mod generates worlds" | YT trending cat:20 | CR only | CR (0.8) + BB (0.5) → both get it |
| "NBA player uses AI training" | r/sports | CW only (if CW had Reddit) | CW (0.9) + BB (0.4) → both |
| "Dragon Ball Z AI upscale 4K" | r/AiVideos | BB only (wrong — anime) | FD (0.7) + BB (0.5) → both |
| "New Jujutsu Kaisen trailer" | YT trending cat:1 | SR only | FD (0.9) + SR (0.3) → FD claims |
| "GTA 6 official trailer leaked" | YouTube search | CR only | CR (0.9) → CR only (correctly) |

## 7. File Changes

### 7.1 New Files

| File | Purpose |
|------|---------|
| `genlab-core/config/shared_sources.yaml` | Unified source registry with affinity tags |
| `genlab-core/src/genlab_core/intelligence/niche_classifier.py` | Multi-label niche classifier |
| `genlab-core/src/genlab_core/pipeline/shared_ingestion.py` | Shared ingestion pipeline |
| `genlab-core/migrations/create_content_pool.sql` | Content pool table DDL |
| `genlab-core/runbooks/com.genlab.shared-ingestion.plist` | LaunchAgent for shared ingestion |

### 7.2 Modified Files

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/media/trending_video_fetcher.py` | Add content pool read mode + fallback |
| `genlab-core/src/genlab_core/pipeline/cli.py` | Add `shared-ingestion` as a runnable pipeline mode |

### 7.3 Unchanged

All niche-specific strategy files, pipeline stages, fetchers, and configs remain unchanged. The router is purely additive.

## 8. Error Handling

| Failure | Impact | Recovery |
|---------|--------|----------|
| Shared ingestion crashes | No content in pool | Niche pipelines fall back to per-niche `FetchTrendingVideos` (existing behavior) |
| Single source fetch fails | Missing stories from that source | Other sources still provide content; circuit breaker prevents cascade |
| Classifier misroutes a story | Wrong niche gets the story | Human review catches it; feedback updates keyword lists |
| Content pool DB down | Pool reads return empty | Same fallback as crash — per-niche fetching |
| YouTube API quota exhausted during shared ingestion | Partial results | Niche-specific fetchers still run independently with their own quota tracking |

## 9. Migration Path

| Phase | What | Risk | Rollback |
|-------|------|------|----------|
| 1 | Create content_pool table + NicheClassifier | Zero — additive only | Drop table |
| 2 | Create shared_sources.yaml by merging existing configs | Zero — new file only | Delete file |
| 3 | Create SharedIngestionPipeline + LaunchAgent | Low — runs independently | Disable LaunchAgent |
| 4 | Modify FetchTrendingVideos to read from pool (with fallback) | Low — fallback preserves existing behavior | Revert to HEAD~1 |
| 5 | Remove redundant per-niche fetching for shared sources | Medium — removes old path | Re-add old code (but Phase 4 fallback covers this) |

## 10. Quality Gates

- Shared ingestion completes in < 10 minutes
- At least 50% of fetched stories route to 1+ niches
- Zero stories route to 4+ niches (indicates keyword over-matching)
- Niche pipelines see >= as many stories as before (shared + exclusive >= old per-niche)
- YouTube API quota usage decreases by >= 50%
- All existing tests pass
- Content pool entries expire after 48 hours (no unbounded growth)

## 11. Monitoring

New dashboard card on Mission Control: "Content Router"
- Stories fetched today: N
- Routed to niches: {ai_creators: X, gaming: Y, ...}
- Multi-niche stories: N (stories that went to 2+ niches)
- Unrouted: N (stories matching no niche — investigate keyword gaps)
- Pool size: N available / N claimed / N expired
