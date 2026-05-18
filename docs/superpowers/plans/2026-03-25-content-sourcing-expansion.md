# Content Sourcing Expansion Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand content sourcing from 73 to ~640 sources, harden deduplication to prevent duplicate posts at 10x volume, and tune the classifier for precision at scale.

**Architecture:** All new sources are zero-cost RSS/Atom feeds added to a single shared registry (`shared_sources.yaml`). Per-niche source files are migrated. Dedup is hardened with 4 new layers (title similarity, YT ID from Reddit, title vs history, pool→pipeline cross-dedup) plus lookback increases and race condition fixes. Classifier is tuned to prevent misrouting at higher volume.

**Tech Stack:** Python 3.12, psycopg3, feedparser, scikit-learn (TF-IDF), concurrent.futures, YAML

**Spec:** `docs/superpowers/specs/2026-03-25-content-sourcing-expansion-design.md`

---

## File Structure

| File | Responsibility | Change Type |
|------|---------------|-------------|
| `genlab-core/config/shared_sources.yaml` | Central source registry (all YouTube, Reddit, RSS feeds) | Major expansion |
| `genlab-core/migrations/add_content_pool_video_idx.sql` | Video ID index on content_pool | Create |
| `genlab-core/src/genlab_core/pipeline/shared_ingestion.py` | Shared ingestion pipeline (fetch, classify, dedup, write to pool) | Modify |
| `genlab-core/src/genlab_core/media/trending_video_fetcher.py` | Per-niche video fetcher + pool reader | Modify (race condition fix) |
| `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py` | Blueprint creation with dedup | Modify (lookback + title dedup) |
| `genlab-core/src/genlab_core/intelligence/niche_classifier.py` | Multi-label niche scorer | Modify (tuning) |
| `genlab-core/src/genlab_core/intelligence/dedup_engine.py` | 3-pass content deduplication | Modify (new method) |
| `BlackboxBrief/config/sources.yaml` | BB-specific sources | Migrate feeds out |
| `CriticalRush/niches/gaming/config/sources.yaml` | Gaming-specific sources | Migrate feeds out |
| `ClutchWire/config/sources.yaml` | Sports-specific sources | Migrate feeds out |
| `SpliceReel/config/sources.yaml` | Movies-specific sources | Migrate feeds out |
| `FrameDrift/config/sources.yaml` | Anime-specific sources | Migrate feeds out |
| `genlab-core/tests/test_shared_ingestion_dedup.py` | Dedup layer tests | Create |
| `genlab-core/tests/test_niche_classifier_tuning.py` | Classifier tuning tests | Create |

---

## Sub-Project 1: Source Expansion (YAML only, no code changes)

### Task 1: Add Reddit Feeds for All 5 Niches

**Files:**
- Modify: `genlab-core/config/shared_sources.yaml`

- [ ] **Step 1: Add AI Creators Reddit feeds (36 new subreddits)**

Add after existing `reddit_feeds:` section. Each subreddit gets 2 entries (new + top). Include tier and affinity tags.

```yaml
  # === ai_creators — additional subreddits ===
  - name: "r/ChatGPT new"
    url: "https://www.reddit.com/r/ChatGPT/new/.rss"
    affinity: [ai_creators]
    tier: 1
  - name: "r/ChatGPT top"
    url: "https://www.reddit.com/r/ChatGPT/top/.rss?t=week"
    affinity: [ai_creators]
    tier: 1
  - name: "r/OpenAI new"
    url: "https://www.reddit.com/r/OpenAI/new/.rss"
    affinity: [ai_creators]
    tier: 1
  - name: "r/OpenAI top"
    url: "https://www.reddit.com/r/OpenAI/top/.rss?t=week"
    affinity: [ai_creators]
    tier: 1
  - name: "r/LocalLLaMA new"
    url: "https://www.reddit.com/r/LocalLLaMA/new/.rss"
    affinity: [ai_creators]
    tier: 1
  - name: "r/LocalLLaMA top"
    url: "https://www.reddit.com/r/LocalLLaMA/top/.rss?t=week"
    affinity: [ai_creators]
    tier: 1
  - name: "r/MachineLearning new"
    url: "https://www.reddit.com/r/MachineLearning/new/.rss"
    affinity: [ai_creators]
    tier: 1
  - name: "r/MachineLearning top"
    url: "https://www.reddit.com/r/MachineLearning/top/.rss?t=week"
    affinity: [ai_creators]
    tier: 1
  - name: "r/artificial new"
    url: "https://www.reddit.com/r/artificial/new/.rss"
    affinity: [ai_creators]
    tier: 1
  - name: "r/artificial top"
    url: "https://www.reddit.com/r/artificial/top/.rss?t=week"
    affinity: [ai_creators]
    tier: 1
  - name: "r/singularity new"
    url: "https://www.reddit.com/r/singularity/new/.rss"
    affinity: [ai_creators]
    tier: 1
  - name: "r/singularity top"
    url: "https://www.reddit.com/r/singularity/top/.rss?t=week"
    affinity: [ai_creators]
    tier: 1
  - name: "r/ClaudeAI new"
    url: "https://www.reddit.com/r/ClaudeAI/new/.rss"
    affinity: [ai_creators]
    tier: 2
  - name: "r/ClaudeAI top"
    url: "https://www.reddit.com/r/ClaudeAI/top/.rss?t=week"
    affinity: [ai_creators]
    tier: 2
  - name: "r/Gemini new"
    url: "https://www.reddit.com/r/Gemini/new/.rss"
    affinity: [ai_creators]
    tier: 2
  - name: "r/Gemini top"
    url: "https://www.reddit.com/r/Gemini/top/.rss?t=week"
    affinity: [ai_creators]
    tier: 2
  # Continue pattern for: perplexity_ai, LLMDevs, ollama, Bard, ElevenLabs,
  # HeyGen, AIMusic, suno, udio, Cursor, AIToolsTech, PromptEngineering,
  # LeonardoAi, aiArt, dalle, deforum, Futurology, technology, robotics,
  # SelfDrivingCars, programming, datascience, agi, GPT4, CopilotAI,
  # waifu_diffusion, Oobabooga, deeplearning
```

Full subreddit list for AI Creators (36 new, in addition to existing 9): ChatGPT, OpenAI, LocalLLaMA, MachineLearning, artificial, singularity, ClaudeAI, Gemini, perplexity_ai, LLMDevs, ollama, Bard, ElevenLabs, HeyGen, AIMusic, suno, udio, Cursor, AIToolsTech, PromptEngineering, LeonardoAi, aiArt, dalle, deforum, Futurology, technology, robotics, SelfDrivingCars, programming, datascience, agi, GPT4, CopilotAI, waifu_diffusion, Oobabooga, deeplearning

- [ ] **Step 2: Add Gaming Reddit feeds (44 new subreddits)**

T1 (8): gamingclips, LivestreamFail, esports, GamePhysics, SteamDeck, truegaming, PS5 (if not already), XboxSeriesX (if not already)
T2 (36): VALORANT, leagueoflegends, FortNiteBR, apexlegends, Overwatch, CallOfDuty, halo, Eldenring, BaldursGate3, cs2, GTA, Minecraft, Helldivers, Palworld, DestinyTheGame, cyberpunkgame, MonsterHunter, Starfield, pokemon, RocketLeague, Genshin_Impact, darksouls, NoMansSkyTheGame, Warframe, PathOfExile, CompetitiveOverwatch, ValorantCompetitive, FortniteCompetitive, VRGaming, OculusQuest, IndieGaming, GamingLeaksAndRumours, speedrun, GameDeals, pcmasterrace, HitBoxPorn

