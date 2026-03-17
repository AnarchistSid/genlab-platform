# GenLab End-to-End Data Flow Trace

**Generated:** 2026-03-17
**Scope:** Complete lifecycle of a video from YouTube discovery through publish to engagement learning.
**Method:** Read from actual source code — no guesses.

---

## Stage 1: Fetch Trending Videos

**Entry point:** `genlab-core/src/genlab_core/media/trending_video_fetcher.py:FetchTrendingVideos.execute()` (line 886)
**Trigger:** Pipeline runner loads stages from `niche.yaml` pipeline.stages list. Each channel's `daily_intel.sh` sources `.env`, then calls `core/pipeline_runner.py` which instantiates `GenericPipelineRunner` and calls `.run(niche_id)`. The runner loads `niche.yaml`, imports stage classes via `importlib`, and executes them in declared order.

**Input:**
- `context["niche_id"]` — e.g. `"gaming"`
- `context["niche_config"]["video_sourcing"]` — `top_n_per_run`, `max_age_hours`, `min_view_velocity`, `use_google_trends`, `allow_keyword_search`
- `YOUTUBE_API_KEY` env var
- `sources.yaml` per niche (subscribed YouTube channels under `youtube_channels`)

**Process:**
1. Reset quota tracker (`QUOTA_TRACKER["units_used"] = 0`)
2. Load subscribed channels from `sources.yaml` via `_load_sources_config(niche_id)`
3. Optionally fetch Google Trends keywords via `GoogleTrendsIntel.get_trending_topics()` (3-tier: RSS feed -> pytrends real-time -> pytrends daily -> seed keywords)
4. Instantiate `TrendingVideoFetcher(api_key)` and call `fetch_trending()`:
   - **Strategy 1:** `_fetch_most_popular(category_id)` — YouTube `videos.list` with `chart=mostPopular` (1 API unit). Category IDs: gaming=20, sports=17, movies=1, anime=none.
   - **Strategy 2:** `_fetch_from_channels(subscribed_channels)` — RSS first (0 quota), then `_fetch_playlist_items()` fallback if RSS < 3 items (1 unit per channel). RSS parses Atom XML from `youtube.com/feeds/videos.xml`.
   - **Strategy 3 (fallback):** `_search_recent()` — `search.list` (100 units/call), only if `allow_keyword_search=true` AND fewer than 3 candidates found. Capped at 2 search calls max.
   - **Stat enrichment:** `_fetch_video_details()` batches up to 50 video IDs per `videos.list` call (1 unit/batch). Parses `snippet,statistics,contentDetails,status`.
   - **Filtering:** Duration 20-240s, view velocity >= niche threshold (gaming:500, sports:800, movies:300, anime:400, ai_creators:150).
5. **Relevance filter:** `RelevanceFilter` from `sources.yaml` `content_filter`. Positive keyword overlap scoring (0.0-1.0), hard reject on negative keyword match. Threshold default 0.3.
6. **Composite quality gate:** `CompositeScorer.score_and_rank()` — `composite = velocity_score * trend_multiplier * niche_relevance`. Per-niche thresholds: gaming 0.35, sports 0.35, movies 0.30, anime 0.30, ai_creators 0.25.
7. Convert passing videos to story dicts via `TrendingVideo.to_story()` — generates `story_id` via `sha256(source_url + published_date)`.
8. Prepend video stories to `context["stories"]`.

**External calls:**
- YouTube Data API v3: `videos.list` (1 unit/call), `search.list` (100 units/call), `playlistItems.list` (1 unit/call)
- YouTube RSS feeds (0 quota)
- Google Trends: pytrends `trending_searches()` / `realtime_trending_searches()` / RSS at `trends.google.com`
- All YouTube calls go through `YOUTUBE_CB` circuit breaker (from `genlab_core.http.circuit_breaker`)

**Output:**
- `context["stories"]` — list of story dicts (video stories prepended to existing)
- `context["trending_videos"]` — raw TrendingVideo dicts
- `context["run_stats"]["trending_videos_found"]`, `youtube_quota_units`, `youtube_rss_fetches`

**State changes:**
- File: `.tmp/runs/{run_id}/trending_videos.json` — manifest of all discovered videos

**Error handling:**
- Missing `YOUTUBE_API_KEY`: logs error, sets `trending_videos_found=0`, returns context unchanged
- Individual API failures: caught per-strategy, logged, returns empty list for that strategy
- Circuit breaker open: skips that API call with warning
- All videos filtered: warns "no content will be published this run"

**Failure modes:**
- Quota exhaustion mid-run: RSS fallback creates "stub" TrendingVideos with estimated velocity from channel weight
- Google Trends rate-limited: falls back to seed keywords silently — may reduce quality but never blocks

---

## Stage 2: Score and Filter

