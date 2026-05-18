# Content Sourcing Mega-Expansion + Dedup Fortress

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand GenLab's content sourcing from 73 to ~640 unique content sources (~810 feed URLs including Reddit new+top variants), all zero-cost RSS/Atom feeds. Unify into a single shared registry and harden deduplication to prevent duplicate posts at 10x volume.

**Source count methodology:** A "source" is a unique content origin (one YouTube channel, one subreddit, one RSS site). Each Reddit subreddit generates 2 feed URLs (new + top). YouTube channels and RSS feeds generate 1 feed URL each. Tier 3 sources (~200 Reddit subs) are disabled by default.

**Date:** 2026-03-25

---

## 1. Problem Statement

### 1.1 Source Coverage Is Thin and Unbalanced

Current source inventory (73 sources in `shared_sources.yaml`):

| Niche | YouTube Channels | Reddit Subs | RSS Feeds | Total |
|-------|-----------------|-------------|-----------|-------|
| AI Creators (BB) | 3 | 9 | 3 | 15 |
| Gaming (CR) | 5 | 0 (in shared) | 7 | 12 |
| Sports (CW) | 5 | 0 (in shared) | 5 | 10 |
| Movies (SR) | 4 | 0 (in shared) | 7 | 11 |
| Anime (FD) | 4 | 0 (in shared) | 7 | 11 |
| **Cross-niche** | 0 | 0 | 0 | 0 |
| **Total** | **21** | **9** | **29** | **73** |

Problems:
- **Movies & Anime have zero Reddit sources in the shared registry** despite r/movies (35M), r/anime (9M), r/OnePiece (2M) etc. being massive viral content hubs
- **Gaming, Sports, Movies, Anime have zero Reddit sources** — only AI Creators has Reddit feeds
- **YouTube channel lists are thin** — 3-5 channels per niche vs. dozens of relevant creators
- **Google Trends seeds are minimal** — 3 keywords per niche
- **Per-niche source files duplicate entries** that should be in the shared registry

### 1.2 Duplicates Already Exist at Current Scale

Querying the current `content_pool` (362 entries from 73 sources) reveals **4 proven duplicate pairs**:

| Title | Source A | Source B |
|-------|---------|---------|
| "Animated GIF with ComfyUI?" | r/StableDiffusion | r/ComfyUI |
| "daVinci-MagiHuman: new opensource video model" | r/StableDiffusion | r/ComfyUI |
| "LTX 2.3 workflow for 8GB VRAM?" | r/ComfyUI | r/StableDiffusion |
| "Peter Molyneux's Masters of Albion" | Steam News | PC Gamer |

These are **exact-title duplicates with different URLs** — the URL-hash dedup (Layer 1) can't catch them. Reddit cross-posts and RSS syndication create different URLs for identical content.

Current duplicate rate: **1.1%** (4/362). At 3,300 projected entries with 843 sources, the rate will increase to an estimated **5-8%** (165-264 duplicates per run) because:
- Reddit cross-posts are ubiquitous (same post in r/gaming + r/PS5 + r/GodOfWar)
- Wire services (AP, Reuters) syndicate to ESPN, BBC, Bleacher Report simultaneously
- YouTube channels repost trending videos across related channels
- RSS feeds run the same press release across multiple outlets

### 1.3 Dual Pipeline Paths Create Dedup Blind Spots

Two independent code paths create blueprints:

```
Path A (Shared Pool):
  SharedIngestion → content_pool → _read_from_content_pool() → per-niche pipeline → push_to_backlog

Path B (Direct Fetch):
  Per-niche FetchTrendingVideos (YouTube API) → per-niche pipeline → push_to_backlog
```

Both converge at `push_to_backlog`, but Path A uses Reddit/RSS URLs as `source_url` while Path B uses YouTube URLs. Same underlying story, different URLs → URL dedup misses it. Only `video_id` dedup catches it IF both paths have a video_id — but Reddit entries currently have NO video_id extracted.

### 1.4 Per-Niche Sources Are Fragmented

Each channel has its own `sources.yaml` with RSS/Reddit/YouTube entries that overlap with `shared_sources.yaml`. Maintaining 6 separate files leads to:
- Missed sources (added to one file but not the other)
- Inconsistent affinity tagging
- No cross-niche discovery (a sports clip in a gaming subreddit stays invisible to ClutchWire)

---

## 2. Scope

**In scope:**
- Expand `shared_sources.yaml` to 843 sources (191 YouTube, 229 Reddit subs, 168 RSS, 5 categories, 50 trends seeds)
- Migrate per-niche RSS/Reddit/YouTube entries into shared registry
- 11-layer dedup fortress preventing duplicate posts at 10x volume
- Classifier hardening for higher volume and precision
- Parallel feed fetching for performance at scale
- Feed health monitoring with auto-disable
- Dedup quality metrics

**Out of scope:**
- Non-English sources
- Paid API sources (all additions are zero-cost RSS/Atom)
- New source connector types (Playwright, API connectors)
- Content pool schema changes (no new columns)
- Pipeline stage changes
- Video rendering, publishing, learning loop changes

---

## 3. Architecture

### 3.1 Source Expansion: 843 Sources Across 3 Tiers

**Tier 1 (Core):** Official channels, top news sources, biggest subreddits — always enabled.
**Tier 2 (Extended):** Game/team/franchise-specific subs, niche RSS feeds — enabled by default.
**Tier 3 (Deep Tail):** Individual team/player subs, small community feeds — disabled by default, opt-in.

#### 3.1.1 YouTube Channels (191 total)

