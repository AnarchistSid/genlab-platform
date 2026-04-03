# Unified Video Pipeline — Design Document

> **Date:** 2026-03-14
> **Status:** Approved
> **Scope:** All 5 channels (BB, CriticalRush, FrameDrift, SpliceReel, ClutchWire)

## Problem Statement

Three stacked failures prevent publishing across all channels:

1. **BB produces zero blueprints** — clip/story ID mismatch (videos downloaded early for broad pool, only 7/88 match current trend_pack), and those 7 stories have empty tags so template matching fails.
2. **Non-gaming channels have no video** — GenericPipelineRunner has no video download/clip/render stages. VisualRenderStrategy only generates Pexels search query strings. Blueprints stay DRAFTED forever.
3. **CriticalRush credential bypass** — `publish_gaming_content.py` reads credentials from `settings` directly, bypassing `niche_credentials.py`. Gaming content published to BB's Facebook page.

## Solution: Unified Video Pipeline

All 5 channels follow the same architecture: **download real source videos after ranking, only for top-N stories**.

### Pipeline Order (All Channels)

```
Fetch sources → Parse → Extract images (URL detection only) → Rank → Top-N cutoff
    → Download videos (source URL or search fallback) → Compose blueprints → Publish
```

---

## Component Design

### 1. VideoSourcer Engine (`genlab_core/media/video_sourcer.py`)

Central video sourcing engine with pluggable search backends and ranked fallback chain.

**Fallback chain (per story):**

| Level | Backend | Trigger |
|-------|---------|---------|
| 1 | Direct source URL | Story URL is YouTube/Reddit video, or `video_url` extracted from article |
| 2 | YouTube Data API search | `search.list` by story title + niche keywords |
| 3 | Reddit search | Niche subreddits JSON API for video posts matching story |
| 4 | TMDB Trailers | Movies niche only — `videos` endpoint for official trailers |
| 5 | Skip | No video found → story excluded from blueprint composition |

**Search result scoring (4 dimensions):**

| Signal | Weight | Logic |
|--------|--------|-------|
| Relevance | 0.40 | Cosine similarity (TF-IDF) between story title and video title |
| Freshness | 0.25 | Exponential decay, 48h half-life from story publish date |
| Quality | 0.20 | View count (log-scaled), like ratio, channel authority |
| Duration fit | 0.15 | Gaussian centered on 60s, sigma=45s (prefers 15s-120s for reels) |

**Infrastructure:**
- Result caching: `DiskCache` with 12h TTL
- Quota management: YouTube API 10,000 units/day. Budget: 20 searches/niche/day = 100 total = 10,000 units. Tracked via `QuotaManager`.
- Global video dedup: SHA-256 of video URL prevents duplicate downloads across niches
- Download via existing `video_downloader.py` (yt-dlp)

**Output:** `clip_index.json` per run:
```json
{
  "story_id": "abc123",
  "clip_path": ".tmp/runs/RUN_ID/clips/abc123.mp4",
  "source_url": "https://youtube.com/watch?v=...",
  "source_backend": "youtube_search",
  "relevance_score": 0.87,
  "duration_seconds": 74,
  "success": true
}
```

### 2. DownloadTopVideos Stage (`genlab_core/media/download_top_videos.py`)

Shared pipeline stage that plugs into both BB's `daily_intel.sh` and GenericPipelineRunner.

**Logic:**
1. Apply top-N cutoff (`blueprint_limits.max_stories` from config)
2. For each story, resolve video source via VideoSourcer fallback chain
3. Download via `video_downloader.py` (best mp4 <=1080p, max 900s)
4. Post-download validation: ffprobe check (codec, resolution, duration, file size > 100KB)
5. Re-encode if needed (wrong codec/container)
6. Write `clip_index.json`

**Concurrency:** Up to 3 parallel downloads via `asyncio.Semaphore`.

**Error handling:** Download timeout 60s → retry once → mark `success: false`. Age-restricted/private → skip to next fallback level. All levels exhausted → story excluded.

### 3. BB Pipeline Restructure

**Change 1 — Split `extract_media.py`:**
- Keeps: image extraction, video URL detection (writes `media_index.json` with `video_url` field)
- Removes: actual video file download (moved to `download_top_videos.py`)

**Change 2 — Tag inference timing in `compose_blueprints.py`:**
- Current (broken): load stories → video filter → infer tags → match templates
- Fixed: load stories → infer tags → match templates → video filter → compose

**Change 3 — `daily_intel.sh` step reorder:**
```
1. fetch_ai_creators.py
2. parse_extract.py
3. extract_media.py          # images + video URL detection only (no download)
4. dedupe_rank_items.py
5. build_trend_pack.py
6. download_top_videos.py    # NEW — downloads video for top-N ranked stories
7. compose_blueprints.py     # tag inference before video filter
...rest unchanged
```