**Entry point:** `genlab-core/src/genlab_core/scoring/composite_scorer.py:CompositeScorer.score_and_rank()` (line 149) — called inline within `FetchTrendingVideos.execute()` at line 988
**Trigger:** Runs immediately after video fetch within the same stage.

**Input:**
- List of `TrendingVideo.to_dict()` objects with `video_id`, `title`, `view_velocity`
- `trend_multipliers: Dict[str, float]` — per-video Google Trends multipliers (1.0-3.0)
- Niche-specific thresholds from `video_sourcing.composite_quality_gate` in `niche.yaml`

**Process:**
1. For each video, compute `VideoScore`:
   - `velocity_score = min(view_velocity / velocity_threshold, 1.0)` — normalised against niche baseline (gaming:1500, sports:2000, movies:800, anime:600, ai_creators:400)
   - `trend_multiplier` — from Google Trends lookup, clamped to [0.0, 3.0]
   - `niche_relevance` — binary 1.0 or 0.0
   - `composite = velocity_score * trend_multiplier * niche_relevance`
2. Filter: only videos with `composite >= min_composite` pass (gaming:0.35, sports:0.35, movies:0.30)
3. Sort descending by composite score
4. `score_visual_potential()` is available for RSS-sourced stories (pre-video sourcing) — uses keyword matching against `_STRONG_VISUAL_SIGNALS` per niche

**External calls:** None (pure computation)

**Output:** List of `VideoScore` objects that passed the gate, sorted best-first. Composite score attached to each story as `story["composite_score"]`.

**Error handling:** If all videos fail the gate, warns but continues — pipeline will produce no blueprints this run.

**Failure modes:** Overly aggressive thresholds can zero out all content for a niche on slow news days.

---

## Stage 3: Compose Blueprints (Push to Backlog)

**Entry point:** `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py:PushToBacklog.execute()` (line 68)
**Trigger:** Loaded from `niche.yaml` as `genlab_core.pipeline.stages.push_to_backlog.PushToBacklog`

**Input:**
- `context["stories"]` — list of story dicts with `content` sub-dict (from WriteGamingContent or VideoContentWriter)
- `context["niche_id"]`
- Azure/SharePoint credentials from environment

**Process:**
For each story in `context["stories"]`:
1. Sanitize title via `sanitize_for_graph_api()`
2. Generate `story_id = sha256(source_url + published_at)`
3. **Upsert story** to SharePoint Stories list:
   - `find_story_by_story_id()` — if exists, update `niche_id`; else create with fields: `story_id`, `title`, `url`, `source`, `published_at`, `summary`, `priority`, `status=INTAKE`, `niche_id`
4. Skip if no `content` dict on story
5. **Video-level dedup:** If `video_id` exists, query Blueprints list for matching `video_id + niche_id`. If found, skip (no duplicate blueprints for same clip).
6. Generate `candidate_id = sha256(story_id + template_id + angle_slug)`
7. **Blueprint dedup:** Query Blueprints by `candidate_id`. If exists with status PUBLISHED/PUBLISHING/VISUAL_READY, skip. If DRAFTED/SCORED, update `niche_id`.
8. **Create blueprint** with fields:
   - `candidate_id`, `story` (linked record ID), `story_id`, `hook_text`, `caption` (IG), `hashtags`, `youtube_content`, `twitter_content`, `facebook_content`
   - `priority_score` from `final_score` / `composite_score` / `score`
   - `status`: **VISUAL_READY** if `rendered_path` exists, else **DRAFTED**
   - `format`: "reel"
   - `niche_id`, `topic`, `angle`
   - If `rendered_path`: `visual_paths` (JSON array), `scheduled_for` (next 06:30 UTC slot)

**External calls:**
- SharePoint Graph API via `BacklogClient`: `stories.create()`, `stories.update()`, `blueprints.all()`, `blueprints.create()`, `blueprints.update()`

**Output:**
- `context["run_stats"]["backlog_push"]` — `{stories_pushed, blueprints_pushed, video_dedup_skipped, errors, status}`

**State changes:**
- SharePoint Stories list: new or updated story records
- SharePoint Blueprints list: new blueprint records with status DRAFTED or VISUAL_READY
- `scheduled_for` field set to next 06:30 UTC (today if not yet passed, tomorrow otherwise)

**Error handling:**
- Missing Azure credentials: logs warning, returns `status=skipped_no_credentials`
- BacklogClient init failure: returns `status=error_init`
- Per-story failures: caught individually, logged, counted in errors list
- Video dedup check failure: allows through with warning (fail-open)

**Failure modes:**
- If SharePoint is down, ALL blueprint creation silently fails — stories accumulate locally but never reach the backlog

---

## Stage 4: Write Content

**Entry point:** `genlab-core/src/genlab_core/writing/video_content_writer.py:write_video_content()` (line 94) — called by niche-specific writing stages (e.g., `WriteGamingContent`)
**Trigger:** Writing stage in pipeline, executed after fetch/score stages