| Niche | Existing | New | Total | Key New Additions |
|-------|----------|-----|-------|-------------------|
| AI Creators | 3 | 18 | 21 | Matt Wolfe, TheAIGRID, Yannic Kilcher, AI Explained, MKBHD, Corridor Crew, sentdex, Linus Tech Tips, 3Blue1Brown, Lex Fridman, ColdFusion |
| Gaming | 5 | 20 | 25 | Riot Games, Rockstar, Valve, Skill Up, ACG, gameranx, jackfrags, AngryJoeShow, Markiplier, PewDiePie, MrBeast Gaming, Asmongold |
| Sports | 5 | 20 | 25 | MLB, NHL, UFC, WWE, Formula 1, LaLiga, Bundesliga, ICC Cricket, Tennis TV, Secret Base, Jomboy Media, Pat McAfee |
| Movies | 4 | 18 | 22 | Movieclips, New Rockstars, CinemaSins, ScreenCrush, Marvel Entertainment, Netflix, Warner Bros., A24, Heavy Spoilers, Nerdwriter1 |
| Anime | 4 | 15+14 | 33 | Muse Asia, Trash Taste, The Anime Man, Nux Taku, Super Eyepatch Wolf, Foxen Anime, Toei Animation, Aniplex USA, Viz Media |

All YouTube channels are consumed as zero-quota RSS feeds: `https://www.youtube.com/feeds/videos.xml?channel_id=UCXXXXX`

Channel IDs verified via VidIQ, NoxInfluencer, SPEAKRJ, and Social Blade analytics platforms.

Cross-niche channels tagged with multiple affinities:
- Corridor Crew: `[ai_creators, movies]`
- Digital Foundry: `[ai_creators, gaming]`
- WatchMojo: `[gaming, movies]`
- Dude Perfect: `[gaming, sports]`
- IShowSpeed: `[gaming, sports]`

#### 3.1.2 Reddit Subreddits (229 active + ~200 disabled Tier 3)

Each subreddit produces 2 feeds: `r/SUBREDDIT/new/.rss` + `r/SUBREDDIT/top/.rss?t=week`

**AI Creators — 45 subreddits (Tier 1+2):**

Core AI (T1): ChatGPT, OpenAI, LocalLLaMA, MachineLearning, artificial, singularity, ClaudeAI, perplexity_ai, Gemini, agi

AI Art/Video (T1): aivideo, AiVideos, kling, midjourney, StableDiffusion, runwayml, sora, ComfyUI, fluxai, dalle, deforum, LeonardoAi, aiArt, waifu_diffusion

AI Audio/Music (T2): ElevenLabs, HeyGen, AIMusic, suno, udio

AI Tools/Dev (T2): Cursor, Oobabooga, AIToolsTech, PromptEngineering, LLMDevs, ollama, Bard, CopilotAI, GPT4

Tech General (T2): Futurology, technology, robotics, SelfDrivingCars, programming, datascience

**Gaming — 55 subreddits (Tier 1+2), ~100+ Tier 3:**

General (T1): gaming, pcgaming, Games, gamingclips, LivestreamFail, esports, GamePhysics
Platforms (T1): PS5, XboxSeriesX, NintendoSwitch, Steam, SteamDeck
Major Franchises (T2): VALORANT, leagueoflegends, FortNiteBR, apexlegends, Overwatch, CallOfDuty, halo, Eldenring, BaldursGate3, cs2, GTA, Minecraft, Helldivers, Palworld, DestinyTheGame, cyberpunkgame, MonsterHunter, Starfield, pokemon, RocketLeague, Genshin_Impact, darksouls, NoMansSkyTheGame, Warframe, PathOfExile
Esports (T2): CompetitiveOverwatch, ValorantCompetitive, FortniteCompetitive
VR (T2): VRGaming, OculusQuest
Tier 3 (disabled): ~100 individual game subs (diablo4, ffxiv, wow, deadbydaylight, Tekken, MortalKombat, residentevil, etc.)

**Sports — 50 subreddits (Tier 1+2), ~100+ Tier 3:**

General (T1): sports, nbahighlights, footballhighlights, fightporn
Major Leagues (T1): nba, nfl, soccer, baseball, hockey, MMA, ufc, boxing, Cricket, SquaredCircle, formula1
Soccer Leagues (T2): PremierLeague, LaLiga, Bundesliga, seriea, championsleague, MLS, Ligue1
College (T2): CFB, CollegeBasketball
Other Sports (T2): tennis, golf, rugbyunion, olympics, cycling, peloton, Swimming, trackandfield
Motorsport (T2): motorsports, NASCAR, INDYCAR, formuladank, motogp
Combat (T2): bjj, Kickboxing, MuayThai
Women's (T2): WNBA, WomensSoccer, NWSL
Tier 3 (disabled): ~100+ individual team subs (lakers, warriors, reddevils, Gunners, chelseafc, realmadrid, etc.)

**Movies — 40 subreddits (Tier 1+2), ~50+ Tier 3:**

General (T1): movies, MovieDetails, boxoffice, trailers, entertainment, TrueFilm, FanTheories
Franchises (T1): marvelstudios, DC_Cinematic, StarWars, comicbookmovies, MCUTheories
Genre (T2): horror, scifi, Documentaries, criterion
Streaming (T2): netflix, DisneyPlus, HBOMAX, amazonprime, appletv, Hulu, streaming
Studios (T2): A24, StudioGhibli, Pixar
TV Shows (T2): television, HouseOfTheDragon, Severance, TheBoys, Invincible, arcane, StrangerThings, squidgame
Tier 3 (disabled): ~50+ individual show/franchise subs (dune, JamesBond, lotr, TheMandalorian, etc.)

**Anime — 40 subreddits (Tier 1+2), ~60+ Tier 3:**

