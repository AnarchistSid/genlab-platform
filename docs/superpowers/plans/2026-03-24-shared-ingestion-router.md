# Shared Content Ingestion + Intelligent Router — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shared ingestion fetches ALL sources once, classifies stories against 5 niche profiles, routes to correct niches — enabling cross-niche discovery and 69% YouTube quota savings.

**Architecture:** New ContentPool table + NicheClassifier + SharedIngestionPipeline runs at 05:00 UTC before all niche pipelines. Niche pipelines read pre-routed stories from the pool + their own exclusive fetchers. Fallback to per-niche fetching if pool is empty.

**Tech Stack:** Python 3.14, PostgreSQL 17, psycopg3, feedparser, YAML, existing RelevanceFilter infrastructure

**Spec:** `docs/superpowers/specs/2026-03-24-shared-ingestion-router-design.md`

**Working directory:** `/Users/anarchistsid/GenLab`

---

## Task 1: Create content_pool database table

**Files:**
- Create: `genlab-core/migrations/create_content_pool.sql`

- [ ] **Step 1:** Create the migration SQL file with the content_pool table definition, indexes, and constraints per spec §3.3.

- [ ] **Step 2:** Run the migration:
```bash
psql -d genlab -f genlab-core/migrations/create_content_pool.sql
```

- [ ] **Step 3:** Verify:
```bash
psql -d genlab -c "\d content_pool"
psql -d genlab -c "SELECT COUNT(*) FROM content_pool"
```

- [ ] **Step 4:** Commit:
```bash
git add genlab-core/migrations/create_content_pool.sql && git commit -m "feat: create content_pool table for shared ingestion router"
```

---

## Task 2: Create NicheClassifier

Multi-label classifier that scores stories against all 5 niche profiles using existing keyword lists from each channel's sources.yaml.

**Files:**
- Create: `genlab-core/src/genlab_core/intelligence/niche_classifier.py`

- [ ] **Step 1:** Build the classifier per spec §3.2. It must:
  - Load positive_keywords, negative_keywords, relevance_threshold from each channel's sources.yaml
  - Use existing `RelevanceFilter.score()` logic for keyword matching
  - Add source affinity boost (+0.2), category match (+0.15), trending keyword bonus (+0.05)
  - `classify(title, description, source_affinity, youtube_category)` → `{"ai_creators": 0.9, "gaming": 0.3, ...}`
  - `route(scores, threshold)` → `["ai_creators"]` (niches above threshold, max 2)
  - Handle ambiguous keywords by respecting negative keyword hard-reject per niche

  Key: the classifier loads niche profiles by reading each channel's sources.yaml. The paths are:
  ```python
  NICHE_SOURCE_PATHS = {
      "ai_creators": "BlackboxBrief/config/sources.yaml",
      "gaming": "CriticalRush/niches/gaming/config/sources.yaml",
      "sports": "ClutchWire/config/sources.yaml",
      "movies": "SpliceReel/config/sources.yaml",
      "anime": "FrameDrift/config/sources.yaml",
  }
  ```

- [ ] **Step 2:** Test:
```bash
~/.local/bin/uv run --package genlab-core python -c "
from genlab_core.intelligence.niche_classifier import NicheClassifier
c = NicheClassifier()
# Test: AI content should route to ai_creators
scores = c.classify('Sora just generated a full Fox News broadcast', 'AI video generation tool')
print(f'AI story: {scores}')
print(f'Routes to: {c.route(scores)}')

# Test: Gaming content should route to gaming
scores = c.classify('Minecraft Dungeons 2 Revealed With Fall 2026 Launch', 'Mojang announces sequel')
print(f'Gaming story: {scores}')
print(f'Routes to: {c.route(scores)}')

# Test: Cross-niche content
scores = c.classify('Minecraft AI mod generates infinite worlds', 'Using neural networks to create procedural Minecraft worlds')
print(f'Cross-niche: {scores}')
print(f'Routes to: {c.route(scores)}')
"
```

- [ ] **Step 3:** Commit:
```bash
git add genlab-core/src/genlab_core/intelligence/niche_classifier.py && git commit -m "feat: NicheClassifier — multi-label niche scoring for content routing"
```

---

## Task 3: Create shared_sources.yaml

Merge all 91 source URLs from 5 channels into a single config with affinity tags.

**Files:**
- Create: `genlab-core/config/shared_sources.yaml`