**Input:**
- `video: dict` — TrendingVideo.to_dict() with `title`, `channel_name`, `view_count`, `view_velocity`, `description_snippet`, `tags`
- `niche_id` — determines voice/persona
- `llm_client` — object with `.complete(system, user, max_tokens, temperature)`
- `existing_hooks` — list of already-used hooks for dedup

**Process:**
1. Load `NICHE_VOICE[niche_id]` — per-niche voice config: `account`, `style`, `audience`, `ctas`, `hashtags`
2. Build system prompt with:
   - Voice/style directives
   - Character limits: hook <=60, IG caption 150-200 chars, Twitter <=280, YouTube <=40 question, Facebook 200-300
   - Existing hooks (last 5) as dedup exclusion list
3. Build user prompt with video title, channel, view stats, tags, description
4. Call LLM: `llm_client.complete(system, user, max_tokens=600, temperature=0.8)`
5. Parse JSON response, strip markdown fences
6. Post-processing:
   - **Hook:** Normalize smart quotes to ASCII. Truncate to 57 chars + "..." if >60.
   - **Instagram caption:** Split body/hashtags, enforce 3-5 hashtags (from voice defaults if <3), inject CTA if missing, budget body to 200 total chars, reassemble: body + CTA + hashtags.
   - **Twitter:** Truncate to 277 + "..." if >280.
   - **YouTube:** Truncate to 37 + "?" if >40.
   - **Facebook:** Truncate to 297 + "..." if >300.

**External calls:**
- Anthropic Claude Haiku API via `llm_client.complete()` (1 call per video)

**Output:**
- Dict with keys: `hook`, `instagram_caption`, `twitter_content`, `youtube_content`, `facebook_content`
- Attached to `story["content"]` by the calling stage

**Error handling:**
- LLM failure: returns fallback using raw video title — hook = title[:57]+"...", IG = title + channel + hashtags, etc. Never returns empty content.
- JSON parse failure: same fallback path

**Failure modes:**
- LLM returns generic/template hooks — the system prompt tries to prevent this but enforcement is post-hoc (character limits only, no semantic check in this stage)

---

## Stage 5: Render Video

**Entry point (FrameCompositor):** `genlab-core/src/genlab_core/media/frame_compositor.py:FrameCompositor.compose()` (line 297)
**Entry point (VideoCompositor):** `genlab-core/src/genlab_core/media/video_compositor.py:VideoCompositor.compose_vertical()` (line 272)
**Trigger:** Niche-specific render stages (e.g., `RenderGamingVideo`, declared in `niche.yaml`)

**Input:**
- Source video clip path (downloaded via yt-dlp)
- `hook_text` — <=60 char hook from content writing
- `visuals.yaml` per niche — branding config: `logo_path`, `accent_color`, `channel_name`, `handle`, ffmpeg preset

**Process — FrameCompositor (Sprint 62 redesign, primary):**
1. Probe source video via ffprobe: get width, height, duration, fps, aspect ratio
2. Classify layout: `landscape` (ar >= 1.33), `portrait` (ar <= 0.75), `square` (0.75-1.33)
3. Build FFmpeg filter_complex per layout case:
   - **Landscape:** Canvas 1080x1920, video scaled to 1080xL_VIDEO_H at y=460. Black top bar (y=0-310) with logo(60px, x=45, y=310) + channel name(24px) + handle(17px). Hook text zone y=380-460 (44px bold white, shadow, vertically centered, max 2 lines). Video at y=466.
   - **Portrait:** Video fills entire 1080x1920 canvas. Dark gradient overlay (55% opacity, top 480px). Logo + hook overlaid on gradient.
   - **Square:** Same header as landscape. Video 1080x1080 at y=460.
4. Execute FFmpeg: inputs = source + logo PNG, filter_complex, output flags: libx264, CRF 15, yuv420p, bt709, AAC 48kHz stereo, `-movflags +faststart`
5. On timeout: retry with `fallback_preset` (fast instead of slow)

**Process — VideoCompositor (sandwich layout):**
1. Check logo exists, source clips exist
2. Optional smart crop (face/motion-aware) for landscape clips
3. Concatenate multiple clips if >1 (ffmpeg concat)
4. `_render_sandwich()`: scale source to content area (top 12% bar + bottom 18% bar), overlay logo (left margin 24px, vertically centered in top bar), overlay hook text (right of logo, 32px bold white, max 2 lines)
5. `derive_landscape()`: 16:9 variant via blurred pillarbox (never crop) for Facebook/X

**External calls:**
- FFmpeg subprocess (local binary), timeout 120-300s
- ffprobe for video metadata

**Output:**
- Rendered MP4 at `.tmp/runs/{run_id}/rendered/{candidate_id}_reel.mp4` (1080x1920, H.264, AAC)
- Landscape variant for FB/X if configured
- Path stored in `story["media"]["rendered_path"]`