General (T1): anime, manga, animemes, animeclips, AnimeSakuga, anime_irl, goodanimemes
Major Franchises (T1): OnePiece, Naruto, DragonBallSuper, JuJutsuKaisen, ChainsawMan, KimetsuNoYaiba, bleach, HunterXHunter, OnePunchMan, SoloLeveling, SpyxFamily, Dandadan, Frieren, BlueLock, OshiNoKo
Franchises (T2): Berserk, ShingekiNoKyojin, BokuNoHeroAcademia, VinlandSaga, MobPsycho100, Haikyuu, CodeGeass, Gundam, evangelion, cowboybebop
Media (T2): AnimeART, AnimeFigures, MangaCollectors, LightNovels, ShitPostCrusaders
Tier 3 (disabled): ~60+ individual anime subs (TokyoGhoul, deathnote, BlackClover, etc.)

#### 3.1.3 RSS Feeds (168 total)

**AI Creators (38):**

Existing (3): DeepLearning.AI, Last Week in AI, MIT Tech Review AI

Major tech news (7): TechCrunch AI, The Verge AI, Ars Technica AI, VentureBeat AI, Wired AI, IEEE Spectrum AI, InfoQ AI/ML

Newsletters (7): The Rundown AI, Ben's Bites, Import AI, The Neuron, AI Breakfast, Ahead of AI, The Gradient

Company blogs (8): OpenAI Blog, Google AI Blog, DeepMind Blog, Hugging Face Blog, Meta AI Blog, Microsoft Research, NVIDIA Blog, Anthropic News

Research (4): Stanford HAI, Papers With Code, Alignment Forum, Distill.pub

Developer (4): KDnuggets, Analytics Vidhya, Machine Learning Mastery, Towards AI

Product/Industry (3): Product Hunt AI, Hacker News AI (100+ pts), arXiv CS.AI

Academic (2): arXiv CS.AI (`http://export.arxiv.org/rss/cs.AI`), arXiv CS.LG (`http://export.arxiv.org/rss/cs.LG`)

**Gaming (32):**

Existing (7): IGN, Kotaku, PC Gamer, Eurogamer, GameSpot, Rock Paper Shotgun, Steam News

Major sites (8): Polygon, The Verge Gaming, Destructoid, GamesRadar, DualShockers, VG247, Siliconera, Game Informer

Platform-specific (6): Nintendo Life, Nintendo Everything, Push Square, Pure Xbox, Xbox Wire, PlayStation Blog

Esports (3): Dot Esports, Dexerto, HLTV

Indie/Mobile/Retro (4): IndieDB, TouchArcade, Pocket Gamer, Time Extension

MMO/Japanese (3): MMORPG.com, Massively OP, Gematsu

Analysis (1): Game Developer (Gamasutra)

**Sports (35):**

Existing (5): ESPN, The Athletic, BBC Sport, Sky Sports, Bleacher Report

Major news (8): CBS Sports, Yahoo Sports, Sports Illustrated, NBC Sports, Sporting News, The Guardian Sport, The Ringer, SB Nation

Soccer (4): FourFourTwo, Football365, 90min, ESPN FC

Cricket (2): ESPNcricinfo, Cricbuzz

Motorsport (4): Motorsport.com, Autosport, The Race, F1 Official

Combat (3): MMA Fighting, Sherdog, Boxing Scene

Other (5): Cycling News, VeloNews, Golf Digest, SwimSwam, Deadspin

Misc (4): Fox Sports, NBC Sports, Sporting News, SB Nation

**Movies (33):**

Existing (7): Deadline, Variety, Hollywood Reporter, IGN Movies, Screen Rant, Collider, IndieWire

Major sites (8): /Film, AV Club, Vulture, Empire, Den of Geek, CinemaBlend, The Wrap, Roger Ebert

Streaming (2): Decider, What's on Netflix

Horror (2): Bloody Disgusting, Dread Central

Sci-Fi/Genre (2): io9, TOR.com

Comics (3): CBR, Newsarama, ComicBook.com

Awards (2): Awards Daily, Gold Derby

Prestige (2): BFI, Letterboxd Journal

TV (2): TVLine, TV Insider

Industry (1): Variety Streaming

**Anime (22):**

Existing (7): ANN (×2), Crunchyroll, MAL, Kotaku Anime, LiveChart, Anime Corner

New (15): Otaku USA, Siliconera, Anime UK News, Anime Trending, Sakuga Blog, CBR Anime, ComicBook.com Anime, ANN Reviews, Japan Times Culture, J-Novel Club Blog, Anime Corner Rankings, LiveChart All, Viz Media Blog, Manga Plus, Anime Trending

#### 3.1.4 Google Trends Seeds (50 total, 10 per niche)

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

### 3.2 YAML Structure

```yaml
# Each source has:
#   name: Human-readable label
#   url: Feed URL
#   affinity: [niche1, niche2] — prior weights for classifier
#   tier: 1|2|3 — for metrics/logging
#   enabled: true|false — Tier 3 defaults to false

youtube_channels:
  - name: "Matt Wolfe"
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UChpleBmo18P08aKCIgti38g"
    affinity: [ai_creators]
    tier: 1

reddit_feeds:
  - name: "r/gaming new"
    url: "https://www.reddit.com/r/gaming/new/.rss"
    affinity: [gaming]
    tier: 1
  - name: "r/gaming top"
    url: "https://www.reddit.com/r/gaming/top/.rss?t=week"
    affinity: [gaming]
    tier: 1

  # Tier 3 — disabled by default
  - name: "r/lakers new"
    url: "https://www.reddit.com/r/lakers/new/.rss"
    affinity: [sports]
    tier: 3
    enabled: false

rss_feeds:
  - name: "TechCrunch AI"
    url: "https://techcrunch.com/category/artificial-intelligence/feed/"
    affinity: [ai_creators]
    tier: 1
```