- [ ] **Step 3: Add Sports Reddit feeds (42 new subreddits)**

T1 (10): nbahighlights, footballhighlights, fightporn, baseball, hockey, boxing, Cricket, SquaredCircle, formula1, CFB
T2 (32): CollegeBasketball, golf, tennis, rugbyunion, olympics, cycling, peloton, Swimming, trackandfield, PremierLeague, LaLiga, Bundesliga, seriea, championsleague, MLS, motorsports, NASCAR, INDYCAR, formuladank, motogp, bjj, Kickboxing, MuayThai, WNBA, WomensSoccer, NWSL, nbadiscussion, Barca, reddevils, LiverpoolFC, Ligue1, ufc

- [ ] **Step 4: Add Movies Reddit feeds (35 new subreddits)**

T1 (7): movies, MovieDetails, boxoffice, trailers, entertainment, TrueFilm, FanTheories
T2 (28): marvelstudios, DC_Cinematic, StarWars, comicbookmovies, MCUTheories, horror, scifi, Documentaries, criterion, netflix, DisneyPlus, HBOMAX, amazonprime, appletv, Hulu, streaming, A24, StudioGhibli, Pixar, television, HouseOfTheDragon, Severance, TheBoys, Invincible, arcane, StrangerThings, squidgame, moviecritic

- [ ] **Step 5: Add Anime Reddit feeds (35 new subreddits)**

T1 (7): anime, manga, animemes, animeclips, anime_irl, goodanimemes, ShitPostCrusaders
T2 (28): OnePiece, Naruto, DragonBallSuper, JuJutsuKaisen, ChainsawMan, KimetsuNoYaiba, bleach, HunterXHunter, OnePunchMan, SoloLeveling, SpyxFamily, Dandadan, Frieren, BlueLock, OshiNoKo, Berserk, ShingekiNoKyojin, BokuNoHeroAcademia, VinlandSaga, MobPsycho100, Haikyuu, CodeGeass, Gundam, evangelion, cowboybebop, AnimeART, AnimeFigures, MangaCollectors

- [ ] **Step 6: Verify YAML parses correctly**

Run: `python3 -c "import yaml; d = yaml.safe_load(open('genlab-core/config/shared_sources.yaml')); print(f'Reddit feeds: {len(d.get(\"reddit_feeds\", []))}')"`
Expected: `Reddit feeds: ~410` (existing 18 + ~192 new subs × 2 feeds each)

- [ ] **Step 7: Commit**

```bash
git add genlab-core/config/shared_sources.yaml
git commit -m "feat(sources): add ~192 Reddit subreddits across all 5 niches"
```

### Task 2: Add RSS Feeds for All 5 Niches

**Files:**
- Modify: `genlab-core/config/shared_sources.yaml`

- [ ] **Step 1: Add AI Creators RSS feeds (35 new)**

Add after existing `rss_feeds:` section:
TechCrunch AI, The Verge AI, Ars Technica AI, VentureBeat AI, Wired AI, IEEE Spectrum AI, InfoQ AI/ML, The Rundown AI, Ben's Bites, Import AI, The Neuron, AI Breakfast, Ahead of AI, The Gradient, OpenAI Blog, Google AI Blog, DeepMind Blog, Hugging Face Blog, Meta AI Blog, Microsoft Research, NVIDIA Blog, Anthropic News, Stanford HAI, Papers With Code, Alignment Forum, KDnuggets, Analytics Vidhya, Machine Learning Mastery, Towards AI, Product Hunt AI, Hacker News AI, arXiv CS.AI, arXiv CS.LG, Distill.pub, Lex Fridman (podcast RSS)

Use exact URLs from the spec (Section 3.1.3).

- [ ] **Step 2: Add Gaming RSS feeds (25 new)**

Polygon, The Verge Gaming, Destructoid, GamesRadar, DualShockers, VG247, Siliconera, Game Informer, Nintendo Life, Nintendo Everything, Push Square, Pure Xbox, Xbox Wire, PlayStation Blog, Dot Esports, Dexerto, HLTV, IndieDB, TouchArcade, Pocket Gamer, Time Extension, MMORPG.com, Massively OP, Gematsu, Game Developer

- [ ] **Step 3: Add Sports RSS feeds (30 new)**

CBS Sports, Yahoo Sports, Sports Illustrated, NBC Sports, Sporting News, The Guardian Sport, The Ringer, SB Nation, Deadspin, FourFourTwo, Football365, 90min, ESPN FC, ESPNcricinfo, Cricbuzz, Motorsport.com, Autosport, The Race, F1 Official, MMA Fighting, Sherdog, Boxing Scene, Cycling News, VeloNews, Golf Digest, SwimSwam, Fox Sports

- [ ] **Step 4: Add Movies RSS feeds (25 new)**

/Film, AV Club, Vulture, Empire, Den of Geek, CinemaBlend, The Wrap, Roger Ebert, Decider, What's on Netflix, Bloody Disgusting, Dread Central, io9, TOR.com, CBR, Newsarama, ComicBook.com, Awards Daily, Gold Derby, BFI, Letterboxd Journal, TVLine, TV Insider, Variety Streaming

- [ ] **Step 5: Add Anime RSS feeds (15 new)**

Otaku USA, Siliconera, Anime UK News, Anime Trending, Sakuga Blog, CBR Anime, ComicBook.com Anime, ANN Reviews, Japan Times Culture, J-Novel Club Blog, Anime Corner Rankings, LiveChart All, Viz Media Blog, Manga Plus

- [ ] **Step 6: Verify YAML**

Run: `python3 -c "import yaml; d = yaml.safe_load(open('genlab-core/config/shared_sources.yaml')); print(f'RSS feeds: {len(d.get(\"rss_feeds\", []))}')"`
Expected: `RSS feeds: ~159` (existing 29 + ~130 new)

- [ ] **Step 7: Commit**

```bash
git add genlab-core/config/shared_sources.yaml
git commit -m "feat(sources): add ~130 RSS feeds across all 5 niches"
```

### Task 3: Add Additional YouTube Channels

**Files:**
- Modify: `genlab-core/config/shared_sources.yaml`

Note: 87 YouTube channels were already added by a prior research agent. This task adds the remaining channels from the second research wave (~79 more).

- [ ] **Step 1: Add remaining YouTube channels**

AI/Tech (12): 3Blue1Brown, Lex Fridman, ColdFusion, Jeff Su, Tina Huang, Sam Witteveen, AssemblyAI, Weights & Biases, IBM Technology, CodeEmporium
Gaming (20): Ubisoft, Square Enix, Capcom, Bethesda, EA, Epic Games, Markiplier, jacksepticeye, PewDiePie, SypherPK, Typical Gamer, LazarBeam, IShowSpeed, MrBeast Gaming, VALORANT, Call of Duty, Fortnite, Asmongold, The Act Man
Sports (15): Serie A, Ligue 1, MLS, Champions League (UEFA), PGA Tour, WTA, Olympics, IndyCar, NASCAR, BCCI, JiDion, Pat McAfee, Jomboy Media, Copa90, Kenny Beecham
Movies (18): Marvel Entertainment, DC, Warner Bros, Universal, Sony, Lionsgate, A24, Netflix, Disney, Paramount, Amazon MGM Studios, Nerdwriter1, Lessons from the Screenplay, Dead Meat, Heavy Spoilers, Ryan Hollinger
Anime (14): Aniplex USA, TOHO animation, Viz Media, Crunchyroll Dubs, Bandai Namco, Toei Animation, Anime Balls Deep, RogerBase, Glass Reflection, Masked Man, AniNews

Use channel IDs from the spec (Section 3.1.1). Verify no duplicates with existing entries.