**Error handling:**
- FFmpeg failure: raises RuntimeError, logged with last 2000 chars of stderr
- Logo not found: raises FileNotFoundError
- Timeout: retries with faster preset, then fails
- `sandbox: true` in niche.yaml routes through `SandboxedFFmpegRunner`

**Failure modes:**
- Source video corruption causes FFmpeg to exit non-zero — no rendered video, blueprint stays DRAFTED
- Logo path misconfiguration silently breaks branding

---

## Stage 6: Validate Videos

**Entry point:** `genlab-core/src/genlab_core/pipeline/stages/validate_videos.py:ValidateVideos.execute()` (line 67)
**Trigger:** Pipeline stage declared in `niche.yaml` (runs after render, before push_to_backlog)

**Input:**
- `context["stories"]` with `media.rendered_path` per story
- `niche_config.video_validation.auto_fix` (default True)
- `niche_config.video_validation.run_vmaf` (default True)

**Process:**
For each story with a rendered video:
1. **ffprobe** the rendered file
2. **Spec check** against `SPEC` dict:
   - Dimensions: exactly 1080x1920
   - Codec: h264
   - Pixel format: yuv420p
   - Color space: bt709
   - Audio: AAC, 48kHz, stereo
   - Duration: 15-60s
   - File size: <100MB
3. If spec passes and VMAF enabled:
   - Compare rendered vs `master_path` using `check_vmaf()` — threshold >= 85
   - On VMAF failure: re-encode at CRF-3 (minimum CRF 12), recheck VMAF
   - On second failure: reject
   - No master_path: skip VMAF (fail-open)
4. If spec fails and auto_fix enabled:
   - `_can_fix()` checks if issues are fixable (codec, pix_fmt, color_space, audio)
   - `_fix()` re-encodes with correct settings: libx264/yuv420p/bt709/AAC/48kHz/stereo
   - Updates `media["rendered_path"]` to fixed file

**External calls:**
- FFmpeg/ffprobe subprocess
- Netflix VMAF model (via `video_validator.check_vmaf()`)

**Output:**
- `story["media"]["video_validation"]` = `{valid: bool, issues: [...], auto_fixed: bool, vmaf_reencoded: bool}`
- `context["run_stats"]["video_validation"]` = `{passed, failed, fixed, skipped}`

**Error handling:**
- Non-fatal: exceptions per-video are caught and logged, don't crash pipeline
- Probe failure: marks as `{valid: False, error: "probe_failed"}`

**Failure modes:**
- VMAF not installed: `check_vmaf` may fail — should degrade to spec-only check
- Auto-fix can produce larger files if re-encoding from already-compressed source

---

## Stage 7: Status Transitions

**State machine (Blueprints list `status` field):**

```
INTAKE -> VALIDATED -> INTEL_READY -> RESEARCHED -> DRAFTED -> VISUAL_READY -> PUBLISHED
```

**Key transitions and triggers:**

| From | To | Trigger | File |
|---|---|---|---|
| (new) | DRAFTED | `PushToBacklog.execute()` — no rendered video | `push_to_backlog.py:230` |
| (new) | VISUAL_READY | `PushToBacklog.execute()` — rendered video exists | `push_to_backlog.py:230` |
| VISUAL_READY | PUBLISHED | `publish_all_platforms.py` — at least 1 platform succeeds (best_effort) | `publish_all_platforms.py:2292` |

**`scheduled_for` field:** Set by `PushToBacklog` when status=VISUAL_READY. Calculates next 06:30 UTC slot (today if not passed, else tomorrow). Dashboard approval can override via `_next_available_slot()` which checks niche collisions.

---

## Stage 8: Human Review (Dashboard)

**Entry point:** `dashboard/server/api/publishing_queue.py` — Flask Blueprint at `/api/v1/queue`
**Dashboard core:** `dashboard/server/core/publishing_queue.py:PublishingQueueManager`

**Queue status derivation (virtual field, not stored):**

| Virtual Status | Backlog Fields |
|---|---|
| PENDING_APPROVAL | status=VISUAL_READY, action_taken blank |
| APPROVED | status=VISUAL_READY, action_taken=approved |
| HELD | status=VISUAL_READY, action_taken=held |
| PUBLISHED | status=PUBLISHED |
| PUBLISH_FAILED | status=VISUAL_READY, action_taken=approved, error_log set |

**Process:**
1. `GET /api/v1/queue` — lists VISUAL_READY items, filterable by `niche_id` and `queue_status`. Uses `_transform_media(lite=True)` for thumbnails (skips ffprobe).
2. `POST /api/v1/queue/:id/approve` — sets `action_taken=approved` on the blueprint. Auto-schedules via `_next_available_slot(niche_id)` — checks publishing.yaml `schedule_slots`, avoids niche collision (1 post/niche/day).
3. `POST /api/v1/queue/:id/hold` — sets `action_taken=held` with hold reason.
4. `POST /api/v1/queue/:id/release` — clears `action_taken` back to blank.