Pipeline reads `enabled` (defaults `true` if absent) and skips disabled sources.

### 3.3 Per-Niche Source Migration

Per-niche `sources.yaml` files will have their RSS/Reddit/YouTube channel entries REMOVED and migrated into `shared_sources.yaml`. What STAYS in each per-niche file:

| Per-Niche File | Stays | Removed |
|----------------|-------|---------|
| `BlackboxBrief/config/sources.yaml` | BB-specific connectors (X, Civitai, HF, TikTok, Threads, IG Playwright), content_filter, google_trends | youtube_channels, sources (Reddit feeds), RSS feeds |
| `CriticalRush/niches/gaming/config/sources.yaml` | clip_sourcer, steam, twitch, igdb, source_filters, content_filter | youtube_channels, rss_feeds, reddit subreddits |
| `ClutchWire/config/sources.yaml` | espn_api, content_filter | tier_1/2/3 RSS sources, reddit subreddits |
| `SpliceReel/config/sources.yaml` | tmdb, omdb, content_filter | tier_2 RSS sources |
| `FrameDrift/config/sources.yaml` | content_filter, google_trends | tier_1/2/3 RSS/YouTube sources |

### 3.4 Volume Projection

Current averages per 48h window from the live content_pool:
- YouTube channels: **1.9 entries/channel** (from limit of 10 per channel RSS)
- Reddit feeds: **5.3 entries/feed** (from limit of 15 per feed)
- RSS feeds: **8.2 entries/feed** (from limit of 10 per feed)

Projected volume at 843 sources (634 active):

| Source Type | Active Feeds | Avg/Feed | Raw Items | After URL Dedup |
|-------------|-------------|----------|-----------|-----------------|
| YouTube channels (191) | 191 | 1.9 | 363 | ~350 |
| Reddit feeds (229 subs × 2) | 458 | 5.3 | 2,427 | ~1,800 |
| RSS feeds (168) | 168 | 8.2 | 1,378 | ~1,100 |
| YouTube trending (5 cats) | 5 | 10 | 50 | ~50 |
| **Total** | **822** | — | **4,218** | **~3,300** |

**Current: 362 entries/run. Projected: ~3,300 entries/run. 9.1x increase.**

Without title-level dedup, estimated duplicates: **165-264 per run (5-8%).**

---

## 4. Dedup Fortress: 11-Layer Defense

### 4.1 Duplicate Scenarios (Exhaustive)

I identified **12 specific scenarios** where the same content enters the system via different paths:

**S1: Same story, different news outlets (RSS syndication)**
- "Lakers beat Celtics 112-105" arrives from ESPN, BBC Sport, Bleacher Report, Yahoo Sports simultaneously
- Different URLs → different `content_hash` → passes URL dedup
- **Caught by: Layer 0 (title similarity)**

**S2: Reddit cross-posts (same content, different subreddits)**
- User cross-posts to r/StableDiffusion AND r/ComfyUI (proven — 3 existing duplicates)
- Different Reddit URLs → passes URL dedup
- Identical titles → **Caught by: Layer 0 (exact title match)**

**S3: Reddit post linking to YouTube video**
- r/gaming post `reddit.com/r/gaming/comments/abc123` embeds YouTube link `youtube.com/watch?v=XYZ`
- YouTube channel RSS also returns `youtube.com/watch?v=XYZ`
- Different URLs → passes URL dedup. Reddit entry has NO video_id extracted.
- **Current: NOT caught. Fix: Layer 2.5 (YouTube ID extraction from Reddit)**

**S4: Same YouTube video from trending API + channel RSS**
- YouTube trending returns video XYZ. Same video appears in channel RSS.
- Same URL `youtube.com/watch?v=XYZ` → same content_hash
- **Caught by: Layer 1 (URL hash)**

**S5: Content pool path + direct pipeline path**
- SharedIngestion routes a story to gaming via content_pool
- CriticalRush's own FetchTrendingVideos also finds the same video via YouTube API
- Both converge at push_to_backlog
- **Caught by: Layer 5 (video_id dedup) IF both have video_id. Layer 5.5 (pool→pipeline cross-dedup) for title-based catch.**

**S6: Recurring content across days**
- Monday: ESPN publishes "NBA Playoffs Preview". Pool entry created, URL in content_memory.
- Wednesday: Bleacher Report publishes similar article with DIFFERENT URL.
- content_pool entry expired (48h), but content_memory persists URL hash.
- Different URL → content_memory misses it.
- **Caught by: Layer 4.5 (title similarity vs content_memory titles)**

**S7: Same story routed to multiple niches**
- "Messi hat-trick" routes to `[sports, movies]` if it mentions streaming
- Each niche pipeline claims and processes independently
- **This is CORRECT behavior** — legitimate multi-niche content. Max 2 niches per item.
- Video_id dedup (Layer 5) prevents the same VIDEO from creating blueprints in two niches.
- **No fix needed.**

**S8: Near-duplicate hooks from same story processed twice**
- LLM generates "Messi's hat-trick stuns the crowd" and "Messi's hat-trick shocks the stadium"
- **Caught by: Layer 6 (hook text Jaccard > 0.6)**

**S9: Reddit new + top returning same post**
- r/gaming/new and r/gaming/top both return the same viral post
- Same Reddit URL → same content_hash
- **Caught by: Layer 1 (URL hash)**