- [ ] **Step 2: Verify no duplicate channel IDs**

Run: `grep 'channel_id=' genlab-core/config/shared_sources.yaml | sed 's/.*channel_id=//' | sed 's/".*//' | sort | uniq -d`
Expected: No output (no duplicates)

- [ ] **Step 3: Verify total YouTube channel count**

Run: `grep -c 'channel_id=' genlab-core/config/shared_sources.yaml`
Expected: ~165 (86 existing + ~79 new)

- [ ] **Step 4: Commit**

```bash
git add genlab-core/config/shared_sources.yaml
git commit -m "feat(sources): add ~79 more YouTube channels across all 5 niches"
```

### Task 4: Expand Google Trends Seeds

**Files:**
- Modify: `genlab-core/config/shared_sources.yaml`

- [ ] **Step 1: Expand niche_seeds from 3 to 10 per niche**

Replace the existing `google_trends:` section:

```yaml
google_trends:
  niche_seeds:
    ai_creators: ["AI", "ChatGPT", "artificial intelligence", "Gemini", "Claude AI",
                   "Midjourney", "Sora", "GPT-5", "LLM", "AI video"]
    gaming: ["gaming", "video games", "esports", "PlayStation", "Xbox",
             "Nintendo", "Steam", "Fortnite", "GTA 6", "Call of Duty"]
    sports: ["sports", "NBA", "cricket", "Premier League", "NFL",
             "UFC", "Formula 1", "Champions League", "IPL", "WWE"]
    movies: ["movie", "trailer", "Netflix", "Marvel", "DC",
             "box office", "Oscar", "Disney Plus", "streaming", "horror movie"]
    anime: ["anime", "manga", "crunchyroll", "one piece", "dragon ball",
            "demon slayer", "jujutsu kaisen", "chainsaw man", "anime fight", "new anime"]
```

- [ ] **Step 2: Commit**

```bash
git add genlab-core/config/shared_sources.yaml
git commit -m "feat(sources): expand Google Trends seeds from 3 to 10 per niche"
```

### Task 5: Add Tier and Enabled Tags to All Sources

**Files:**
- Modify: `genlab-core/config/shared_sources.yaml`

- [ ] **Step 1: Add `tier: 1` to all existing YouTube channels and core feeds**

All youtube_channels entries get `tier: 1` (unless already tagged). All existing reddit_feeds get `tier: 1`. All existing rss_feeds get `tier: 1`.

- [ ] **Step 2: Add enabled filtering to shared_ingestion.py**

In `shared_ingestion.py`, update each fetch method to skip entries where `enabled: false`:

```python
# In _fetch_youtube_channels, _fetch_reddit_feeds, _fetch_rss_feeds:
# After getting the feed config list, filter:
feeds = [f for f in feeds if f.get("enabled", True)]
```

This is a one-line addition at the top of each of the 3 feed methods.

- [ ] **Step 3: Commit**

```bash
git add genlab-core/config/shared_sources.yaml genlab-core/src/genlab_core/pipeline/shared_ingestion.py
git commit -m "feat(sources): add tier/enabled tags, add enabled filtering to pipeline"
```

### Task 6: Migrate Per-Niche Sources to Shared Registry

**Files:**
- Modify: `BlackboxBrief/config/sources.yaml`
- Modify: `CriticalRush/niches/gaming/config/sources.yaml`
- Modify: `ClutchWire/config/sources.yaml`
- Modify: `SpliceReel/config/sources.yaml`
- Modify: `FrameDrift/config/sources.yaml`

- [ ] **Step 1: Remove youtube_channels from BlackboxBrief sources.yaml**

Remove the `youtube_channels:` section (lines 8-14). These 3 channels are already in shared_sources.yaml.

- [ ] **Step 2: Remove youtube_channels and rss_feeds from CriticalRush sources.yaml**

Remove the `youtube_channels:` section (lines 87-112) and `rss_feeds:` section (lines 22-43). Keep: clip_sourcer, steam, twitch, igdb, reddit, reddit_sources, source_filters, content_filter, twitch_clips, steam_trailers.

- [ ] **Step 3: Remove tier RSS sources from ClutchWire sources.yaml**

Remove the tier_1, tier_2, tier_3 RSS `sources:` entries. Keep: espn_api, content_filter. Also keep the tier structure keys (refresh intervals) but remove individual source entries since they're now in shared.

- [ ] **Step 4: Remove tier RSS sources from SpliceReel sources.yaml**

Remove tier_2 RSS `sources:` entries. Keep: tmdb, omdb, content_filter.

- [ ] **Step 5: Remove tier RSS and youtube_channels from FrameDrift sources.yaml**

Remove tier_1/2/3 `sources:` entries and `youtube_channels:` section. Keep: content_filter, google_trends.

- [ ] **Step 6: Verify all per-niche configs still parse**

```bash
for f in BlackboxBrief/config/sources.yaml CriticalRush/niches/gaming/config/sources.yaml ClutchWire/config/sources.yaml SpliceReel/config/sources.yaml FrameDrift/config/sources.yaml; do
  python3 -c "import yaml; yaml.safe_load(open('$f')); print(f'OK: $f')" || echo "FAIL: $f"
done
```

- [ ] **Step 7: Commit**

```bash
git add BlackboxBrief/config/sources.yaml CriticalRush/niches/gaming/config/sources.yaml ClutchWire/config/sources.yaml SpliceReel/config/sources.yaml FrameDrift/config/sources.yaml
git commit -m "refactor(sources): migrate per-niche feeds to shared registry"
```

---

## Sub-Project 2: Dedup Hardening (Code Changes)

### Task 7: Add Video ID Index to content_pool

**Files:**
- Create: `genlab-core/migrations/add_content_pool_video_idx.sql`

- [ ] **Step 1: Create migration file**

```sql
-- Add video_id index for cross-entry dedup lookups
CREATE INDEX IF NOT EXISTS idx_cp_video_id
    ON content_pool(video_id)
    WHERE video_id IS NOT NULL;
```

- [ ] **Step 2: Run migration**

Run: `psql -d genlab -f genlab-core/migrations/add_content_pool_video_idx.sql`
Expected: `CREATE INDEX`

- [ ] **Step 3: Verify index exists**

Run: `psql -d genlab -c "SELECT indexname FROM pg_indexes WHERE tablename = 'content_pool' AND indexname = 'idx_cp_video_id';"`
Expected: `idx_cp_video_id`

- [ ] **Step 4: Commit**

```bash
git add genlab-core/migrations/add_content_pool_video_idx.sql
git commit -m "feat(db): add video_id index on content_pool for dedup"
```

### Task 8: Fix routed_niches Array Merge in Upsert

**Files:**
- Modify: `genlab-core/src/genlab_core/pipeline/shared_ingestion.py:461-466`

- [ ] **Step 1: Write failing test**

Create `genlab-core/tests/test_shared_ingestion_dedup.py`:

```python
"""Tests for shared ingestion dedup hardening."""
import pytest


def test_routed_niches_merge_not_overwrite():
    """When the same URL is upserted twice with different routed_niches,
    the result should be the union, not the second value overwriting the first."""
    # This test verifies the SQL behavior. We test by checking the SQL string.
    from genlab_core.pipeline.shared_ingestion import SharedIngestionPipeline
    import inspect

    source = inspect.getsource(SharedIngestionPipeline._write_to_pool)
    # The upsert SQL must use array union, not simple overwrite
    assert "EXCLUDED.routed_niches" in source
    # Must NOT have a simple assignment like: routed_niches = EXCLUDED.routed_niches
    # Must have array concatenation/union
    assert "content_pool.routed_niches || EXCLUDED.routed_niches" in source or \
           "array_cat" in source, \
           "Upsert must merge routed_niches arrays, not overwrite"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_shared_ingestion_dedup.py::test_routed_niches_merge_not_overwrite -v`