**Gate invariant:** Nothing publishes unless `action_taken == "approved"` (enforced in `publish_all_platforms.py` line 1677).

**External calls:** SharePoint Graph API (sync REST via `requests`, not async msgraph SDK — to avoid Eventlet deadlocks).

**State changes:**
- SharePoint Blueprints: `action_taken` field, `scheduled_for` field
- WebSocket event `blueprint_updated` emitted to connected dashboard clients

---

## Stage 9: Publish

**Entry point:** `Content Scraper/execution/publish_all_platforms.py:main()` (line 1560) -> `_main_locked()` (line 1605)
**Trigger:** Scheduled via launchd plist per niche (gaming 09:30 IST, anime 11:30, movies 13:30, sports 15:30). Publish window 06:30 UTC = 12:00 IST.

**Input:**
- `--run-id`, `--niche`, `--dry-run`, `--force`, `--max-blueprints`, `--platforms`
- `publishing.yaml` config
- SharePoint Blueprints list (VISUAL_READY items)

**Process:**
1. **Exclusive file lock** — per-niche `publisher-{niche}.lock` in `.tmp/` via `fcntl.LOCK_EX|LOCK_NB`. Prevents overlapping daemon runs.
2. **Fetch candidates:** `client.get_blueprints_by_status("VISUAL_READY", niche_id=niche_filter)`
3. **Approval gate:** Filter to `action_taken == "approved"` (unless SKIP_APPROVAL_GATE or TEST_MODE)
4. **Selection gates** (applied in order):
   - `_select_due_blueprints()` — `scheduled_for` must be <= now (schedule gate)
   - **Video-only gate:** `_has_local_video_file()` — at least one .mp4 must exist on disk
   - **Per-niche dedup:** max 1 item per niche per run
   - **Score floor gate:** `priority_score >= MIN_PRIORITY_SCORE` (default 0.3)
   - **Gap guard:** min hours between consecutive publishes (configurable)
5. **Pre-flight checks:** Verify Facebook token, Twitter credentials per niche
6. **DailyCapEnforcer:** Loaded from `platform_caps.yaml` — default 1 post/platform/niche/day. Counts from SharePoint Publishing_Analytics.
7. **For each due blueprint:**
   a. Validate via `BlueprintContract.from_record()` — schema check
   b. **Cross-channel guard:** `_require_niche_id(fields)` — validates niche_id in `_VALID_NICHE_IDS`, blocks if missing/unknown
   c. **Niche filter:** rejects if `bp_niche_id != niche_filter`
   d. **Content safety:** `_has_publishable_content()` check
   e. Load linked story from SharePoint
   f. Check existing `platform_publish_status` — skip already-PUBLISHED platforms
   g. **Per-platform gates:** credentials, daily cap, payload readiness, PublishGatekeeper
   h. Mark platforms as PUBLISHING in SharePoint (intermediate state)
   i. **Build payloads:** `build_payload(fields, platform)` -> `PublishPayload`
   j. **Concurrent dispatch:** `_dispatch_niche_aware(tasks, niche_id)`:
      - Per-niche credential resolution via `niche_credentials.py` (`resolve_meta_credentials`, `resolve_youtube_credentials`, `resolve_twitter_credentials`, `resolve_fb_credentials`, `resolve_threads_credentials`)
      - Instagram: upload to litterbox CDN (public URLs required), ensure audio track, call `InstagramClient.publish()`
      - YouTube: `YouTubeClient.publish()` — Shorts upload via Data API v3
      - Facebook: `FacebookClient.publish()` — Page video post via Graph API
      - X/Twitter: `XTwitterClient.publish()` — tweet with media via API v2
      - Threads: `ThreadsClient.publish()` — via Threads API
      - All platforms dispatched via `ThreadPoolExecutor` (concurrent)
   k. **Per-platform result:** Update `platform_publish_status` in SharePoint after each platform completes
   l. **Overall status:** best_effort = PUBLISHED if ANY succeeds; all_or_nothing = PUBLISHED only if ALL succeed
   m. **Status update:** `client.blueprints.update(record_id, {status: "PUBLISHED", platform_publish_status: {...}})`
   n. **Publishing_Analytics:** `client.log_publish_result()` per platform — candidate_id, platform, status, post_id, format, timing, file_size, niche_id
   o. **Analytics seed:** `client.upsert_analytics(post_id, platform, insights={})` — empty metrics, populated later by FetchInsights
   p. **PendingFeedback registration:** Creates `PendingFeedbackTask` per successful platform publish — `content_id`, `platform`, `niche_id`, `published_at`, `platform_post_id`, `content_type`, `hook_text`
