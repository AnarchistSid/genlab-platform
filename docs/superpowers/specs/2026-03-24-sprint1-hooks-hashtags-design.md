# Sprint 1: Hook Quality + Hashtag Optimization + Cross-Platform Adaptation

**Goal:** Dramatically improve hook quality for zero-follower channels using few-shot learning from BB's top performers, replace static hashtags with trending-topic-aware dynamic hashtags, and adapt hooks per platform.

**Date:** 2026-03-24

---

## 1. Hook Quality Upgrade

### Current State
- Claude Haiku with 1 good example and 1 bad example per niche
- No few-shot examples from actual top performers
- Temperature 0.9 (too creative, inconsistent quality)
- Same hook used across all platforms

### Upgrade

**A. Few-shot prompt engineering:**
Add the top 5 real hooks that got the most engagement per niche as few-shot examples in the system prompt. For BB we have real data; for new channels, seed with BB-style hooks adapted to each niche.

```
TOP PERFORMING HOOKS (use these as inspiration for style and structure):
1. "AI video generation just hit a wall we didn't know existed" (611K reach)
2. "AI just made a full cinematic film nobody expected" (11.6K reach)
3. "This gym session went catastrophically wrong" (2.7K reach)

Write a hook that matches this energy and specificity.
```

**B. Lower temperature to 0.7** — more consistent quality while still creative.

**C. Add hook scoring** — generate 3 hook candidates, score each with the hook features model, pick the best one. This uses the existing `hook_features.py` + `hook_classifier.py` infrastructure.

**D. Platform-specific hook variants:**
- Instagram: emotional, emoji-friendly, 40-50 chars
- YouTube: question format, curiosity gap, ≤40 chars (title)
- X/Twitter: hot take, conversational, provocative
- Facebook: engagement bait (ask a question), shareable

## 2. Dynamic Hashtag Strategy

### Current State
- Static hashtags hardcoded in `video_content_writer.py`: `["#Gaming", "#Gamer", "#GamingClips", "#VideoGames"]`
- Same hashtags on every post regardless of topic
- No trending hashtag integration

### Upgrade

**A. Topic-aware hashtags:**
Extract 2-3 topic hashtags from the story content (e.g., story about Minecraft → `#Minecraft #MinecraftDungeons`).

**B. Trending hashtags from Google Trends:**
The `GoogleTrendsIntel` class already exists. Query trending topics and include 1-2 trending hashtags that are relevant to the niche.

**C. Niche base + topic + trending formula:**
```
Instagram: 2 niche base + 2 topic-specific + 1 trending = 5 hashtags
YouTube: tags in description (10-15 keywords, no # prefix)
Twitter: 1-2 hashtags max (less is more on X)
Threads: 2-3 hashtags
```

## 3. File Changes

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/writing/llm_hook_generator.py` | Add few-shot examples, lower temp, generate 3 candidates + score, add platform-specific variant generation |
| `genlab-core/src/genlab_core/writing/video_content_writer.py` | Replace static hashtag arrays with dynamic topic+trending function |
| `genlab-core/src/genlab_core/strategies/base_writing.py` | Wire dynamic hashtags into caption generation |
| `genlab-core/src/genlab_core/strategies/base_platform_adaptation.py` | Generate platform-specific hooks instead of reusing same hook |

## 4. Quality Gates

- Hook generator produces 3 candidates and picks the highest-scored one
- Few-shot examples include real top-performer data
- Temperature reduced to 0.7
- Hashtags include at least 1 topic-specific tag per post
- YouTube titles are question format (verified by hook validator)
- Each platform gets a tailored hook variant
- `uv run --package genlab-core pytest genlab-core/tests/` passes
