# CLAUDE.md — Gen Lab
# Authoritative context for every Claude Code session in this project.
# Last updated: 2026-03-17 (post-Sprint 63 — definitive audit remediation)

## MISSION — READ THIS FIRST

Gen Lab is a **video-first viral content automation platform** that tracks trends and
publishes short-form video reels to grow social media channels toward monetisation.

The end goal is a **multi-tenant SaaS** product for micro-influencers and brands.
We are currently in Phase 1: proving the system works on our own 5 channels before
opening to external subscribers.

### What we build

Short-form VIDEO REELS for Instagram, YouTube Shorts, Facebook Reels, TikTok, and X.

**NOT text posts. NOT carousels. NOT static images. NOT slideshows.**
Text posts have zero traction on short-form video platforms. If it is not a reel with
real video footage, it does not ship.

### The five channels

| Channel | Niche | Video Content |
|---|---|---|
| Blackbox Brief | AI news | Creator clips — humans demoing/explaining AI tools |
| CriticalRush | Gaming | Trending gameplay clips, esports highlights, game trailers |
| ClutchWire | Sports | Top sports moments, highlight plays, viral athletic moments |
| SpliceReel | Movies | Trending trailers, viral film clips, box office reaction clips |
| FrameDrift | Anime | Trending fight scenes, viral episode moments, anime clips |

### The video-first pipeline

**THE VIDEO IS THE STARTING POINT.** Content is written around what is already
trending on YouTube — NOT found after a text story.

```
OLD (wrong): Fetch RSS text → try to find video → fail → stuck at DRAFTED forever
NEW (correct): Find trending YouTube clip → write content around it → render → publish
```

Sources:
- CriticalRush: YouTube Data API v3, category 20 (Gaming), mostPopular chart
- ClutchWire: YouTube Data API v3, category 17 (Sports), mostPopular chart
- SpliceReel: YouTube Data API v3, categories 1 + 24 (Film/Entertainment)
- FrameDrift: YouTube keyword search (no native anime category)
- BlackboxBrief: YouTube search for AI creator/explainer content + RSS for context

Google Trends (via pytrends) enriches the YouTube keyword search in real time.
Today's trending topics feed directly into what clips we search for.

---

## ARCHITECTURE — THREE LAYERS

```
Layer 1 — genlab-core/        SHARED INFRASTRUCTURE (never changes per niche)
Layer 2 — niches/*/strategies/ PLUGGABLE STRATEGIES (abstract interfaces per niche)
Layer 3 — niches/*/config/    NICHE CONFIGURATION (pure YAML)
```

### What belongs where