**S10: Wire service articles with slight title variations**
- AP: "OpenAI Announces GPT-5 Model" → ESPN, BBC, etc. all run slightly reworded versions
- "OpenAI Announces GPT-5 Model" vs "OpenAI Unveils GPT-5" vs "GPT-5 Launched by OpenAI"
- Different URLs AND different titles (but same story)
- **Caught by: Layer 0 Pass 3 (TF-IDF cosine similarity ≥ 0.75)**

**S11: YouTube re-uploads across channels**
- Same clip uploaded by the official channel AND a clip compilation channel
- Different video_ids, different URLs, different channel names
- **Partially caught by: Layer 0 (title similarity) if titles are similar. Layer 6 (hook dedup) as last resort.**
- **Remaining gap: visually identical content with different titles. Accepted risk — would require video fingerprinting (out of scope).**

**S12: Event-driven floods (E3, Oscars, game reveals)**
- 50+ sources all cover "GTA 6 Trailer Drops" simultaneously
- Title similarity catches most, but slight variations may slip through
- **Caught by: Layer 0 (title + TF-IDF), Layer 6 (hook dedup). Risk: 1-2 may slip through during major events.**
- **Mitigation: DailyCapEnforcer limits to 1 post/niche/day regardless.**

**S13: RSS aggregator re-titling (Google News, Flipboard)**
- Original: "OpenAI Launches GPT-5" from openai.com
- Aggregator: "GPT-5 is Here: What You Need to Know" from news.google.com
- Different URL AND different title → evades Layer 0 title similarity AND Layer 1 URL hash
- **Partially caught by: Layer 0 Pass 3 (TF-IDF cosine) if summary has enough shared vocabulary.**
- **Additional mitigation: Normalize RSS `<link>` elements before hashing — many aggregators link back to the original source URL. Add canonical URL extraction to `_content_hash()` using `feedparser` entry's `link` field (not the aggregator page URL).**

**S14: Stale cross-day duplicates beyond 48h pool window**
- Monday: "NBA Playoffs Preview" from ESPN. Pool entry created, URL saved in content_memory.
- Thursday: "NBA Playoffs Preview Update" from Bleacher Report. Pool entry expired (48h), but content_memory persists URL hash.
- Different URL → content_memory misses it. Pool titles expired → Layer 0 misses it.
- **Caught by: Layer 4.5 (title similarity vs content_memory titles) IF content_memory stores titles alongside URL hashes.**
- **Requirement: content_memory `create()` call in push_to_backlog must include the `title` field (currently it does — line 568).**

### 4.2 Layer-by-Layer Specification

#### Layer 0 (NEW): Title Similarity Dedup at Shared Ingestion

**Where:** `shared_ingestion.py`, after fetching all feeds, before `_classify_all()`

**What:** Run 3-pass dedup on the batch AND against existing content_pool titles from the last 48h.

**Implementation:**
```python
def _deduplicate_batch(self) -> None:
    """Remove near-duplicate entries by title similarity."""
    if len(self._entries) < 2:
        return

    # Load existing titles from content_pool (last 48h, max 3000)
    existing_titles = self._load_recent_pool_titles(limit=3000)

    # Build DedupEngine
    from genlab_core.intelligence.dedup_engine import DedupEngine
    engine = DedupEngine(
        jaccard_threshold=0.70,
        tfidf_threshold=0.75,
        url_field="source_url",
        text_field="title",
    )

    # Create items list: existing pool titles (anchors) + current batch
    anchor_items = [{"title": t, "source_url": f"__anchor_{i}"} for i, t in enumerate(existing_titles)]
    batch_items = [{"title": e.title, "source_url": e.source_url} for e in self._entries]
    all_items = anchor_items + batch_items

    result = engine.run(all_items)

    # Keep only batch items that survived dedup
    surviving_urls = {item["source_url"] for item in result.unique if not item["source_url"].startswith("__anchor_")}
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
    except Exception:
        return []
```