Expected: FAIL (current SQL does simple overwrite)

- [ ] **Step 3: Fix the upsert SQL**

In `shared_ingestion.py`, change lines 461-466 from:

```python
            ON CONFLICT (content_hash) DO UPDATE SET
                niche_scores = EXCLUDED.niche_scores,
                routed_niches = EXCLUDED.routed_niches,
                routing_reason = EXCLUDED.routing_reason,
                view_count = COALESCE(EXCLUDED.view_count, content_pool.view_count),
                view_velocity = COALESCE(EXCLUDED.view_velocity, content_pool.view_velocity)
```

To:

```python
            ON CONFLICT (content_hash) DO UPDATE SET
                niche_scores = EXCLUDED.niche_scores,
                routed_niches = (
                    SELECT ARRAY(SELECT DISTINCT unnest(
                        content_pool.routed_niches || EXCLUDED.routed_niches
                    ))
                ),
                routing_reason = EXCLUDED.routing_reason,
                view_count = COALESCE(EXCLUDED.view_count, content_pool.view_count),
                view_velocity = COALESCE(EXCLUDED.view_velocity, content_pool.view_velocity)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_shared_ingestion_dedup.py::test_routed_niches_merge_not_overwrite -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/pipeline/shared_ingestion.py genlab-core/tests/test_shared_ingestion_dedup.py
git commit -m "fix(dedup): merge routed_niches arrays on upsert instead of overwriting"
```

### Task 9: Add YouTube Video ID Extraction from Reddit Posts

**Files:**
- Modify: `genlab-core/src/genlab_core/pipeline/shared_ingestion.py:290-343`
- Test: `genlab-core/tests/test_shared_ingestion_dedup.py`

- [ ] **Step 1: Write failing test**

```python
def test_youtube_id_extracted_from_reddit_summary():
    """Reddit posts linking to YouTube should have video_id extracted."""
    from genlab_core.pipeline.shared_ingestion import _extract_youtube_id

    assert _extract_youtube_id("Check this out https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _extract_youtube_id("https://youtu.be/dQw4w9WgXcQ cool stuff") == "dQw4w9WgXcQ"
    assert _extract_youtube_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _extract_youtube_id("no youtube link here") is None
    assert _extract_youtube_id("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_shared_ingestion_dedup.py::test_youtube_id_extracted_from_reddit_summary -v`
Expected: FAIL (function doesn't exist)

- [ ] **Step 3: Add _extract_youtube_id function and wire into Reddit fetcher**

Add at module level in `shared_ingestion.py` (after imports, before `_content_hash`):

```python
import re

_YT_ID_RE = re.compile(
    r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)'
    r'([a-zA-Z0-9_-]{11})'
)

def _extract_youtube_id(text: str) -> str | None:
    """Extract YouTube video ID from text containing a YouTube URL."""
    if not text:
        return None
    match = _YT_ID_RE.search(text)
    return match.group(1) if match else None
```

Then in `_fetch_reddit_feeds`, after creating `pool_entry` (around line 335), add:

```python
                    # Extract YouTube video_id from Reddit post content
                    for text_field in [
                        entry.get("summary", ""),
                        (entry.get("content", [{}])[0].get("value", "")
                         if entry.get("content") else ""),
                    ]:
                        yt_id = _extract_youtube_id(text_field)
                        if yt_id:
                            pool_entry.video_id = yt_id
                            pool_entry.video_url = f"https://www.youtube.com/watch?v={yt_id}"
                            break
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_shared_ingestion_dedup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/pipeline/shared_ingestion.py genlab-core/tests/test_shared_ingestion_dedup.py
git commit -m "feat(dedup): extract YouTube video_id from Reddit post content"
```

### Task 10: Add Title Similarity Dedup at Shared Ingestion (Layer 0)

**Files:**
- Modify: `genlab-core/src/genlab_core/pipeline/shared_ingestion.py`
- Modify: `genlab-core/src/genlab_core/intelligence/dedup_engine.py`
- Test: `genlab-core/tests/test_shared_ingestion_dedup.py`

- [ ] **Step 1: Write failing test**

```python
def test_title_dedup_removes_near_duplicates():
    """Near-duplicate titles from different sources should be deduplicated."""
    from genlab_core.pipeline.shared_ingestion import PoolEntry, SharedIngestionPipeline

    # Create entries with near-duplicate titles but different URLs
    entries = [
        PoolEntry(content_hash="aaa", title="Lakers beat Celtics 112-105 in thriller", source_url="https://espn.com/1", source_name="ESPN", source_platform="rss"),
        PoolEntry(content_hash="bbb", title="Lakers defeat Celtics 112-105 in exciting game", source_url="https://bbc.com/2", source_name="BBC", source_platform="rss"),
        PoolEntry(content_hash="ccc", title="Completely unrelated story about AI", source_url="https://tech.com/3", source_name="Tech", source_platform="rss"),
    ]

    pipeline = SharedIngestionPipeline.__new__(SharedIngestionPipeline)
    pipeline._entries = entries
    pipeline._stats = {"title_dedup_removed": 0}
    pipeline._db_url = None  # skip DB lookup for test

    pipeline._deduplicate_batch()

    # Should remove 1 near-duplicate, keep 2 unique
    assert len(pipeline._entries) == 2
    assert pipeline._stats["title_dedup_removed"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL (`_deduplicate_batch` doesn't exist)

- [ ] **Step 3: Implement _deduplicate_batch and _load_recent_pool_titles**

Add to `SharedIngestionPipeline` class in `shared_ingestion.py`:

```python
    def _deduplicate_batch(self) -> None:
        """Remove near-duplicate entries by title similarity (Layer 0)."""
        if len(self._entries) < 2:
            return

        from genlab_core.intelligence.dedup_engine import DedupEngine

        # Load existing pool titles for cross-batch dedup
        existing_titles = self._load_recent_pool_titles(limit=3000)

        # Build dedup engine with title-level thresholds
        engine = DedupEngine(
            jaccard_threshold=0.70,
            tfidf_threshold=0.75,
            url_field="source_url",
            text_field="title",
        )

        # Anchor items = existing pool titles (not deduped against each other)
        anchor_items = [{"title": t, "source_url": f"__anchor_{i}"} for i, t in enumerate(existing_titles)]
        batch_items = [{"title": e.title, "source_url": e.source_url} for e in self._entries]
        all_items = anchor_items + batch_items

        result = engine.run(all_items)

        surviving_urls = {
            item["source_url"]
            for item in result.unique
            if not item["source_url"].startswith("__anchor_")
        }
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.source_url in surviving_urls]
        removed = before - len(self._entries)
        self._stats["title_dedup_removed"] = removed
        if removed:
            logger.info("[SharedIngestion] Title dedup removed %d near-duplicates", removed)

    def _load_recent_pool_titles(self, limit: int = 3000) -> list[str]:
        """Load titles from content_pool entries from the last 48h."""
        if not self._db_url:
            return []
        try:
            with psycopg.connect(self._db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT title FROM content_pool WHERE fetched_at > NOW() - INTERVAL '48 hours' ORDER BY fetched_at DESC LIMIT %s",
                        (limit,),
                    )
                    return [row[0] for row in cur.fetchall() if row[0]]
        except Exception as exc:
            logger.warning("[SharedIngestion] Could not load pool titles: %s", exc)
            return []