- [ ] **Step 1:** Build the shared sources config by reading each channel's sources.yaml and merging:
  - YouTube channels: 21 unique across all channels, each tagged with source niche affinity
  - YouTube categories: 5 categories (gaming:20, sports:17, movies:1, entertainment:24, tech:28)
  - Reddit feeds: 18 from BB (tagged with ai_creators affinity)
  - RSS feeds: ~50 unique across all channels
  - Google Trends seed keywords per niche

  Write a script to generate this automatically:
  ```bash
  ~/.local/bin/uv run --package genlab-core python -c "
  # Script to merge sources — output to stdout, pipe to shared_sources.yaml
  import yaml
  from pathlib import Path

  root = Path('.')
  paths = {
      'ai_creators': root / 'BlackboxBrief/config/sources.yaml',
      'gaming': root / 'CriticalRush/niches/gaming/config/sources.yaml',
      'sports': root / 'ClutchWire/config/sources.yaml',
      'movies': root / 'SpliceReel/config/sources.yaml',
      'anime': root / 'FrameDrift/config/sources.yaml',
  }
  # ... merge logic
  " > genlab-core/config/shared_sources.yaml
  ```

  Or create it manually from the spec §3.1 template.

- [ ] **Step 2:** Verify the config is valid YAML:
```bash
~/.local/bin/uv run --package genlab-core python -c "
import yaml
from pathlib import Path
d = yaml.safe_load(Path('genlab-core/config/shared_sources.yaml').read_text())
print(f'YouTube categories: {len(d.get(\"youtube_categories\", []))}')
print(f'YouTube channels: {len(d.get(\"youtube_channels\", []))}')
print(f'Reddit feeds: {len(d.get(\"reddit_feeds\", []))}')
print(f'RSS feeds: {len(d.get(\"rss_feeds\", []))}')
"
```

- [ ] **Step 3:** Commit:
```bash
git add genlab-core/config/shared_sources.yaml && git commit -m "feat: shared_sources.yaml — unified source registry with niche affinity tags"
```

---

## Task 4: Create SharedIngestionPipeline

The main pipeline that fetches from all sources, classifies, and routes to the content pool.

**Files:**
- Create: `genlab-core/src/genlab_core/pipeline/shared_ingestion.py`

- [ ] **Step 1:** Build the pipeline per spec §3.4. Key components:
  - `load_shared_sources()` — reads shared_sources.yaml
  - `fetch_youtube_trending(categories)` — calls `TrendingVideoFetcher._fetch_most_popular()` for each category
  - `fetch_channel_rss(channels)` — fetches YouTube channel RSS feeds (0 API units)
  - `fetch_reddit_feeds(feeds)` — fetches Reddit RSS via feedparser
  - `fetch_rss_feeds(feeds)` — fetches general RSS/news feeds
  - `deduplicate(stories)` — dedup by URL hash using content_memory
  - `classify_and_route(stories)` — runs NicheClassifier on each story
  - `write_to_content_pool(stories)` — upserts to content_pool table
  - `expire_old_entries(hours=48)` — marks expired entries
  - `run()` — orchestrates all steps, returns routing report

  Reuse existing infrastructure:
  - `TrendingVideoFetcher` for YouTube API calls
  - `feedparser` for RSS/Reddit (already a dependency)
  - `NicheClassifier` from Task 2
  - `psycopg` for content_pool writes

- [ ] **Step 2:** Add CLI entry point to pipeline/cli.py:
```python
# Add to cli.py — allow running shared ingestion via:
# python -m genlab_core.pipeline --mode shared-ingestion
```

- [ ] **Step 3:** Test locally (dry run):
```bash
~/.local/bin/uv run --package genlab-core python -c "
from genlab_core.pipeline.shared_ingestion import SharedIngestionPipeline
pipeline = SharedIngestionPipeline()
report = pipeline.run()
print(f'Fetched: {report[\"total_fetched\"]}')
print(f'Routed: {report[\"total_routed\"]}')
print(f'By niche: {report[\"by_niche\"]}')
print(f'Multi-niche: {report[\"multi_niche\"]}')
"
```

- [ ] **Step 4:** Verify content_pool has entries:
```bash
psql -d genlab -c "SELECT COUNT(*), array_agg(DISTINCT unnest) FROM content_pool, unnest(routed_niches) LIMIT 1"
```

- [ ] **Step 5:** Commit:
```bash
git add genlab-core/src/genlab_core/pipeline/shared_ingestion.py genlab-core/src/genlab_core/pipeline/cli.py && git commit -m "feat: SharedIngestionPipeline — fetch all sources, classify, route to content pool"
```

---

## Task 5: Modify FetchTrendingVideos to read from content pool

Add content pool reading to the existing FetchTrendingVideos stage with fallback to per-niche fetching.

**Files:**
- Modify: `genlab-core/src/genlab_core/media/trending_video_fetcher.py`

