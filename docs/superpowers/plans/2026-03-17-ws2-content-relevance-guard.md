# WS2: Content Relevance Guard — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop off-niche content from entering pipelines (MMA in anime, generic shorts in movies, etc.) by adding a post-fetch relevance filter with per-niche positive/negative keyword lists.

**Architecture:** New `RelevanceFilter` class in genlab-core, wired into `TrendingVideoFetcher` after candidate collection. Per-niche `content_filter` config in each `sources.yaml`. Rejected videos logged in run_report.

**Tech Stack:** Python, regex keyword matching, YAML config

**Spec:** `docs/superpowers/specs/2026-03-17-ws2-content-relevance-guard-design.md`

---

## Chunk 1: RelevanceFilter Implementation

### Task 1: Create RelevanceFilter class

**Files:**
- Create: `genlab-core/src/genlab_core/media/relevance_filter.py`
- Test: `genlab-core/tests/media/test_relevance_filter.py`

- [ ] **Step 1: Write failing tests**

```python
# genlab-core/tests/media/test_relevance_filter.py
"""Tests for the content relevance filter."""

from genlab_core.media.relevance_filter import RelevanceFilter


class TestRelevanceFilter:
    def _make_filter(self, **overrides):
        config = {
            "positive_keywords": ["anime", "manga", "crunchyroll", "shonen", "episode"],
            "negative_keywords": ["mma", "ufc", "boxing", "dana white", "nate diaz"],
            "relevance_threshold": 0.3,
        }
        config.update(overrides)
        return RelevanceFilter("anime", config)

    def test_rejects_mma_content(self):
        rf = self._make_filter()
        score = rf.score("Dana White's beef with Nick Diaz went DEEP", "UFC fight")
        assert score == 0.0

    def test_accepts_anime_content(self):
        rf = self._make_filter()
        score = rf.score("Subaru's about to break AGAIN", "Re:Zero anime episode")
        assert score >= 0.3

    def test_negative_keyword_hard_reject(self):
        rf = self._make_filter()
        score = rf.score("Epic anime-style UFC knockout", "MMA highlights")
        assert score == 0.0  # "ufc" in text triggers hard reject

    def test_filter_removes_irrelevant(self):
        rf = self._make_filter()
        candidates = [
            {"title": "One Piece episode 1200 reaction", "description": "anime"},
            {"title": "Nate Diaz on Living With His Brother", "description": "MMA"},
            {"title": "Re:Zero Season 3 trailer", "description": "crunchyroll anime"},
        ]
        kept = rf.filter(candidates)
        assert len(kept) == 2
        assert all("anime" in c["title"].lower() or "Re:Zero" in c["title"] for c in kept)

    def test_empty_config_passes_all(self):
        rf = RelevanceFilter("gaming", {})
        candidates = [{"title": "anything", "description": "whatever"}]
        kept = rf.filter(candidates)
        assert len(kept) == 1

    def test_relevance_score_attached(self):
        rf = self._make_filter()
        candidates = [{"title": "anime fight scene", "description": "shonen manga"}]
        kept = rf.filter(candidates)
        assert "relevance_score" in kept[0]
        assert kept[0]["relevance_score"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/media/test_relevance_filter.py -v --tb=short
```