8. Write summary to `.tmp/runs/{run_id}/publish_all_summary.json`
9. Release file lock

**External calls:**
- Instagram: `graph.facebook.com/v21.0/{ig_user_id}/media` (container creation), `graph.facebook.com/v21.0/{ig_user_id}/media_publish` (publish). Video uploaded to litterbox CDN first.
- YouTube: Data API v3 `videos.insert` with `snippet.categoryId`, `status.privacyStatus=public`, `status.selfDeclaredMadeForKids=false`
- Facebook: `graph.facebook.com/v21.0/{page_id}/videos` (video upload)
- X/Twitter: API v2 via tweepy — `media.upload()` + `tweets.create()`
- SharePoint: multiple CRUD operations via BacklogClient

**State changes:**
- SharePoint Blueprints: `status` -> PUBLISHED, `platform_publish_status` -> JSON with per-platform results and post IDs
- SharePoint Publishing_Analytics: new record per platform with `candidate_id`, `platform`, `status`, `post_id`, `published_at`, `niche_id`
- SharePoint Analytics: seeded with empty metrics
- SharePoint PendingFeedback: new task per platform
- File: `.tmp/runs/{run_id}/publish_all_summary.json`

**Error handling:**
- Per-platform failures are isolated — one platform failing doesn't block others
- PUBLISH_OP_ERRORS tuple catches RequestException, TimeoutError, OSError, ODataError, GoogleHttpError
- Analytics logging failures are silently swallowed (never block publishing)
- Stale file detection: blueprints with missing rendered files are skipped
- Twitter 429: shared `tw_rate_limited` Event signals all subsequent blueprints to skip Twitter

**Failure modes:**
- Litterbox CDN upload failure: Instagram publish fails (no public URL)
- YouTube quota exhausted: `uploadLimitExceeded` — fails silently per blueprint
- SharePoint down during publish: platform results may not be persisted (re-publish risk)

---

## Stage 10: Engagement Collection (FetchInsights)

**Entry point:** `genlab-core/src/genlab_core/pipeline/stages/fetch_insights.py:FetchInsights.execute()` (line 46)
**Trigger:** Pipeline stage in `niche.yaml` (runs at end of daily pipeline, after publish)

**Input:**
- `context["backlog_client"]` — BacklogClient instance
- `context["niche_id"]`
- SharePoint Publishing_Analytics records

**Process:**
1. Query Publishing_Analytics for current niche: `formula = AND({niche_id}='gaming')`
2. For each record:
   - Skip if `metrics_fetched` is already set
   - Skip if no `post_id` or `platform`
   - Parse `published_at`, compute age in hours
   - **FRESH window:** 6h-48h after publish (first snapshot)
   - **WARM window:** 2-7 days (growth tracking)
   - Skip if < 6h (API data delay) or > 7 days
3. Dispatch to platform-specific fetcher through circuit breaker:
   - **Instagram:** `graph.facebook.com/v21.0/{post_id}?fields=like_count,comments_count` + `/insights?metric=reach,saved,shares,total_interactions`. Returns: likes, comments, reach, saved, shares.
   - **YouTube:** `googleapis.com/youtube/v3/videos?part=statistics&id={post_id}`. Returns: views, likes, comments.
   - **Facebook:** `graph.facebook.com/v21.0/{post_id}?fields=shares,reactions.summary,comments.summary`. Returns: shares, reactions, comments.
   - **X/Twitter:** `api.twitter.com/2/tweets/{post_id}?tweet.fields=public_metrics`. Returns: likes, retweets, replies, impressions.
4. Mark record as fetched: update `metrics_fetched` timestamp in Publishing_Analytics

**External calls:**
- Meta Graph API (Instagram + Facebook insights)
- YouTube Data API v3 (video statistics)
- Twitter API v2 (tweet metrics)
- SharePoint Graph API (read + update Publishing_Analytics)
- All calls go through per-platform circuit breakers

**Output:**
- `context["run_stats"]["insights"]` = `{fetched, skipped, errors, platforms: {platform: {fetched, errors}}}`

**State changes:**
- SharePoint Publishing_Analytics: `metrics_fetched` timestamp set per record

**Error handling:**
- Non-fatal: API failures logged per platform, never crash pipeline
- Missing credentials: returns None for that platform
- Circuit breaker open: skips platform with warning

**Failure modes:**
- IG Reels don't support `impressions` metric (400 error) — handled by using `reach,saved,shares,total_interactions` instead
- YouTube 48h data lag means very fresh posts return stale/zero metrics

---

## Stage 11: Learning Loop

### RewardShaper

**Entry point:** `genlab-core/src/genlab_core/learning/reward_shaper.py:RewardShaper.compute_reward()` (line 239) and `MonetisationRewardShaper.compute()` (line 375)
**Trigger:** Called by `PerformanceLearner` stage after FetchInsights