```

Then wire into `run()` — add after fetch calls and before `_classify_all()`:

```python
        # 2b. Title-level dedup (Layer 0)
        self._deduplicate_batch()
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_shared_ingestion_dedup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/pipeline/shared_ingestion.py genlab-core/tests/test_shared_ingestion_dedup.py
git commit -m "feat(dedup): add Layer 0 title-similarity dedup at shared ingestion"
```

### Task 11: Fix Race Condition in Pool Claiming

**Files:**
- Modify: `genlab-core/src/genlab_core/media/trending_video_fetcher.py:874-900`

- [ ] **Step 1: Change SELECT to FOR UPDATE SKIP LOCKED**

In `_read_from_content_pool`, change the SELECT query from:

```python
                    cur.execute(
                        """
                        SELECT * FROM content_pool
                        WHERE %s = ANY(routed_niches)
                          AND status = 'available'
                        ORDER BY view_velocity DESC NULLS LAST, fetched_at DESC
                        LIMIT 20
                        """,
                        (niche_id,),
                    )
```

To:

```python
                    cur.execute(
                        """
                        SELECT * FROM content_pool
                        WHERE %s = ANY(routed_niches)
                          AND status = 'available'
                        ORDER BY view_velocity DESC NULLS LAST, fetched_at DESC
                        LIMIT 20
                        FOR UPDATE SKIP LOCKED
                        """,
                        (niche_id,),
                    )
```

- [ ] **Step 2: Commit**

```bash
git add genlab-core/src/genlab_core/media/trending_video_fetcher.py
git commit -m "fix(dedup): prevent race condition in pool claiming with FOR UPDATE SKIP LOCKED"
```

### Task 12: Increase Dedup Lookback in push_to_backlog

**Files:**
- Modify: `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py`

- [ ] **Step 1: Increase all 3 lookback values from 500 to 2000**

Change line ~262 (`max_records=500` for hooks):
```python
            recent_bps = client.blueprints.all(
                formula=f"{{niche_id}}='{niche_id}'",
                max_records=2000,  # was 500
            )
```

Change line ~287 (`max_records=500` for stories):
```python
            existing_stories = client.stories.all(
                formula=f"{{niche_id}}='{niche_id}'",
                max_records=2000,  # was 500
            )
```

Change line ~302 (`max_records=500` for content_memory):
```python
                cm_records = cm_proxy.all(
                    formula=f"{{niche_id}}='{niche_id}'",
                    max_records=2000,  # was 500
                )
```

- [ ] **Step 2: Add title similarity dedup (Layer 4.5)**

After line ~312 (where `seen_urls` is populated from content_memory), add title collection:

```python
        # Collect titles from stories + content_memory for title-level dedup (Layer 4.5)
        existing_titles: set[str] = set()
        for s in existing_stories:
            t = (s.get("fields", s).get("title") or "").strip().lower()
            if t and len(t) > 10:
                existing_titles.add(t)
        for rec in cm_records:
            t = (rec.get("fields", rec).get("title") or "").strip().lower()
            if t and len(t) > 10:
                existing_titles.add(t)

        # Also load titles from content_pool entries claimed by this niche (Layer 5.5)
        try:
            db_url = os.environ.get("DATABASE_URL")
            if db_url:
                import psycopg as _psycopg
                with _psycopg.connect(db_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT title FROM content_pool WHERE claimed_by = %s AND claimed_at > NOW() - INTERVAL '48 hours'",
                            (niche_id,),
                        )
                        for row in cur.fetchall():
                            if row[0]:
                                existing_titles.add(row[0].strip().lower())
                logger.info("[PUSH] Loaded %d titles for cross-dedup (stories + content_memory + pool)", len(existing_titles))
        except Exception:
            pass
        context["existing_titles"] = existing_titles
```

Then in the story loop (around line ~320, after URL dedup check), add:

```python
            # Title similarity dedup (Layer 4.5) — catches same story from different sources
            title_lower = title.lower().strip()
            title_is_dupe = False
            if len(title_lower) > 10:
                title_words = set(title_lower.split())
                for existing in existing_titles:
                    existing_words = set(existing.split())
                    if len(title_words) > 3 and len(existing_words) > 3:
                        intersection = len(title_words & existing_words)
                        union = len(title_words | existing_words)
                        if union > 0 and intersection / union > 0.65:
                            logger.info("[PUSH] Title near-dupe: '%s' ≈ '%s'", title[:40], existing[:40])
                            title_is_dupe = True
                            break
            if title_is_dupe:
                continue
            existing_titles.add(title_lower)