**Performance:** Jaccard Pass 2 is O(n²). With 3300 batch + 3000 anchors = 6300 items, worst case = ~19.8M comparisons × ~10μs = ~198 seconds. However, anchors are not compared against each other (they're pre-deduped), reducing to ~3300 × 3000 = ~9.9M comparisons = ~99 seconds. Pass 3 (TF-IDF cosine) only runs on items surviving Pass 2, further reducing n. Total Layer 0 estimate: **2-3 minutes**. Acceptable for a daily pipeline that currently runs at 05:00 UTC with no time pressure.

If performance becomes an issue, MinHash/LSH can replace brute-force Jaccard for approximate nearest neighbor with O(n) complexity, but this optimization is deferred until proven necessary.

#### Layer 1: URL Hash (content_pool UNIQUE constraint)

**Where:** `shared_ingestion.py:445-466`, content_pool `uq_content_pool_hash` UNIQUE index

**How:** `sha256(source_url)[:32]` → `ON CONFLICT (content_hash) DO UPDATE`

**Fix needed:** The ON CONFLICT UPDATE currently OVERWRITES `routed_niches`:
```sql
-- CURRENT (broken):
ON CONFLICT (content_hash) DO UPDATE SET
    routed_niches = EXCLUDED.routed_niches,  -- overwrites!

-- FIXED (merges):
ON CONFLICT (content_hash) DO UPDATE SET
    routed_niches = (
        SELECT ARRAY(SELECT DISTINCT unnest(
            content_pool.routed_niches || EXCLUDED.routed_niches
        ))
    ),
```

This ensures that if Source A routes to `[gaming]` and Source B (same URL) routes to `[gaming, movies]`, the final result is `[gaming, movies]` regardless of insertion order.

#### Layer 2: In-Memory Batch Dedup

**Where:** `shared_ingestion.py:120-127`, `_seen_hashes` set

**Status:** Working. No changes needed.

#### Layer 2.5 (NEW): YouTube Video ID Extraction from Reddit Posts

**Where:** `shared_ingestion.py:_fetch_reddit_feeds()`, after creating `pool_entry`

**What:** Parse YouTube video links from Reddit post summary/content and extract video_id.

**Implementation:**
```python
import re
_YT_ID_RE = re.compile(
    r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)'
    r'([a-zA-Z0-9_-]{11})'
)

# After creating pool_entry in _fetch_reddit_feeds:
for text_field in [
    entry.get("summary", ""),
    # feedparser puts HTML content in content[0].value
    (entry.get("content", [{}])[0].get("value", "") if entry.get("content") else ""),
]:
    match = _YT_ID_RE.search(text_field)
    if match:
        pool_entry.video_id = match.group(1)
        pool_entry.video_url = f"https://www.youtube.com/watch?v={match.group(1)}"
        break
```

This closes Scenario S3. Reddit posts linking to YouTube videos will now carry `video_id`, enabling Layer 5 to catch duplicates.

#### Layer 3: Content Memory (per-niche URL dedup)

**Where:** `push_to_backlog.py:300-312`

**Fix:** Increase lookback from 500 to 2000 records.

```python
# Change line ~302:
cm_records = cm_proxy.all(
    formula=f"{{niche_id}}='{niche_id}'",
    max_records=2000,  # was 500
)
```

#### Layer 4: Story URL Hash (per-niche)

**Where:** `push_to_backlog.py:282-297`

**Fix:** Increase lookback from 500 to 2000 records.

```python
# Change line ~262:
existing_stories = client.stories.all(
    formula=f"{{niche_id}}='{niche_id}'",
    max_records=2000,  # was 500
)
```

#### Layer 4.5 (NEW): Title Similarity vs Content Memory

**Where:** `push_to_backlog.py`, after URL dedup check (line ~325), before blueprint creation

**What:** Check if a story with similar title already exists in content_memory or recent stories.

**Implementation:**
```python
# Load titles from recent stories + content_memory for title-level dedup
existing_titles: set[str] = set()
for s in existing_stories:
    t = (s.get("fields", s).get("title") or "").strip().lower()
    if t and len(t) > 10:
        existing_titles.add(t)
for rec in cm_records:
    t = (rec.get("fields", rec).get("title") or "").strip().lower()
    if t and len(t) > 10:
        existing_titles.add(t)

# In the story processing loop, after URL dedup:
title_lower = title.lower().strip()
for existing in existing_titles:
    # Word-level Jaccard (same as hook dedup)
    title_words = set(title_lower.split())
    existing_words = set(existing.split())
    if len(title_words) > 3 and len(existing_words) > 3:
        intersection = len(title_words & existing_words)
        union = len(title_words | existing_words)
        if union > 0 and intersection / union > 0.65:
            logger.info("[PUSH] Title near-dupe with history: '%s' ≈ '%s'", title[:40], existing[:40])
            skip = True
            break
```

#### Layer 5: Video ID Dedup (per-niche)

**Where:** `push_to_backlog.py:410-425`

**Status:** Working. No changes needed. Queries `video_id` column on blueprints table with `idx_bp_video_niche` index.

#### Layer 5.5 (NEW): Pool→Pipeline Cross-Dedup

**Where:** `push_to_backlog.py`, during setup phase (before story loop)

**What:** Load titles from content_pool entries recently claimed by this niche, so that stories arriving via the direct pipeline path are deduplicated against pool-sourced stories.

**Implementation:**
```python
# After loading existing_stories and content_memory, also load pool claims
try:
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        import psycopg
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT title FROM content_pool WHERE claimed_by = %s AND claimed_at > NOW() - INTERVAL '48 hours'",
                    (niche_id,),
                )
                for row in cur.fetchall():
                    if row[0]:
                        existing_titles.add(row[0].strip().lower())
        logger.info("[PUSH] Loaded %d pool-claimed titles for cross-dedup", len(existing_titles))
except Exception:
    pass  # non-critical
```

#### Layer 6: Hook Text Dedup (per-niche)

**Where:** `push_to_backlog.py:436-463`

**Fix:** Increase lookback from 500 to 2000 hooks.

```python
# Change line ~261:
recent_bps = client.blueprints.all(
    formula=f"{{niche_id}}='{niche_id}'",
    max_records=2000,  # was 500
)
```

#### Layer 7: DedupEngine 3-Pass (within pipeline batch)

**Where:** `dedup_engine.py`, used by `qc_gates.py` and `base_content_research.py`

**Status:** Working. No changes needed.

### 4.3 Database Changes

#### 4.3.1 New Video ID Index

```sql
-- File: genlab-core/migrations/add_content_pool_video_idx.sql
CREATE INDEX IF NOT EXISTS idx_cp_video_id
    ON content_pool(video_id)
    WHERE video_id IS NOT NULL;
```

Enables efficient video_id lookups for Layer 2.5 cross-entry merge.

#### 4.3.2 Fix Upsert routed_niches Merge

```sql
-- In shared_ingestion.py upsert_sql, change:
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

#### 4.3.3 Fix Race Condition in Pool Claiming

```sql
-- In trending_video_fetcher.py _read_from_content_pool, change SELECT to:
SELECT * FROM content_pool
WHERE %s = ANY(routed_niches)
  AND status = 'available'
ORDER BY view_velocity DESC NULLS LAST, fetched_at DESC
LIMIT 20
FOR UPDATE SKIP LOCKED
```

This prevents two concurrent niche pipelines from claiming the same rows.

---

## 5. Classifier Hardening

### 5.1 Raise Normalizer Cap (3→5)

**File:** `niche_classifier.py:154`

```python
# Current:
normalizer = min(max(len(profile.positive_keywords) * 0.15, 1), 3)
# Fixed:
normalizer = min(max(len(profile.positive_keywords) * 0.15, 1), 5)
```

With 100+ positive keywords per niche, 3 keyword hits currently gives max keyword score (0.6). Raising to 5 means weak matches (1-2 hits) get proportionally lower scores.

### 5.2 Minimum 2 Keyword Hits for Keyword Score

**File:** `niche_classifier.py:153-156`

```python
hits = sum(1 for p in profile._positive_patterns if p.search(text_lower))
# Suppress keyword score for weak matches, but DON'T return 0.0 —
# YouTube category match and source affinity can still contribute.
if hits < 2:
    keyword_score = 0.0  # single keyword is never enough for keyword component
else:
    normalizer = min(max(len(profile.positive_keywords) * 0.15, 1), 5)
    keyword_score = min(hits / normalizer, 1.0) * 0.6

score = keyword_score
```

**Why not `return 0.0`:** A YouTube video from category 20 (Gaming) with a generic title like "New update live now" has zero keyword hits but should still route via the category signal (+0.15). Returning 0.0 early would break YouTube category routing. Instead, we zero only the keyword component and let category/affinity bonuses still contribute.

**Worked examples with proposed changes:**
- "Cooking with AI" from ai_creators source: hits=1 → keyword=0.0, affinity requires hits>0 → +0.0, no category → total=0.0. **Rejected** (threshold 0.30). Correct.
- "New update live now" from YT category 20: hits=0 → keyword=0.0, no affinity, category=gaming → +0.15, total=0.15. **Rejected** by gaming threshold (0.20). This is acceptable — a title with zero niche keywords is genuinely ambiguous.
- "Fortnite Season 5 trailer drops" from YT category 20: hits=2 (fortnite, trailer) → keyword=(2/5)*0.6=0.24, category=+0.15, total=0.39. **Accepted** by gaming threshold (0.20). Correct.
- "Lakers beat Celtics in overtime thriller" from ESPN [sports]: hits=3 (lakers, celtics, overtime) → keyword=(3/5)*0.6=0.36, affinity (hits>0)=+0.15, total=0.51. **Accepted** by sports threshold (0.20). Correct.
- "One Piece episode 1200 reaction" from r/anime [anime]: hits=2 (one piece, episode) → keyword=(2/5)*0.6=0.24, affinity (hits>0)=+0.15, total=0.39. **Accepted** by anime threshold (0.40)? No — 0.39 < 0.40. **Rejected.** This is too aggressive. Consider lowering anime threshold to 0.35.


### 5.3 Affinity Boost Requires Keyword Match

**File:** `niche_classifier.py:159-161`

```python
# Current:
if source_affinity and profile.niche_id in source_affinity:
    score += 0.2
# Fixed:
if source_affinity and profile.niche_id in source_affinity and hits > 0:
    score += 0.15  # reduced from 0.2, requires keyword match
```

Prevents WatchMojo `[gaming, movies]` from routing an anime video to gaming when zero gaming keywords match.

### 5.4 Multi-Label Cap Already at 2

**File:** `niche_classifier.py:206` — `max_niches: int = 2`

Already correct. No change needed.

---

## 6. Performance at Scale

### 6.1 Parallel Feed Fetching

**Current:** Sequential fetching. 822 feeds × worst-case 15s timeout = 3.4 hours.

**Fix:** `ThreadPoolExecutor` with domain-level rate limiting.

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time as _time

class _DomainRateLimiter:
    """Per-domain rate limiter to avoid 429s."""
    def __init__(self):
        self._locks: dict[str, threading.Lock] = {}
        self._last_request: dict[str, float] = {}
        self._delays = {"reddit.com": 2.0}  # Reddit: max 1 req/2s
        self._default_delay = 0.1  # 100ms between requests to same domain

    def wait(self, url: str) -> None:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        if domain not in self._locks:
            self._locks[domain] = threading.Lock()
        with self._locks[domain]:
            delay = self._delays.get(domain, self._default_delay)
            last = self._last_request.get(domain, 0)
            wait_time = max(0, delay - (_time.time() - last))
            if wait_time > 0:
                _time.sleep(wait_time)
            self._last_request[domain] = _time.time()
```

**Realistic timing estimate:**
- YouTube channels (191 feeds): ~20 seconds with 15 workers (no rate limit)
- Reddit feeds (458 feeds): ~15 minutes with domain lock at 1 req/2s (Reddit rate-limits aggressively). **Optimization:** Use multi-reddit URLs to batch subreddits: `reddit.com/r/gaming+pcgaming+Games/new/.rss` returns combined feed from up to 100 subreddits in one request. 229 subs ÷ 50 per multi-reddit = ~5 requests for new + 5 for top = 10 Reddit requests × 2s = 20 seconds.
- RSS feeds (168 feeds): ~30 seconds with 15 workers
- Title dedup (Layer 0): ~2-3 minutes
- Classification + DB write: ~30 seconds

**Target: < 5 minutes total** (down from potential 3.4 hours sequential).

### 6.2 Feed Health Tracking

Track consecutive failures per feed. After 5 failures, auto-disable for 24h.

```python
# Persist to genlab-core/.tmp/cache/feed_health.json (NOT in config/ — runtime state)
{
    "https://www.example.com/feed": {
        "consecutive_failures": 3,
        "last_failure": "2026-03-25T10:00:00Z",
        "last_success": "2026-03-24T10:00:00Z",
        "disabled_until": null
    }
}
```

### 6.3 Dedup Quality Metrics

Add to the ingestion report:

```
DEDUP BREAKDOWN:
  URL hash (Layer 1)      :   45
  Title similarity (L0)   :   23
  Video ID merge (L2.5)   :    8
  Total prevented         :   76
  Unique stories (est.)   : 1842
  Multi-source stories    :  312  (same story from 2+ outlets)
```

---

## 7. File Changes Manifest

### 7.1 Core Changes

| File | Change | Lines Est. |
|------|--------|-----------|
| `genlab-core/config/shared_sources.yaml` | Expand to ~640 sources (~810 feed URLs) with tier/affinity/enabled tags. De-duplicate cross-niche channels (WatchMojo, Corridor Crew, Dude Perfect, LoL Esports listed ONCE each with multi-affinity, not duplicated) | +2500 |
| `genlab-core/migrations/add_content_pool_video_idx.sql` | New: `idx_cp_video_id` index | +3 |
| `genlab-core/src/genlab_core/pipeline/shared_ingestion.py` | Title dedup (Layer 0), YT ID from Reddit (Layer 2.5), parallel fetching with ThreadPoolExecutor, domain rate limiting, feed health tracking, routed_niches array merge in upsert, enabled/tier filtering, dedup metrics in report | +200 |
| `genlab-core/src/genlab_core/media/trending_video_fetcher.py` | Fix `_read_from_content_pool`: change SELECT to `FOR UPDATE SKIP LOCKED` to prevent race condition when concurrent niche pipelines claim the same rows | ~5 |
| `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py` | Lookback 500→2000 (3 places: content_memory, stories, hooks), title sim vs content_memory (Layer 4.5), pool→pipeline cross-dedup (Layer 5.5) | +60 |
| `genlab-core/src/genlab_core/intelligence/niche_classifier.py` | Normalizer cap 3→5, min-2-hits gate (keyword component only, not full return), affinity requires keyword match, boost 0.2→0.15 | ~15 |
| `genlab-core/src/genlab_core/intelligence/dedup_engine.py` | Add `deduplicate_against_existing()` convenience method for pool-level title dedup | +30 |
| `genlab-core/src/genlab_core/strategies/base_content_research.py` | Verify DedupEngine import still works after new method addition (no interface change expected, but verify) | ~0 |

### 7.2 Per-Niche Source Migration

| File | Change | Lines Est. |
|------|--------|-----------|
| `BlackboxBrief/config/sources.yaml` | Remove youtube_channels, reddit feeds, RSS feeds (moved to shared). Keep: BB-specific connectors (X, Civitai, HF, TikTok, Threads, IG Playwright), content_filter, google_trends | -100 |
| `CriticalRush/niches/gaming/config/sources.yaml` | Remove youtube_channels, rss_feeds, reddit subs. Keep: clip_sourcer, steam, twitch, igdb, source_filters, content_filter | -40 |
| `ClutchWire/config/sources.yaml` | Remove tier_1/2/3 RSS entries, reddit subs. Keep: espn_api, content_filter | -30 |
| `SpliceReel/config/sources.yaml` | Remove tier_2 RSS entries. Keep: tmdb, omdb, content_filter | -15 |
| `FrameDrift/config/sources.yaml` | Remove tier_1/2/3 RSS, youtube_channels. Keep: content_filter, google_trends | -40 |

### 7.3 Tests

| File | Change |
|------|--------|
| `genlab-core/tests/test_shared_ingestion.py` | Test title dedup (Layer 0), YT ID extraction from Reddit content, parallel fetching, enabled/tier filtering, feed health auto-disable, routed_niches array merge |
| `genlab-core/tests/test_niche_classifier.py` | Test normalizer change impact, min-2-hits gate (verify YouTube category still routes), affinity+keyword gate, threshold interactions for all 5 niches |
| `genlab-core/tests/test_dedup_engine.py` | Test `deduplicate_against_existing()` method, verify anchor items don't get deduped against each other |
| `genlab-core/tests/test_push_to_backlog.py` | Test lookback increase, title similarity vs content_memory, pool→pipeline cross-dedup |

### 7.4 Operational

| File | Change |
|------|--------|
| `genlab-core/.tmp/cache/feed_health.json` | Runtime state file (NOT in config/ — persists feed failure counts, auto-disable timestamps). Gitignored. |

---

## 8. What Does NOT Change

- Pipeline execution order, stage interfaces, strategies
- Content pool table schema (no new columns, only new index)
- Video rendering, FFmpeg, frame compositor
- Publishing logic, platform clients, scheduling, DailyCapEnforcer
- Learning loop, bandit arms, reward shaper, metric collector
- Engagement engine, comment processor, reply clients
- Dashboard frontend and backend
- Per-niche API integrations (ESPN, TMDB, IGDB, OMDb)
- API quota usage (zero increase — all new sources are RSS)
- Credential architecture, env vars
- LaunchAgent schedules and plists
- Content quality rules (hooks ≤60 chars, captions, etc.)

---

## 9. Quality Gates

- `shared_sources.yaml` parses without error (`yaml.safe_load`)
- All YouTube channel RSS feeds respond with valid Atom XML (spot-check 10%)
- All Reddit RSS feeds respond with valid RSS (spot-check 10%)
- Shared ingestion completes in < 5 minutes (down from potential 3.4 hours)
- Content pool duplicate rate < 1% (measured by title-prefix grouping)
- No duplicate blueprints created in push_to_backlog (verified by video_id + title checks)
- NicheClassifier routes content to correct niches (manual spot-check 20 entries)
- `npm run build` passes (if any dashboard changes — none expected)
- Per-niche pipeline tests pass
- Dedup metrics appear in ingestion report