**Input:**
- `platform` — which platform metrics came from
- `metrics` — per-post engagement metrics (views, likes, shares, completion_rate, etc.)
- `channel_metrics` — channel-level metrics for threshold proximity (subscriber_count, watch_hours, etc.)

**Process (RewardShaper):**
1. Get base weights per platform — e.g. YouTube: views 0.3, avg_view_duration 0.3, subscriber_gained 0.2, like_rate 0.1, comment_rate 0.1
2. **Threshold proximity boosting:** For each `MonetisationThreshold`, if channel metric is within 20% of target, multiply the `boost_metric` weight by `boost_factor`:
   - YouTube: within 20% of 4000 watch hours -> avg_view_duration weight * 3.0
   - YouTube: within 20% of 1000 subs -> subscriber_gained weight * 2.0
   - TikTok: within 20% of 10K followers -> share_rate weight * 2.0
   - Facebook: within 20% of 600K minutes_viewed -> completion_rate weight * 3.0
3. Re-normalise weights to sum to 1.0
4. Compute reward: `sum(weight * normalise(metric_value))` clamped to [0.0, 1.0]
5. Normalisation: `min(1.0, value / target)` — targets per metric per platform (e.g. YouTube views target = 10000)

**Process (MonetisationRewardShaper):**
1. Weighted sum: `completion_rate * 0.40 + engagement * 0.35 + shares/views * share_scale * 0.25`
2. Threshold boost from live SharePoint data (MonetisationMultiplierProvider) or static YAML
3. `final_reward = base * (1.0 + boost)` then normalised via WelfordNormalizer (z-score after 10 samples)

### LinUCB Bandit

**Entry point:** `genlab-core/src/genlab_core/learning/linucb.py:LinUCBBandit` (line 104)

**Process:**
1. 6-dimensional context vector: `[day_of_week, hour_utc, source_type, duration_bucket, view_velocity, relevance_score]`
2. Per-arm: `A` (6x6 matrix), `b` (6-vector), `n_obs` counter
3. Select: `p = theta^T x + alpha * sqrt(x^T A^{-1} x)` — exploitation + exploration
4. Update: `A += x x^T`, `b += reward * x`
5. Cold-start: arms with < 50 observations fall back to Thompson Sampling (existing alpha/beta posteriors)
6. State persisted to SharePoint via `arm_loader.py`

### Config Writer

**Entry point:** `genlab-core/src/genlab_core/learning/config_writer.py:update_schedule_from_bandit()` (line 63)

**Process:**
1. Reads `publishing.yaml` for current schedule_slots
2. For each platform with >= 50 observations:
   - Shift slot toward bandit-learned optimal hour
   - Max shift: +/- 2 hours per update cycle
   - Clamp to 06:00-23:00 range
3. Atomic write: write to `.yaml.tmp` then rename

**Safety rules:**
- `MIN_OBSERVATIONS_PER_ARM = 50` — requires data before trusting
- `MAX_SHIFT_HOURS = 2` — never dramatic schedule changes
- `ALLOWED_HOUR_RANGE = (6, 23)` — never schedule at night
- Content type ratios clamped to [10%, 50%]

---

## Stage 12: Engagement Reply

### Comment Processor

**Entry point:** `genlab-core/src/genlab_core/engagement/comment_processor.py:process_reply_event()` (line 190)
**Trigger:** Dramatiq actor in `engagement/tasks.py`, fed by pollers

**Input:**
- `event` dict: `comment_id`, `comment_text`, `platform`, `niche_id`, `post_id`, `post_context`

**Process — 8-step pipeline:**
1. **Injection check:** `check_for_injection(comment_text)` — skip if injection pattern detected
2. **Idempotency:** `_has_replied(comment_id, platform)` — scan `.engagement_replied.jsonl` file. Skip if already replied.
3. **SharePoint record:** Write to PendingEngagement list (optional, fails gracefully)
4. **Spam filter:** `is_spam(comment_text)` — fast regex patterns. Skip if spam.
5. **Inbound toxicity:** `ToxicityGate().check_inbound(comment_text)` — Detoxify model. Skip if `is_toxic=True`.
6. **Rate limit:** `EngagementRateLimiter.acquire(platform)` — token bucket per platform (IG: 20/hr, YT: 10/hr, X: 4/hr, Threads: 3/hr). Raises RuntimeError on limit (triggers Dramatiq retry with backoff).
7. **Generate reply:**
   - Load persona YAML (`engagement/personas/{niche_id}.yaml`)
   - `PersonaEngine.generate_reply(comment, platform, post_context)` — Claude Haiku LLM call with persona instructions
   - **Outbound toxicity gate** — Detoxify checks generated reply. Fails-closed (toxic replies blocked).
   - **Confidence routing:** `classify_reply_action(reply_text, confidence, toxicity)`:
     - `auto`: confidence >= 0.85 AND toxicity < 0.15 AND safe pattern AND <100 chars -> post immediately
     - `review`: confidence >= 0.5 AND toxicity < 0.3 -> queue for human approval
     - `discard`: low confidence OR high toxicity -> log and drop