Expected: ImportError (module doesn't exist)

- [ ] **Step 3: Implement RelevanceFilter**

```python
# genlab-core/src/genlab_core/media/relevance_filter.py
"""Post-fetch content relevance filter.

Scores video candidates against niche-specific keyword lists and rejects
off-niche content. Uses positive keyword overlap scoring with negative
keyword hard-reject.

Config lives in each niche's sources.yaml under content_filter:
    content_filter:
      relevance_threshold: 0.3
      positive_keywords: [anime, manga, ...]
      negative_keywords: [mma, ufc, ...]
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RelevanceFilter:
    def __init__(self, niche_id: str, config: dict[str, Any]) -> None:
        self.niche_id = niche_id
        self.positive_keywords = [k.lower() for k in config.get("positive_keywords", [])]
        self.negative_keywords = [k.lower() for k in config.get("negative_keywords", [])]
        self.threshold = config.get("relevance_threshold", 0.3)

    def score(self, title: str, description: str = "") -> float:
        """Score relevance 0.0-1.0. Returns 0.0 on negative keyword match."""
        text = f"{title} {description}".lower()

        for neg in self.negative_keywords:
            if neg in text:
                return 0.0

        if not self.positive_keywords:
            return 1.0

        hits = sum(1 for kw in self.positive_keywords if kw in text)
        denominator = max(len(self.positive_keywords) * 0.3, 1)
        return min(1.0, hits / denominator)

    def filter(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter candidates, attaching relevance_score to each. Returns kept list."""
        kept: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for v in candidates:
            s = self.score(v.get("title", ""), v.get("description", ""))
            v["relevance_score"] = s
            if s >= self.threshold:
                kept.append(v)
            else:
                rejected.append(v)

        if rejected:
            logger.info(
                "[RelevanceFilter:%s] Rejected %d/%d candidates (threshold=%.2f): %s",
                self.niche_id, len(rejected), len(candidates), self.threshold,
                [r.get("title", "?")[:50] for r in rejected[:5]],
            )

        return kept
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/media/test_relevance_filter.py -v --tb=short
```

Expected: 7 PASSED

- [ ] **Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/media/relevance_filter.py genlab-core/tests/media/test_relevance_filter.py
git commit -m "feat(quality): add RelevanceFilter for post-fetch niche content validation

Scores candidates via positive keyword overlap, hard-rejects on negative
keywords. Configurable threshold per niche via sources.yaml content_filter."
```

---

### Task 2: Wire into TrendingVideoFetcher + add YAML configs

**Files:**
- Modify: `genlab-core/src/genlab_core/media/trending_video_fetcher.py` (around line 326-350)
- Modify: `FrameDrift/config/sources.yaml`
- Modify: `ClutchWire/config/sources.yaml`
- Modify: `SpliceReel/config/sources.yaml`
- Modify: `CriticalRush/niches/gaming/config/sources.yaml`
- Modify: `Content Scraper/config/sources.yaml`

- [ ] **Step 1: Wire RelevanceFilter into TrendingVideoFetcher**

In `trending_video_fetcher.py`, after the line `for video in candidates.values():` scoring loop (around line 338), add:

```python
from genlab_core.media.relevance_filter import RelevanceFilter

# After scoring, before returning results — add around line 345
content_filter_config = (niche_config or {}).get("content_filter", {})
if content_filter_config:
    rf = RelevanceFilter(niche_id, content_filter_config)
    pre_count = len(results)
    results = rf.filter(results)
    context_stats = {"rejected": pre_count - len(results), "kept": len(results)}
    logger.info("[TrendingVideoFetcher] Relevance filter: %s", context_stats)
```

Pass `niche_config` through to `fetch_trending()` — it's available in the pipeline context and should be threaded through the stage that calls this fetcher.

- [ ] **Step 2: Add content_filter to FrameDrift/config/sources.yaml**

Append to end of file:

```yaml
content_filter:
  relevance_threshold: 0.35
  positive_keywords:
    - anime
    - manga
    - otaku
    - crunchyroll
    - funimation
    - shonen
    - shojo
    - isekai
    - waifu
    - dubbed
    - subbed
    - episode
    - season
    - opening
    - ending
    - op
    - ed
    - ova
    - light novel
    - weeb
  negative_keywords:
    - mma
    - ufc
    - boxing
    - wrestling
    - dana white
    - nate diaz
    - nick diaz
    - conor mcgregor
    - bellator
    - cage fight
    - knockout ko
    - wwe
    - aew
```

- [ ] **Step 3: Add content_filter to other 4 niches**

ClutchWire (sports):
```yaml
content_filter:
  relevance_threshold: 0.25
  positive_keywords:
    - goal
    - touchdown
    - home run
    - slam dunk
    - championship
    - playoff
    - highlights
    - match
    - game
    - score
    - season
    - nba
    - nfl
    - mlb
    - premier league
    - champions league
    - athlete
    - coach
    - stadium
  negative_keywords:
    - cooking
    - beauty
    - prank
    - life hack
    - motivational speech
    - cancer awareness
    - lottery
    - unboxing
```

SpliceReel (movies):
```yaml
content_filter:
  relevance_threshold: 0.25
  positive_keywords:
    - trailer
    - movie
    - film
    - cinema
    - director
    - actor
    - actress
    - box office
    - premiere
    - sequel
    - franchise
    - oscar
    - hollywood
    - netflix
    - disney
    - marvel
    - dc
    - horror
    - thriller
    - comedy
    - drama
    - scene
    - clip
  negative_keywords:
    - prank
    - life hack
    - motivational
    - compilation funny
    - try not to laugh
    - satisfying
    - asmr
```

CriticalRush (gaming):
```yaml
content_filter:
  relevance_threshold: 0.20
  positive_keywords:
    - game
    - gaming
    - gameplay
    - esports
    - streamer
    - speedrun
    - playstation
    - xbox
    - nintendo
    - steam
    - pc gaming
    - fps
    - mmorpg
    - battle royale
    - fortnite
    - valorant
    - league of legends
    - trailer
    - update
    - patch
    - dlc
  negative_keywords:
    - cooking
    - beauty tutorial
    - mukbang
    - asmr eating
```

Content Scraper (ai_creators):
```yaml
content_filter:
  relevance_threshold: 0.20
  positive_keywords:
    - ai
    - artificial intelligence
    - chatgpt
    - claude
    - gemini
    - openai
    - anthropic
    - machine learning
    - llm
    - gpt
    - deep learning
    - neural network
    - automation
    - robot
    - tech
  negative_keywords:
    - cooking
    - beauty
    - prank
    - lottery
```

- [ ] **Step 4: Run pipeline test for FrameDrift**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/ -x -q --tb=short 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/media/trending_video_fetcher.py \
  FrameDrift/config/sources.yaml ClutchWire/config/sources.yaml \
  SpliceReel/config/sources.yaml CriticalRush/niches/gaming/config/sources.yaml \
  "Content Scraper/config/sources.yaml"
git commit -m "feat(quality): wire RelevanceFilter into TrendingVideoFetcher + add niche configs

Each niche now has content_filter in sources.yaml with positive/negative
keyword lists. Anime hard-rejects MMA/UFC terms. All niches reject
generic non-topical viral content."
```