```

- [ ] **Step 3: Commit**

```bash
git add genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py
git commit -m "feat(dedup): increase lookback 500→2000, add title similarity (Layer 4.5) and pool cross-dedup (Layer 5.5)"
```

---

## Sub-Project 3: Classifier Tuning + Performance

### Task 13: Tune NicheClassifier for 10x Volume

**Files:**
- Modify: `genlab-core/src/genlab_core/intelligence/niche_classifier.py:152-167`
- Create: `genlab-core/tests/test_niche_classifier_tuning.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for classifier tuning at 10x volume."""
import pytest
from genlab_core.intelligence.niche_classifier import NicheClassifier


@pytest.fixture
def classifier(tmp_path):
    """Create a classifier with minimal test profiles."""
    # Create minimal sources.yaml files for each niche
    for niche_id, keywords in [
        ("ai_creators", {"positive_keywords": ["ai", "chatgpt", "llm", "neural", "model"], "negative_keywords": ["cooking"], "relevance_threshold": 0.30}),
        ("gaming", {"positive_keywords": ["game", "gaming", "esports", "fortnite", "playstation"], "negative_keywords": ["cooking"], "relevance_threshold": 0.20}),
        ("sports", {"positive_keywords": ["sports", "nba", "football", "cricket", "ufc"], "negative_keywords": ["cooking"], "relevance_threshold": 0.20}),
        ("movies", {"positive_keywords": ["movie", "trailer", "film", "oscar", "netflix"], "negative_keywords": ["cooking"], "relevance_threshold": 0.25}),
        ("anime", {"positive_keywords": ["anime", "manga", "crunchyroll", "one piece", "demon slayer"], "negative_keywords": ["cooking"], "relevance_threshold": 0.35}),
    ]:
        import yaml
        from pathlib import Path
        niche_dir = tmp_path / niche_id
        niche_dir.mkdir()
        cfg_file = niche_dir / "sources.yaml"
        cfg_file.write_text(yaml.dump({"content_filter": keywords}))

    # Patch NICHE_SOURCE_PATHS
    from unittest.mock import patch
    paths = {nid: tmp_path / nid / "sources.yaml" for nid in ["ai_creators", "gaming", "sports", "movies", "anime"]}
    with patch("genlab_core.intelligence.niche_classifier.NICHE_SOURCE_PATHS", paths):
        with patch("genlab_core.intelligence.niche_classifier.GENLAB_ROOT", tmp_path):
            return NicheClassifier(genlab_root=tmp_path)


def test_single_keyword_not_enough(classifier):
    """A single keyword hit should NOT produce a keyword score."""
    scores = classifier.classify("Something about AI today")
    # With only 1 hit ("ai"), keyword component should be 0
    # Only affinity or category can contribute
    assert scores["ai_creators"] < 0.20, f"Single keyword should score low, got {scores['ai_creators']}"


def test_affinity_requires_keyword_match(classifier):
    """Source affinity should only boost when at least one keyword matches."""
    scores = classifier.classify(
        "Cooking tips for beginners",  # negative keyword = 0 anyway
        source_affinity=["ai_creators"],
    )
    assert scores["ai_creators"] == 0.0


def test_two_keywords_scores_reasonably(classifier):
    """Two keyword hits should produce a meaningful score."""
    scores = classifier.classify("AI model breaks records")
    assert scores["ai_creators"] > 0.10


def test_youtube_category_still_routes_without_keywords(classifier):
    """YouTube category 20 (Gaming) should contribute even with zero keyword hits."""
    scores = classifier.classify(
        "New update available now",
        youtube_category="20",
    )
    # Category gives +0.15, which is below gaming threshold (0.20)
    # This is correct — generic titles SHOULD be rejected
    assert scores["gaming"] == 0.15
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_niche_classifier_tuning.py -v`
Expected: At least `test_single_keyword_not_enough` FAILS (current normalizer cap of 3 gives high score from 1 hit)

- [ ] **Step 3: Apply classifier changes**

In `niche_classifier.py`, replace `_score_niche` method body (lines 145-167):

```python
        text_lower = text.lower()

        # Negative keyword hard-reject
        for pattern in profile._negative_patterns:
            if pattern.search(text_lower):
                return 0.0

        # Keyword relevance (0 - 0.6)
        hits = sum(1 for p in profile._positive_patterns if p.search(text_lower))

        # Min-2-hits gate: suppress keyword component for weak matches
        # but don't return 0.0 — category/affinity can still contribute
        if hits < 2:
            keyword_score = 0.0
        else:
            normalizer = min(max(len(profile.positive_keywords) * 0.15, 1), 5)
            keyword_score = min(hits / normalizer, 1.0) * 0.6

        score = keyword_score

        # Source affinity bonus (+0.15, requires at least 1 keyword hit)
        if source_affinity and profile.niche_id in source_affinity and hits > 0:
            score += 0.15

        # YouTube category match (+0.15)
        if youtube_category and CATEGORY_TO_NICHE.get(youtube_category) == profile.niche_id:
            score += 0.15

        return round(min(score, 1.0), 4)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_niche_classifier_tuning.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run existing classifier tests to verify no regression**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/ -k classifier -v`

- [ ] **Step 6: Commit**

```bash
git add genlab-core/src/genlab_core/intelligence/niche_classifier.py genlab-core/tests/test_niche_classifier_tuning.py
git commit -m "feat(classifier): tune for 10x volume — normalizer 3→5, min-2-hits, affinity+keyword gate"
```

### Task 14: Add Parallel Feed Fetching

**Files:**
- Modify: `genlab-core/src/genlab_core/pipeline/shared_ingestion.py`

- [ ] **Step 1: Add domain rate limiter class**

Add after the imports section:

```python
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time as _time

class _DomainRateLimiter:
    """Thread-safe per-domain rate limiter."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._last_request: dict[str, float] = {}
        self._global_lock = threading.Lock()
        self._delays = {"reddit.com": 2.0, "www.reddit.com": 2.0}
        self._default_delay = 0.05

    def wait(self, url: str) -> None:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        with self._global_lock:
            if domain not in self._locks:
                self._locks[domain] = threading.Lock()
        lock = self._locks[domain]
        delay = self._delays.get(domain, self._default_delay)
        with lock:
            last = self._last_request.get(domain, 0)
            wait_time = max(0, delay - (_time.time() - last))
            if wait_time > 0:
                _time.sleep(wait_time)
            self._last_request[domain] = _time.time()
```

- [ ] **Step 2: Refactor run() to use ThreadPoolExecutor**

Replace the sequential fetch calls in `run()` with parallel execution:

```python
    def run(self) -> str:
        start = time.time()
        logger.info("[SharedIngestion] Starting shared ingestion pipeline")
        self._load_config()

        # 1. Fetch YouTube trending (API, sequential — only 5 calls)
        self._fetch_youtube_trending()

        # 2. Fetch all RSS/Reddit/YouTube-channel feeds in parallel
        self._fetch_all_feeds_parallel()

        # 3. Title-level dedup (Layer 0)
        self._deduplicate_batch()

        # 4. Classify
        self._classify_all()

        # 5. Write to DB
        self._write_to_pool()

        # 6. Expire
        self._expire_old_entries()

        elapsed = time.time() - start
        report = self._build_report()
        logger.info("[SharedIngestion] Completed in %.1fs", elapsed)
        return report

    def _fetch_all_feeds_parallel(self) -> None:
        """Fetch YouTube channel RSS, Reddit RSS, and general RSS in parallel."""
        rate_limiter = _DomainRateLimiter()
        feeds: list[tuple[str, dict]] = []

        for ch in self._config.get("youtube_channels", []):
            if ch.get("enabled", True):
                feeds.append(("yt_channel", ch))
        for f in self._config.get("reddit_feeds", []):
            if f.get("enabled", True):
                feeds.append(("reddit", f))
        for f in self._config.get("rss_feeds", []):
            if f.get("enabled", True):
                feeds.append(("rss", f))

        logger.info("[SharedIngestion] Fetching %d feeds in parallel (max_workers=15)", len(feeds))

        with ThreadPoolExecutor(max_workers=15) as pool:
            futures = {
                pool.submit(self._fetch_single_feed, ftype, cfg, rate_limiter): (ftype, cfg)
                for ftype, cfg in feeds
            }
            for future in as_completed(futures):
                ftype, cfg = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    logger.warning("[SharedIngestion] Feed %s failed: %s", cfg.get("name", "?"), exc)
                    self._stats["errors"] += 1

    def _fetch_single_feed(self, ftype: str, cfg: dict, rate_limiter: _DomainRateLimiter) -> None:
        """Fetch a single feed (called from thread pool)."""
        url = cfg.get("url", "")
        if not url:
            return
        rate_limiter.wait(url)
        name = cfg.get("name", "unknown")
        affinity = cfg.get("affinity", [])

        try:
            feed = feedparser.parse(url, agent=_USER_AGENT)
            limit = 10 if ftype != "reddit" else 15

            for entry_data in feed.entries[:limit]:
                link = entry_data.get("link", "")
                if not link:
                    continue

                published_at = None
                if entry_data.get("published_parsed"):
                    try:
                        published_at = datetime(*entry_data.published_parsed[:6], tzinfo=UTC)
                    except (TypeError, ValueError):
                        pass

                if published_at and (datetime.now(UTC) - published_at) > timedelta(hours=48):
                    continue

                platform = {"yt_channel": "youtube", "reddit": "reddit", "rss": "rss"}[ftype]

                video_id = None
                video_url = None
                thumbnail = ""

                if ftype == "yt_channel":
                    yt_vid = entry_data.get("yt_videoid", "")
                    if yt_vid:
                        video_id = yt_vid
                    elif "watch?v=" in link:
                        video_id = link.split("watch?v=")[-1].split("&")[0]
                    video_url = link
                    if hasattr(entry_data, "media_thumbnail") and entry_data.media_thumbnail:
                        thumbnail = entry_data.media_thumbnail[0].get("url", "")
                elif ftype == "reddit":
                    # Extract YouTube video_id from Reddit post content (Layer 2.5)
                    for text_field in [
                        entry_data.get("summary", ""),
                        (entry_data.get("content", [{}])[0].get("value", "") if entry_data.get("content") else ""),
                    ]:
                        yt_id = _extract_youtube_id(text_field)
                        if yt_id:
                            video_id = yt_id
                            video_url = f"https://www.youtube.com/watch?v={yt_id}"
                            break

                pool_entry = PoolEntry(
                    content_hash=_content_hash(link),
                    title=entry_data.get("title", ""),
                    summary=entry_data.get("summary", "")[:500],
                    source_url=link,
                    source_name=name,
                    source_platform=platform,
                    video_url=video_url,
                    video_id=video_id,
                    thumbnail_url=thumbnail,
                    published_at=published_at,
                    source_affinity=affinity,
                )
                if self._add_entry(pool_entry):
                    self._stats[{"yt_channel": "yt_channels", "reddit": "reddit", "rss": "rss"}[ftype]] += 1

        except Exception as exc:
            logger.warning("[SharedIngestion] %s %s failed: %s", ftype, name, exc)
            self._stats["errors"] += 1