### 4. Non-Gaming Pipeline Upgrade

**4a. YouTube sources per niche (added to `sources.yaml`):**

| Niche | YouTube Channels | Tier |
|-------|-----------------|------|
| Anime | Crunchyroll, AnimeMan, Gigguk, Mother's Basement, ANN (YT) | tier_1 + tier_2 |
| Movies | Studio channels, trailer aggregators (FilmSelect, ONE Media), review (Stuckmann) | tier_1 + tier_2 |
| Sports | Official league (NBA, NFL, Premier League), ESPN (YT), House of Highlights | tier_1 + tier_2 |

Format: YouTube RSS feeds (`youtube.com/feeds/videos.xml?channel_id=...`).

**4b. GenericPipelineRunner stage addition:**
```yaml
pipeline:
  - stage: ContentResearchStrategy
  - stage: ScoringStrategy
  - stage: DownloadTopVideos          # NEW
  - stage: WritingStrategy
  - stage: HooksStrategy
  - stage: VisualRenderStrategy       # UPGRADED
  - stage: PlatformAdaptationStrategy
  - stage: PushToBacklogStrategy      # UPGRADED
```

**4c. VisualRenderStrategy upgrade:**
- Check if story has downloaded video from DownloadTopVideos
- If yes: run through VideoCompositor (text overlays, branding, 1080x1920)
- If no: mark `render_status: "no_video"`, excluded from backlog push

**4d. PushToBacklogStrategy upgrade:**
- If rendered video exists → status = VISUAL_READY
- If no video → status = DRAFTED (excluded from publish queue)

### 5. CriticalRush Credential Fix

Refactor `publish_gaming_content.py` to use `niche_credentials.py` for all 5 platform methods:

```python
# Before (vulnerable):
access_token = settings.meta_access_token

# After (guarded):
from genlab_core.publishing.niche_credentials import resolve_meta_credentials
creds = resolve_meta_credentials("gaming")
access_token = creds.get("ig_access_token", "")
```

**Cross-channel assertion:**
```python
def _validate_niche_match(blueprint_niche: str, credential_niche: str):
    if blueprint_niche != credential_niche:
        raise CrossChannelPublishError(...)
```

Test coverage: integration test that attempts gaming blueprint with BB credentials → must raise error.

### 6. Observability

New `video_sourcing` section in `run_report.json`:
```json
{
  "video_sourcing": {
    "stories_needing_video": 10,
    "videos_found": 8,
    "videos_downloaded": 7,
    "videos_failed": 1,
    "by_backend": {
      "direct_url": 3,
      "youtube_search": 4,
      "reddit_search": 0,
      "tmdb_trailer": 1
    },
    "avg_relevance_score": 0.82,
    "youtube_api_quota_used": 400,
    "youtube_api_quota_remaining": 9600,
    "cache_hits": 2
  }
}
```

Alert conditions:
- Video sourcing success rate < 50% for 2 runs → warning
- YouTube API quota > 80% → reduce to top-5 only
- Average relevance score < 0.5 → search query tuning warning

---

## Files Changed

| # | Change | Scope | Files |
|---|--------|-------|-------|
| 1 | VideoSourcer engine | New (genlab-core) | `genlab_core/media/video_sourcer.py` + 3 backend files |
| 2 | DownloadTopVideos stage | New (genlab-core) | `genlab_core/media/download_top_videos.py` |
| 3 | Split extract_media.py | BB modification | `execution/extract_media.py` |
| 4 | Tag inference timing | BB modification | `execution/compose_blueprints.py` |
| 5 | Pipeline step reorder | BB modification | `runbooks/daily_intel.sh` |
| 6 | YouTube sources per niche | Config addition | 3x `config/sources.yaml` |
| 7 | Pipeline stage addition | Config addition | 3x `config/niche.yaml` |
| 8 | VisualRenderStrategy upgrade | Per-niche modification | 3x `visual_render.py` |
| 9 | PushToBacklog upgrade | Per-niche modification | 3x `push_to_backlog.py` |
| 10 | CR credential guard | CR modification | `publish_gaming_content.py` |
| 11 | Cross-channel assertion | New (genlab-core) | `niche_credentials.py` |
| 12 | Observability metrics | BB + genlab-core | `run_report`, `download_top_videos.py` |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| YouTube API quota exhaustion | Quota tracking + auto-reduce to top-5 at 80% |
| Low relevance search results | 4-dimension scoring + minimum threshold (0.3) |
| yt-dlp breakage on platform changes | Pin yt-dlp version, monitor download failures |
| CR credential fix breaks gaming publish | Integration test + staged rollout (test mode first) |
| extract_media.py split introduces regression | Existing BB tests + new unit tests for split behavior |