8. **Timing jitter:** `human_delay()` — log-normal distribution delay (human-like timing)
9. **Post reply:** `_post_reply(platform, post_id, comment_id, reply_text)` — routes to platform client via `get_client(platform)`, calls `client.post_reply(parent_id, text, context_id)`
10. **Mark replied:** Append `{c: comment_id, p: platform}` to `.engagement_replied.jsonl` with file-level locking (`fcntl.LOCK_EX`)

### Pollers

**Entry point:** `genlab-core/src/genlab_core/engagement/poller.py`

**YouTube (30-minute interval):**
1. Get API key or OAuth token
2. Fetch recent video IDs from uploads playlist (`playlistItems.list`, max 10)
3. Poll `commentThreads` per video (`videoId` param, `order=time`, `maxResults=20`)
4. Filter out own comments (skip if `authorChannelId == channel_id`)
5. Quota handling: on 403 with "quota" in message, stop polling

**X/Twitter (15-minute interval):** Poll mentions via API v2

**Threads (10-minute interval):** Poll via Threads API

**State changes:**
- `.engagement_replied.jsonl` — append-only idempotency log
- SharePoint PendingEngagement list — comment records with status (replied/skipped/failed/rate_limited)

---

## Data Persistence Summary

| Data | Storage | Key Fields |
|---|---|---|
| Trending videos | `.tmp/runs/{run_id}/trending_videos.json` | video_id, title, view_velocity |
| Stories | SharePoint Stories list | story_id, title, url, niche_id, status |
| Blueprints | SharePoint Blueprints list | candidate_id, status, niche_id, scheduled_for, action_taken, visual_paths, platform_publish_status |
| Rendered videos | `.tmp/runs/{run_id}/rendered/*.mp4` | 1080x1920, H.264, AAC |
| Publish results | SharePoint Publishing_Analytics | candidate_id, platform, post_id, status, published_at, niche_id |
| Engagement metrics | SharePoint Analytics | post_id, platform, metrics, metrics_fetched |
| Pending feedback | SharePoint PendingFeedback | content_id, platform, published_at, platform_post_id |
| Engagement replies | `.engagement_replied.jsonl` | comment_id, platform |
| Bandit state | SharePoint {Niche}_BanditArms | arm_id, A_matrix, b_vector, n_obs |
| Run reports | `.tmp/runs/{run_id}/run_report.json` | per-stage stats |
| Pipeline logs | `.tmp/{niche_id}_pipeline_logs.jsonl` | per-stage timing, errors |

---

## Critical Path (Happy Path)

```
daily_intel.sh
  -> pipeline_runner.py --niche gaming
    -> FetchTrendingVideos.execute()
      -> TrendingVideoFetcher.fetch_trending("gaming")
        -> YouTube RSS (0 quota) + mostPopular (1 unit) + videos.list (1 unit)
        -> RelevanceFilter + CompositeScorer
      -> context["stories"] = [video_story_1, video_story_2, ...]
    -> WriteGamingContent.execute()
      -> write_video_content(video, "gaming", llm_client)
      -> Claude Haiku -> {hook, ig_caption, yt_content, tw_content, fb_content}
    -> RenderGamingVideo.execute()
      -> FrameCompositor.compose(source_clip, hook, output)
      -> FFmpeg: 1080x1920, logo overlay, hook text, H.264/AAC
    -> ValidateVideos.execute()
      -> ffprobe spec check + VMAF >= 85
    -> PushToBacklog.execute()
      -> SharePoint: create story + blueprint (VISUAL_READY, scheduled_for=06:30 UTC)

dashboard/review_server.py
  -> /api/v1/queue -> shows PENDING_APPROVAL items
  -> /api/v1/queue/:id/approve -> action_taken=approved, scheduled_for=next_slot

publish_all_platforms.py --niche gaming
  -> fetch VISUAL_READY + approved blueprints
  -> schedule gate + video gate + daily cap + niche filter
  -> _dispatch_niche_aware([("instagram", payload), ("youtube", payload), ...])
    -> ThreadPoolExecutor: IG + YT + FB + X concurrently
  -> status -> PUBLISHED
  -> log_publish_result() to Publishing_Analytics
  -> PendingFeedbackTask created per platform

FetchInsights.execute() (next day's pipeline)
  -> query Publishing_Analytics for posts 6h-7d old
  -> fetch IG/YT/FB/X metrics per post
  -> mark metrics_fetched

PerformanceLearner.execute()
  -> RewardShaper.compute_reward(metrics)
  -> LinUCBBandit.update(arm, context, reward)
  -> config_writer.update_schedule_from_bandit()
```