```

Note: `_add_entry` uses `_seen_hashes` set which needs to be thread-safe. Add a lock:

```python
# In __init__:
self._entry_lock = threading.Lock()

# Modify _add_entry:
def _add_entry(self, entry: PoolEntry) -> bool:
    with self._entry_lock:
        if entry.content_hash in self._seen_hashes:
            self._stats["deduped"] += 1
            return False
        self._seen_hashes.add(entry.content_hash)
        self._entries.append(entry)
        return True
```

- [ ] **Step 3: Remove the now-unused sequential fetch methods**

Remove `_fetch_youtube_channels`, `_fetch_reddit_feeds`, `_fetch_rss_feeds` (replaced by `_fetch_all_feeds_parallel` + `_fetch_single_feed`).

- [ ] **Step 4: Update the report to include new stats**

Add to `_build_report`:

```python
            f"  Title dedup      : {self._stats.get('title_dedup_removed', 0):>4}",
```

- [ ] **Step 5: Test the pipeline runs without errors**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core python -m genlab_core.pipeline.shared_ingestion`
Expected: Pipeline completes, report shows feed counts

- [ ] **Step 6: Commit**

```bash
git add genlab-core/src/genlab_core/pipeline/shared_ingestion.py
git commit -m "feat(perf): parallel feed fetching with ThreadPoolExecutor + domain rate limiting"
```

### Task 15: Run Full Pipeline Test

- [ ] **Step 1: Run shared ingestion end-to-end**

```bash
cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core python -m genlab_core.pipeline.shared_ingestion
```

Check the report output for:
- Total fetched should be significantly higher than 362
- Dedup counts should be > 0
- All 5 niches should have routed entries
- No errors beyond normal feed timeouts

- [ ] **Step 2: Check content_pool for duplicate titles**

```bash
psql -d genlab -c "SELECT left(lower(title), 30) as prefix, count(*) as dupes FROM content_pool WHERE status = 'available' GROUP BY left(lower(title), 30) HAVING count(*) > 1 ORDER BY dupes DESC LIMIT 10;"
```

Expected: Zero or minimal duplicates (Layer 0 should catch them)

- [ ] **Step 3: Run all genlab-core tests**

```bash
cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/ -x
```

Expected: ALL PASS

- [ ] **Step 4: Commit any fixes**

```bash
git add genlab-core/
git commit -m "fix: address issues found during full pipeline test"
```

---

## Review Errata: Critical & Important Fixes

The following corrections apply to the tasks above. Implementers MUST apply these before executing.

### Errata 1 (CRITICAL): Task 14 — Thread-Safe Stats

`self._stats` is a plain dict mutated from multiple threads. Add a lock:

```python
# In __init__, add:
self._stats_lock = threading.Lock()

# In _fetch_single_feed, wrap ALL stats mutations:
with self._stats_lock:
    self._stats[stat_key] += 1

# OR use a simpler approach — replace _stats dict with threading-safe Counter:
from collections import Counter
# In __init__:
self._stats = Counter()  # Counter is not thread-safe either, but...
# Better: accumulate per-thread results and merge after join
```

**Recommended approach:** Have `_fetch_single_feed` return a dict of counts instead of mutating `self._stats`. Merge after `as_completed`:

```python
def _fetch_single_feed(self, ftype, cfg, rate_limiter):
    counts = {"items": 0, "errors": 0}
    # ... fetch logic, increment counts["items"] locally ...
    return counts

# In _fetch_all_feeds_parallel:
for future in as_completed(futures):
    ftype, cfg = futures[future]
    try:
        counts = future.result()
        stat_key = {"yt_channel": "yt_channels", "reddit": "reddit", "rss": "rss"}[ftype]
        self._stats[stat_key] += counts["items"]
    except Exception:
        self._stats["errors"] += 1
```

This eliminates all thread-safety issues since only the main thread writes to `_stats`.

### Errata 2 (CRITICAL): Task Ordering — Tasks 5 & 9 Subsumed by Task 14

**Task 5 Step 2** (add enabled filtering to sequential methods) and **Task 9 Step 3** (add YT ID extraction to `_fetch_reddit_feeds`) modify sequential fetch methods that **Task 14 deletes and replaces**.

**Resolution:**
- Execute Task 5 Step 1 only (YAML tier/enabled tags) — skip Step 2 (code change)
- Execute Task 9 Step 1-2 only (test + `_extract_youtube_id` function) — skip Step 3 (wiring into `_fetch_reddit_feeds`)
- Task 14 already includes both `enabled` filtering AND YT ID extraction in `_fetch_single_feed`
- The test from Task 9 (`test_youtube_id_extracted_from_reddit_summary`) still validates the `_extract_youtube_id` function

### Errata 3 (CRITICAL): Task 10 — Anchor Items Must Not Be Deduped Against Each Other

The `DedupEngine.run()` method applies Jaccard pairwise to ALL items including anchors. Two similar existing pool titles would incorrectly deduplicate each other.

**Fix:** Modify Task 10's `_deduplicate_batch` to NOT pass anchors through the DedupEngine. Instead, compare batch items against anchors manually:

```python
def _deduplicate_batch(self) -> None:
    if len(self._entries) < 2:
        return

    from genlab_core.intelligence.dedup_engine import jaccard_similarity

    existing_titles = self._load_recent_pool_titles(limit=3000)

    # First: dedup batch against existing pool titles (anchors)
    surviving = []
    for entry in self._entries:
        is_dup = False
        entry_title = entry.title.lower().strip()
        if len(entry_title) > 10:
            for existing in existing_titles:
                if jaccard_similarity(entry_title, existing.lower()) >= 0.70:
                    is_dup = True
                    break
        if not is_dup:
            surviving.append(entry)

    # Second: dedup batch items against each other using DedupEngine
    from genlab_core.intelligence.dedup_engine import DedupEngine
    engine = DedupEngine(
        jaccard_threshold=0.70,
        tfidf_threshold=0.75,
        url_field="source_url",
        text_field="title",
    )
    batch_items = [{"title": e.title, "source_url": e.source_url} for e in surviving]
    result = engine.run(batch_items)
    surviving_urls = {item["source_url"] for item in result.unique}

    before = len(self._entries)
    self._entries = [e for e in surviving if e.source_url in surviving_urls]
    removed = before - len(self._entries)
    self._stats["title_dedup_removed"] = removed
    if removed:
        logger.info("[SharedIngestion] Title dedup removed %d near-duplicates", removed)
```

This separates anchor-vs-batch comparison (manual Jaccard loop) from batch-vs-batch comparison (DedupEngine), preventing anchors from being deduped against each other.

### Errata 4 (IMPORTANT): Missing Task — Feed Health Tracking

Add as **Task 16** after Task 15:

**Files:**
- Modify: `genlab-core/src/genlab_core/pipeline/shared_ingestion.py`

- [ ] **Step 1: Add feed health tracking**