**genlab-core/** — If two channels would write identical code, it lives here:
- BacklogClient (SharePoint/Graph API)
- TrendingVideoFetcher (YouTube trending clips)
- GoogleTrendsIntel (pytrends trending topics)
- VideoContentWriter (LLM content generation)
- text_sanitizer (Graph API compatibility)
- story_hook_generator (story-specific hooks)
- publish_all_platforms logic
- LLM client (Claude Haiku, GPT-Image-1)
- FFmpeg render utilities
- Engagement Engine
- Learning Loop (Thompson Sampling / BanditArms)
- Token health manager
- Metric collector
- All Pydantic schemas

**niches/CHANNEL/** — Everything niche-specific:
- config/ (niche.yaml, sources.yaml, scoring.yaml, visuals.yaml, schedule.yaml)
- strategies/ (ContentResearch, Scoring, Writing, Hook, VisualRender, PlatformAdaptation)
- pipeline/ (fetch_trending_videos.py, compose_blueprints.py, etc.)
- prompts/ (LLM prompts for this specific niche)
- tests/

### ⚠️ KNOWN ARCHITECTURE DEBT

**BlackboxBrief** (Blackbox Brief's directory) and **CriticalRush** currently house
shared infrastructure that all other channels depend on. This is wrong. Migration plan:
1. Identify all code in `BlackboxBrief/pipeline/` that is imported by other channels
2. Move to `genlab-core/src/genlab_core/`
3. Update all imports
4. BlackboxBrief directory becomes a pure niche directory (Layer 2+3 only)

Do NOT add new shared code to BlackboxBrief or CriticalRush. Always add to genlab-core.

---

## PIPELINE EXECUTION ORDER

For all non-BB channels (CriticalRush, ClutchWire, SpliceReel, FrameDrift):

```
1. fetch_trending_videos.py   ← YouTube API: find what's viral RIGHT NOW
2. score_and_filter.py        ← View velocity + Google Trends multiplier
3. compose_blueprints.py      ← Build blueprint for top-N clips
4. write_content.py           ← LLM: write hook + captions around the video
5. render_visuals.py          ← FFmpeg: render video with logo overlay
6. human_review               ← Approve at review.aspirehub.ai
7. publish_all_platforms.py   ← Publish to IG, YT, FB, X, Threads
8. metric_collector.py        ← Collect engagement after 6h/24h
9. performance_learner.py     ← Update BanditArms for next run
```

For BlackboxBrief (AI news):
- Stage 1 is RSS fetch + YouTube creator clip search
- Rest of pipeline is identical

---

## STRICT VIDEO REQUIREMENTS

Every rendered video MUST:
- Be 1080×1920 (9:16 portrait, vertical)
- Use the channel's source video (downloaded via yt-dlp)
- Have the channel logo overlaid (visuals.yaml → logo_path, logo_height: 60px)
- Be bt709 color space (not bt470bg)
- Be H.264, AAC 48kHz stereo
- Be 15–60 seconds long
- Have VMAF ≥ 85 before upload

Videos MUST NOT:
- Be a text-only motion render (banned — not a reel)
- Be a static image with Ken Burns (banned)
- Be a placeholder or generic compilation used across multiple blueprints
- Be rendered without the channel logo overlay

If no source video exists for a story, the blueprint stays at DRAFTED.
Gaming (CriticalRush) requires a verified video clip — no exceptions.
Other channels: try hard to find a clip; only skip if truly none exists.

---

## CONTENT QUALITY RULES

### Hooks (≤60 characters, story-specific)
- Must reference something specific from the actual video/story
- No generic templates: "Something big happened", "the community is going wild",
  "players need to see this", "Cinema is back and", "No more excuses"
- No boilerplate suffixes
- Deduplication enforced: same hook cannot appear twice in same niche

### Captions
- Instagram: 150-200 chars + 3-5 hashtags + CTA (must end with CTA)
- Twitter: ≤280 chars, NO external links in tweet body (links in first reply only)
- YouTube: question format title, ≤40 characters
- Facebook: 200-300 chars, engaging question, no external links

### HTML/formatting
- Strip all HTML tags (<cite>, <p>, <strong> etc.) before writing to SharePoint
- text_sanitizer.sanitize_for_graph_api() must be called on all string fields

---

## PUBLISHING RULES

- 1 reel per channel per day, hard cap
- Publish window: 06:30 UTC (12:00 IST)
- Daily cap enforced at RENDER time (not just publish time)
- All platforms attempted in parallel (ThreadPoolExecutor)
- When a platform is skipped due to missing credentials: LOG a WARNING, write
  SKIPPED record to Publishing_Analytics — never silent-fail
- Facebook post survival check at 24h: if post removed by Meta, mark REMOVED_BY_META
- YouTube titles: question format, ≤40 chars
- **SKIP_APPROVAL_GATE is REMOVED** (Sprint 62). Dashboard approval is the real gate.
  Auto-scheduling on approval is niche-aware (checks slot collisions per niche).

---

## CREDENTIAL ARCHITECTURE (Sprint 62)

- Root `GenLab/.env` — shared credentials + all per-niche prefixed vars
- Per-niche `.env` — that channel's own tokens (belt + suspenders)
- `niche_credentials.py` resolves `{PREFIX}_{KEY}` per niche, never falls back cross-channel
- FB tokens are permanent EAA Page Tokens (expires_at=0) via Aspire Publisher app
- **Never run env consolidation AFTER token provisioning** — stale values overwrite fresh ones

## PIPELINE SCHEDULE (Sprint 62)

| Channel | IST | UTC | Plist |
|---------|-----|-----|-------|
| BB | 08:00 | 02:30 | com.genlab.daily-intel |
| CR (gaming) | 09:30 | 04:00 | com.genlab.criticalrush |
| FD (anime) | 11:30 | 06:00 | com.genlab.framedrift |
| SR (movies) | 13:30 | 08:00 | com.genlab.splicereel |
| CW (sports) | 15:30 | 10:00 | com.genlab.clutchwire |

Publish window: 06:30 UTC (12:00 IST) — PushToBacklog schedules at this time.

## DEDUP ARCHITECTURE (Sprint 62)

Three layers prevent duplicates:
1. **video_id dedup** in PushToBacklog — same clip never creates two blueprints
2. **DailyCapEnforcer** with niche_id — per-channel caps, not global
3. **PUBLISHED skip** — PushToBacklog won't overwrite PUBLISHED/VISUAL_READY blueprints

## CONTENT RELEVANCE (Sprint 63)

`RelevanceFilter` in `genlab_core.media.relevance_filter` scores video candidates
against niche-specific keyword lists AFTER YouTube fetch, BEFORE pipeline processing.

- Each niche has `content_filter:` in `config/sources.yaml` with `positive_keywords`,
  `negative_keywords`, and `relevance_threshold`
- Hard-rejects on negative keywords (score=0.0). Anime rejects: MMA, UFC, boxing, wrestling
- Anime threshold 0.35 (strictest — no native YouTube category)
- Rejected videos logged in run_report for debugging

## VIDEO QUALITY PIPELINE (Sprint 63)

Per-platform transcode via `PLATFORM_SPECS` in `genlab_core.media.ffmpeg`:
- YouTube: H.265 CRF18 (best quality/size ratio)
- Instagram/TikTok: H.264 CRF15 (max quality, platform re-encodes)
- Facebook: H.264 CRF20 (balanced)
- X/Twitter: H.264 CRF18

VMAF gate enabled (threshold ≥85). On fail: re-encode at CRF-3 (min CRF 12).
Override via `genlab-core/config/platform_encode_specs.yaml`.

## CIRCUIT BREAKERS (Sprint 63)

`genlab_core.http.circuit_breaker.CircuitBreaker` — CLOSED → OPEN → HALF_OPEN states.
Pre-configured instances: SHAREPOINT_CB, META_API_CB, YOUTUBE_CB, ANTHROPIC_CB, TWITTER_CB.
`@resilient` decorator combines retry + circuit breaker.

Wired into: BacklogClient, TrendingVideoFetcher, PersonaEngine, FetchInsights.
On circuit open: graceful degradation (log + continue), never crash pipeline.

## LEARNING LOOP (Sprint 63 — 100% complete)

Full feedback loop: publish → metrics → reward → bandit update.

- **FetchInsights** queries SharePoint Publishing_Analytics (not current-run context)
- **MetricCollector** has 6 platform fetchers (YT, IG, FB, X, TikTok, Threads)
- **20 insight plists** (5 niches × 4 windows: 6h, 24h, 48h, 168h)
- **LinUCB contextual bandit** with 6D features (day, hour, source, duration, velocity, relevance)
- Cold-start Thompson Sampling fallback (<50 observations per arm)
- **RewardShaper** with monetisation-aware threshold proximity boosting

## ENGAGEMENT ENGINE (Sprint 63 — 100% complete)

Hybrid auto-reply system:
- **auto** (conf ≥0.85, tox <0.15, safe pattern, <100 chars) → post immediately
- **review** (conf ≥0.5, tox <0.3) → queue for dashboard approval
- **discard** (low conf or high tox) → log and drop

5 platform reply clients in `engagement/platform_clients/`.
Pollers: YouTube (30min), Twitter (15min), Threads (10min), Facebook, Instagram (webhook).
All 5 niches covered. Detoxify toxicity gate, Dramatiq priority queues, lognormal jitter.

YouTube poll interval is 30 minutes (not 5) to conserve 10K daily API quota.

## SAAS TOOLS (Sprint 63)

- `uv run python -m genlab_core.tools.create_niche --niche-id X --brand-name Y --accent-color Z --output-dir /path`
- `uv run python -m genlab_core.tools.validate_configs --niche-dir /path`
- Canonical niche_id is `ai_creators` (not `ai_news`). `ai_news` kept as backward-compat alias.

## OBSERVABILITY (Sprint 63)

- **structlog** wraps stdlib logging — JSON output in production, console in dev
- `PipelineMetrics` auto-records per-stage timing in `metrics.jsonl` per run
- Alerting thresholds in `genlab-core/config/alerting.yaml`
- 40 integration smoke tests (`pytest -m integration`)

---

## KEY INFRASTRUCTURE

### SharePoint Lists (Site: 4020953b-b622-4a33-a0ea-763386c6af24)

| List | ID | Purpose |
|---|---|---|
| Blueprints | 1376e514-ea7d-4995-8544-eab68f8eb9cc | Main content queue |
| Stories | 217e0bc1-22c9-4219-8e3a-de865a09be3e | Fetched stories backlog |
| Publishing_Analytics | ea0c759a-1d9c-4aea-84ee-45cd2b5deb42 | Per-platform publish records |
| Analytics | 05d52cce-8501-470c-9a43-0537f7090ca8 | Engagement data |
| Content_Memory | aabea5e0-6225-4aec-890c-98fe2bb1814e | Dedup history — DO NOT PURGE |
| CriticalRush_BanditArms | b361467c-876d-427e-becd-8718f476fcc6 | Thompson Sampling state |
| GenLab_PendingFeedback | 95b41fc0-36e2-4d1d-a870-21f46c75d423 | Engagement collection queue |
| PendingEngagement | 5d03ac7f-d0d6-4291-93e6-c61e310e92f3 | Engagement worker queue |
| GenLab_MonetisationProgress | 1a464f5e-fc95-4597-84ab-3fe6e7f4274a | Channel growth tracking |

### YouTube API
- YOUTUBE_API_KEY = Data API v3 key (search + videos.list)
- Category 20 = Gaming, 17 = Sports, 1 = Film, 24 = Entertainment, 25 = News
- Anime: keyword search only (no native category)
- Quota: 10,000 units/day — search.list costs 100 units, videos.list costs 1 unit
- Max 50 search calls per day across all channels

### External Services
- LLM: Anthropic Claude Haiku (content writing), OpenAI GPT-Image-1 (visuals)
- TTS: ElevenLabs → OpenAI TTS → Edge-TTS → gTTS (cascade)
- Video: yt-dlp (download), FFmpeg (render), short-video-maker Docker (port 3123)
- Trends: pytrends (Google Trends unofficial wrapper)

---

## CODING STANDARDS

- Python 3.12+, strict typing, Pydantic v2 schemas
- All new shared code goes in genlab-core/src/genlab_core/
- Read existing patterns before writing new code (read backlog_client.py before
  writing a new client; read trending_video_fetcher.py before writing a new fetcher)
- Every new file needs tests in tests/ using unittest.mock for external calls
- Conventional commits: feat(scope), fix(scope), refactor(scope)
- Never put values in code that belong in config YAML
- All pipeline stages accept and return typed Pydantic models
- Log what every stage is doing, which path it took, and why

---

## WHAT NOT TO DO

1. **Never add text-only renders** — not now, not as a fallback, not for any reason
2. **Never add new shared code to BlackboxBrief or CriticalRush** — use genlab-core
3. **Never modify core pipeline base classes** without explicit discussion
4. **Never hardcode credentials** — all secrets in .env files, never in code or YAML
5. **Never silent-fail** a platform publish — log warnings and write SKIPPED records
6. **Never use template strings** for captions ("Something big happened with [TITLE]")
7. **Never write a hook longer than 60 characters**
8. **Never create a blueprint for a scheduled future game** (sports: Final/In Progress only)
9. **Never ingest non-anime content into FrameDrift** — require anime keyword match
10. **Never publish more than 1 reel per channel per day**

---

## SAAS ARCHITECTURE CONSIDERATIONS

Every decision should consider multi-tenancy:
- Use niche_id on all SharePoint writes (tenant isolation)
- No hardcoded account IDs — read from publishing.yaml per niche
- Config-driven: new brand = new YAML config, not new code
- Future: PostgreSQL RLS + FastAPI ContextVar middleware for true tenant isolation

---

## CHANNEL ACCENT COLORS & LOGOS

| Channel | niche_id | Accent Color | Logo |
|---|---|---|---|
| Blackbox Brief | ai_creators | #00D4FF | BlackboxBrief/assets/logo.png |
| CriticalRush | gaming | #f97316 | CriticalRush/assets/logo.png |
| ClutchWire | sports | #FF2040 | ClutchWire/assets/logo.png |
| SpliceReel | movies | #C9A84C | SpliceReel/assets/logo.png |
| FrameDrift | anime | #7B3FE4 | FrameDrift/assets/logo.png |

FrameDrift is ANIME (not fashion — that was a legacy description bug fixed in Sprint 47).

---

## HUMAN ACTION ITEMS OUTSTANDING

| # | Action | Blocker for |
|---|---|---|
| H1 | ElevenLabs API key → BlackboxBrief/.env | TTS quality |
| H3 | YouTube quota increase (Google Cloud Console) | Multi-channel YT publishing |
| H5 | Per-niche X/Twitter + Threads credentials (CW, SR, FD, CR) | X/Threads publishing |
| H6 | SpliceReel FB page origin investigation (8,507 followers) | SR Facebook publishing |

---

## CURRENT SPRINT STATUS (as of 2026-03-14)

Completed: Sprints 1–48
- Sprint 45: P0–P3 audit remediation, schedule change to 1x/day
- Sprint 45B: 17 gap items, ruff clean, test fixes
- Sprint 46A: ClutchWire emoji/apostrophe sanitizer (SP push_to_backlog)
- Sprint 46B: Fresh pipeline restart, stale data purge
- Sprint 47: v4 audit remediation, BB 0-blueprint fix, hook overhaul
- Sprint 47 Addendum: Pre-existing test failures fixed
- Sprint 48: Video-first architecture — TrendingVideoFetcher, GoogleTrendsIntel, VideoContentWriter

Test baseline: ~3,428 passing, target 0 failures
