# WS2: Content Relevance Guard

**Goal**: G2 Content Quality 58% → 75%
**Effort**: ~4h
**Dependencies**: None

## Problem

YouTube trending chart returns off-niche content:
- **Anime** (FrameDrift): 3/4 hooks were MMA/UFC ("Dana White's beef with Nick Diaz") — violates "Never ingest non-anime content into FrameDrift"
- **Movies** (SpliceReel): Generic viral shorts, not movie trailers
- **Sports** (ClutchWire): "Cancer Had Spread" — not sports highlights

**Root cause**: Anime has no YouTube category ID, so `YOUTUBE_CATEGORIES.get("anime")` returns `None`, skipping the category chart entirely. The keyword search uses terms like "anime fight scene" which overlap with MMA/UFC. Movies uses category 1 (Film & Animation) which is extremely broad. Sports category 17 is also broad.

## Changes

### 1. Post-fetch relevance scorer — `genlab-core/src/genlab_core/media/relevance_filter.py` (NEW)

Lightweight TF-IDF cosine similarity scorer. After fetching candidate videos, score each title + description against niche-specific keyword corpus.

```python
class RelevanceFilter:
    def __init__(self, niche_id: str, config: dict):
        self.positive_keywords = config.get("positive_keywords", [])
        self.negative_keywords = config.get("negative_keywords", [])
        self.threshold = config.get("relevance_threshold", 0.3)

    def score(self, title: str, description: str) -> float:
        """0.0 = irrelevant, 1.0 = perfect match."""
        text = f"{title} {description}".lower()

        # Hard reject on negative keywords
        for neg in self.negative_keywords:
            if neg.lower() in text:
                return 0.0

        # Positive keyword overlap scoring
        hits = sum(1 for kw in self.positive_keywords if kw.lower() in text)
        return min(1.0, hits / max(len(self.positive_keywords) * 0.3, 1))

    def filter(self, candidates: list[dict]) -> list[dict]:
        kept, rejected = [], []
        for v in candidates:
            score = self.score(v.get("title", ""), v.get("description", ""))
            v["relevance_score"] = score
            if score >= self.threshold:
                kept.append(v)
            else:
                rejected.append(v)
        if rejected:
            logger.info("[RelevanceFilter] Rejected %d/%d candidates below %.2f",
                        len(rejected), len(candidates), self.threshold)
        return kept
```

### 2. Negative keyword lists per niche — in each `config/sources.yaml`

```yaml
# FrameDrift/config/sources.yaml
content_filter:
  relevance_threshold: 0.35
  positive_keywords:
    - anime
    - manga
    - otaku
    - crunchyroll
    - funimation
    - shonen
    - isekai
    - waifu
    - dubbed
    - subbed
    - episode
    - season
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
```

Similar lists for sports (reject: cooking, beauty, prank, cancer awareness) and movies (reject: compilation, prank, life hack, motivational).

### 3. Wire RelevanceFilter into TrendingVideoFetcher

In `trending_video_fetcher.py`, after fetching candidates and before returning:

```python
from genlab_core.media.relevance_filter import RelevanceFilter

# After collecting all candidates
content_filter_config = niche_config.get("content_filter", {})
if content_filter_config:
    rf = RelevanceFilter(self.niche_id, content_filter_config)
    candidates = rf.filter(candidates)
```

### 4. AniList title validation for anime

For anime niche only, add a secondary check: query AniList GraphQL for the video title. If no anime title matches (fuzzy, threshold 0.6), mark as suspicious and reduce relevance score by 0.5.

This is a lightweight query (AniList is free, no auth needed) and catches edge cases where MMA content uses anime-adjacent terms.

### 5. Log rejected videos in run_report

Add `rejected_videos` list to run_report.json with title, score, and rejection reason for debugging.

## Files Modified

| File | Change |
|---|---|
| `genlab-core/src/genlab_core/media/relevance_filter.py` | NEW — RelevanceFilter class |
| `genlab-core/src/genlab_core/media/trending_video_fetcher.py` | Wire filter after fetch |
| `FrameDrift/config/sources.yaml` | Add content_filter section |
| `ClutchWire/config/sources.yaml` | Add content_filter section |
| `SpliceReel/config/sources.yaml` | Add content_filter section |
| `CriticalRush/niches/gaming/config/sources.yaml` | Add content_filter section |
| `Content Scraper/config/sources.yaml` | Add content_filter section |
| `genlab-core/tests/media/test_relevance_filter.py` | NEW — unit tests |

## Validation

- Run FrameDrift pipeline → zero MMA/UFC content in blueprints
- Run SpliceReel pipeline → only movie/trailer content
- `pytest genlab-core/tests/media/test_relevance_filter.py` passes
- Check run_report.json for `rejected_videos` entries

## Risks

- Over-filtering could reduce blueprint count to 0 for a run (mitigated by threshold tuning)
- AniList API could be down (fail-open — skip validation, log warning)