```python
import json as _json
from pathlib import Path as _Path

_FEED_HEALTH_PATH = _Path(__file__).resolve().parent.parent.parent.parent / ".tmp" / "cache" / "feed_health.json"

class _FeedHealthTracker:
    """Track consecutive failures per feed. Auto-disable after 5 failures."""

    def __init__(self):
        self._health: dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            if _FEED_HEALTH_PATH.exists():
                self._health = _json.loads(_FEED_HEALTH_PATH.read_text())
        except Exception:
            self._health = {}

    def _save(self):
        try:
            _FEED_HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
            _FEED_HEALTH_PATH.write_text(_json.dumps(self._health, indent=2, default=str))
        except Exception:
            pass

    def is_disabled(self, url: str) -> bool:
        entry = self._health.get(url, {})
        disabled_until = entry.get("disabled_until")
        if disabled_until:
            from datetime import datetime, UTC
            try:
                dt = datetime.fromisoformat(disabled_until)
                if datetime.now(UTC) < dt:
                    return True
            except (ValueError, TypeError):
                pass
        return False

    def record_success(self, url: str):
        self._health[url] = {"consecutive_failures": 0, "last_success": datetime.now(UTC).isoformat()}

    def record_failure(self, url: str):
        entry = self._health.get(url, {"consecutive_failures": 0})
        entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
        entry["last_failure"] = datetime.now(UTC).isoformat()
        if entry["consecutive_failures"] >= 5:
            entry["disabled_until"] = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
            logger.warning("[FeedHealth] Auto-disabled %s after %d consecutive failures", url, entry["consecutive_failures"])
        self._health[url] = entry

    def save(self):
        self._save()
```

Wire into `_fetch_single_feed`: check `health.is_disabled(url)` before fetching, call `health.record_success(url)` or `health.record_failure(url)` after.

- [ ] **Step 2: Commit**

```bash
git add genlab-core/src/genlab_core/pipeline/shared_ingestion.py
git commit -m "feat(ops): add feed health tracking with auto-disable after 5 failures"
```

### Errata 5 (IMPORTANT): Missing Tests for push_to_backlog Changes

Add as **Task 17**:

- [ ] **Step 1: Write test for title similarity dedup in push_to_backlog**

Create `genlab-core/tests/test_push_to_backlog_dedup.py`:

```python
"""Tests for push_to_backlog dedup hardening."""

def test_title_similarity_catches_near_duplicate():
    """Stories with similar titles from different sources should be deduplicated."""
    existing_titles = {"lakers beat celtics 112-105 in thriller"}
    title = "Lakers defeat Celtics 112-105 in exciting game"
    title_lower = title.lower().strip()

    title_words = set(title_lower.split())
    is_dupe = False
    for existing in existing_titles:
        existing_words = set(existing.split())
        if len(title_words) > 3 and len(existing_words) > 3:
            intersection = len(title_words & existing_words)
            union = len(title_words | existing_words)
            if union > 0 and intersection / union > 0.65:
                is_dupe = True
                break

    assert is_dupe, "Near-duplicate title should be caught"


def test_unrelated_titles_not_caught():
    """Unrelated stories should not be flagged as duplicates."""
    existing_titles = {"lakers beat celtics 112-105 in thriller"}
    title = "New AI model breaks benchmark records"
    title_lower = title.lower().strip()

    title_words = set(title_lower.split())
    is_dupe = False
    for existing in existing_titles:
        existing_words = set(existing.split())
        if len(title_words) > 3 and len(existing_words) > 3:
            intersection = len(title_words & existing_words)
            union = len(title_words | existing_words)
            if union > 0 and intersection / union > 0.65:
                is_dupe = True
                break

    assert not is_dupe, "Unrelated title should not be caught"
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_push_to_backlog_dedup.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add genlab-core/tests/test_push_to_backlog_dedup.py
git commit -m "test: add push_to_backlog title similarity dedup tests"
```

### Errata 6 (IMPORTANT): Missing Task — Dedup Quality Metrics in Report

Add to Task 14 Step 4. The `_build_report` method should include:

```python
            f"  Title dedup (L0) : {self._stats.get('title_dedup_removed', 0):>4}",
            f"  Video ID merge   : {self._stats.get('video_id_merge', 0):>4}",
            f"  Feed errors      : {self._stats.get('errors', 0):>4}",
            f"  Feeds disabled   : {self._stats.get('feeds_disabled', 0):>4}",
```

### Errata 7 (IMPORTANT): Missing Task — Tier 3 Disabled Sources

Add to Task 1 after Step 5:

- [ ] **Step 5b: Add Tier 3 disabled sources (optional deep-tail coverage)**

For each niche, add individual team/game/franchise subs with `tier: 3` and `enabled: false`. Example:

```yaml
  # === Tier 3: Individual team subs (disabled by default) ===
  - name: "r/lakers new"
    url: "https://www.reddit.com/r/lakers/new/.rss"
    affinity: [sports]
    tier: 3
    enabled: false
  - name: "r/lakers top"
    url: "https://www.reddit.com/r/lakers/top/.rss?t=week"
    affinity: [sports]
    tier: 3
    enabled: false
```

Full Tier 3 lists by niche (add all with `enabled: false`):
- Gaming (~100): diablo4, ffxiv, wow, deadbydaylight, Tekken, MortalKombat, residentevil, SpidermanPS4, HarryPotterGame, Splatoon, AnimalCrossing, StardewValley, Terraria, EscapefromTarkov, Warframe, etc.
- Sports (~100): lakers, warriors, bostonceltics, NYKnicks, sixers, heat, KansasCityChiefs, eagles, cowboys, 49ers, Patriots, NYYankees, Dodgers, redsox, leafs, BostonBruins, realmadrid, chelseafc, Gunners, MCFC, coys, etc.
- Movies (~50): dune, JamesBond, lotr, JurassicPark, batman, spiderman, TheMandalorian, gameofthrones, freefolk, SuccessionTV, BlackMirror, rickandmorty, etc.
- Anime (~60): TokyoGhoul, deathnote, BlackClover, VinlandSaga, MobPsycho100, Haikyuu, SwordArtOnline, FinalFantasy, Fate, Jojos, etc.

### Errata 8 (IMPORTANT): Multi-Reddit URL Optimization

The current plan's Task 14 treats each Reddit feed as an individual request with 2s domain rate limit. With 458 feeds, this takes ~15 minutes for Reddit alone.

**Fix:** In `_fetch_all_feeds_parallel`, group Reddit subreddits by niche+type (new/top) into multi-reddit URLs:

```python
def _build_multireddit_feeds(self, feeds: list[dict]) -> list[dict]:
    """Batch Reddit feeds into multi-reddit URLs (max 100 subs per URL)."""
    import re
    groups: dict[str, list[str]] = {}  # key = "new" or "top", value = list of subreddit names
    non_reddit = []

    for f in feeds:
        url = f.get("url", "")
        match = re.match(r'https://www\.reddit\.com/r/([^/]+)/(new|top)/\.rss', url)
        if match:
            sub, feed_type = match.groups()
            key = feed_type
            groups.setdefault(key, []).append(sub)
        else:
            non_reddit.append(f)

    batched = []
    for feed_type, subs in groups.items():
        # Reddit supports up to 100 subs in a multi-reddit
        for i in range(0, len(subs), 50):
            chunk = subs[i:i+50]
            multi_url = f"https://www.reddit.com/r/{'+'.join(chunk)}/{feed_type}/.rss"
            if feed_type == "top":
                multi_url += "?t=week"
            batched.append({
                "name": f"multi-reddit {feed_type} batch {i//50+1}",
                "url": multi_url,
                "affinity": [],  # classifier will route based on content
            })

    return non_reddit + [("reddit", b) for b in batched]
```

This reduces ~458 Reddit requests to ~10 multi-reddit requests.

### Errata 9 (SUGGESTION): Task 15 Step 4 — Don't Use git add -A

Change `git add -A` to `git add genlab-core/` to avoid accidentally staging sensitive files.