- [ ] **Step 1:** Add a `_read_from_content_pool(niche_id)` method that:
  - Queries `content_pool WHERE niche_id = ANY(routed_niches) AND status = 'available'`
  - Converts pool records to the same dict format as existing story dicts
  - Marks records as `status = 'claimed', claimed_by = niche_id`
  - Returns list of story dicts

- [ ] **Step 2:** Modify `execute()` to try the content pool first:
```python
# At the start of execute():
pool_stories = self._read_from_content_pool(niche_id)
if pool_stories:
    logger.info("[FetchTrending] Read %d stories from content pool for %s", len(pool_stories), niche_id)
    existing_stories = context.get("stories", [])
    existing_stories.extend(pool_stories)
    context["stories"] = existing_stories
    # Still run the rest of the method for YouTube trending + channel RSS
    # (these may find additional content not in the pool)
```

  Keep ALL existing fetching logic as-is — the pool is additive, not a replacement (yet).

- [ ] **Step 3:** Test that existing niche pipeline still works:
```bash
~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/ -q --tb=short --ignore=genlab-core/tests/engagement/ -x -k "test_fetch or test_trending" 2>&1 | tail -5
```

- [ ] **Step 4:** Commit:
```bash
git add genlab-core/src/genlab_core/media/trending_video_fetcher.py && git commit -m "feat: FetchTrendingVideos reads from content pool + fallback to per-niche"
```

---

## Task 6: Create LaunchAgent + verify end-to-end

**Files:**
- Create: `genlab-core/runbooks/com.genlab.shared-ingestion.plist`

- [ ] **Step 1:** Create the LaunchAgent plist:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.genlab.shared-ingestion</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/anarchistsid/GenLab/scripts/launch_wrapper.sh</string>
        <string>uv</string>
        <string>run</string>
        <string>--package</string>
        <string>genlab-core</string>
        <string>python</string>
        <string>-m</string>
        <string>genlab_core.pipeline.shared_ingestion</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/anarchistsid/GenLab</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>10</integer>
        <key>Minute</key>
        <integer>30</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/anarchistsid/GenLab/.logs/shared_ingestion.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/anarchistsid/GenLab/.logs/shared_ingestion_error.log</string>
    <key>TimeOut</key>
    <integer>600</integer>
</dict>
</plist>
```
Note: Hour=10, Minute=30 in IST (launchd uses LOCAL TIME) = 05:00 UTC.

- [ ] **Step 2:** Install and load:
```bash
cp genlab-core/runbooks/com.genlab.shared-ingestion.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.genlab.shared-ingestion.plist
```

- [ ] **Step 3:** Run manually to verify end-to-end:
```bash
~/.local/bin/uv run --package genlab-core python -m genlab_core.pipeline.shared_ingestion
```

- [ ] **Step 4:** Verify content pool has data and niche pipelines can read it:
```bash
psql -d genlab -c "
SELECT
    unnest(routed_niches) as niche,
    COUNT(*) as stories,
    COUNT(*) FILTER (WHERE status = 'available') as available
FROM content_pool
GROUP BY niche
ORDER BY stories DESC
"
```

- [ ] **Step 5:** Commit:
```bash
git add genlab-core/runbooks/com.genlab.shared-ingestion.plist && git commit -m "feat: shared ingestion LaunchAgent — runs daily at 05:00 UTC"
```

---

## Task 7: Final verification + parent repo commit

- [ ] **Step 1:** Run the full pipeline for one niche to verify pool integration:
```bash
~/.local/bin/uv run --package genlab-core python -m genlab_core.pipeline --niche gaming --dry-run
```

- [ ] **Step 2:** Verify quality gates:
```bash
echo "=== Content pool stats ==="
psql -d genlab -c "SELECT status, COUNT(*) FROM content_pool GROUP BY status"
echo ""
echo "=== Routing distribution ==="
psql -d genlab -c "SELECT unnest(routed_niches) as niche, COUNT(*) FROM content_pool GROUP BY niche ORDER BY count DESC"
echo ""
echo "=== Multi-niche stories ==="
psql -d genlab -c "SELECT COUNT(*) FROM content_pool WHERE array_length(routed_niches, 1) > 1"
echo ""
echo "=== Unrouted ==="
psql -d genlab -c "SELECT COUNT(*) FROM content_pool WHERE array_length(routed_niches, 1) = 0 OR routed_niches IS NULL"
```

- [ ] **Step 3:** Final commit:
```bash
git add -A genlab-core/ && git commit -m "feat: shared content ingestion + intelligent routing — cross-niche discovery"
```
