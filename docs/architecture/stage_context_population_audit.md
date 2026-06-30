# StageContext field population audit

**Date:** 2026-06-30
**Source of truth:** `genlab-core/src/genlab_core/pipeline/stage_context.py`
**Seeder:** `genlab-core/src/genlab_core/pipeline/pipeline_runner.py` (`GenericPipelineRunner.run_niche`)

## Why this audit exists

PR `2ac8dbb2` ("fix(pipeline): populate context['sources_config']") shipped after weeks of silent
no-op runs caused by `context['sources_config']` being declared in `StageContext` but never
populated by the runner. Every fetcher that read it (`FetchRedditClips`, `FetchScorebat`,
`FetchTMDBTrailers`, `FetchAnimePromos`, `FetchTwitchClips`, `FetchSteamTrailers`) silently
early-returned on the empty dict — diagnostic signature was `stage_timing < 0.001s` in
`metrics.jsonl`.

This file enumerates every declared field, what populates it, what reads it, and its status.
The goal is to surface other latent silent-failure candidates before they cause the next
multi-week outage.

## Field-by-field audit

| Field | Populated at | Read at (representative) | Status |
|---|---|---|---|
| `niche_id` | `pipeline_runner.py:336` (dict literal) | many (push_to_backlog, fetch_*, relevance_gate, etc.) | LIVE |
| `niche_root` | `pipeline_runner.py:337` (dict literal) | `push_to_backlog.py:1087`, `relevance_gate.py:40` | LIVE |
| `run_id` | `pipeline_runner.py:338` (dict literal) | `run_report.py:64`, `download_top_videos.py:661`, `decision_trace.py:204`, BB strategies | LIVE |
| `run_dir` | `pipeline_runner.py:339` (dict literal) | `run_report.py:359`, `base_visual_render.py:145`, `download_top_videos.py:627`, `trending_video_fetcher.py:1581` | LIVE |
| `stories` | `pipeline_runner.py:340` (seed) + many stage writers (`merge_stories`/`replace_stories` in `models.py`, `preflight_dedup`, `qc_gates`, `relevance_gate`, `pre_download_dedup`, `video_gate`, `fetch_twitch_clips`, BB strategies, gaming/sports/movies/anime stages) | many | LIVE |
| `blueprints` | `pipeline_runner.py:341` (seed only) | `validate_videos.py:140` (docstring), `CriticalRush/.../generate_gaming_audio.py:90`, `CriticalRush/.../render_text_overlays.py:39` | LIVE |
| `run_stats` | `pipeline_runner.py:342` (seed) + many stage writers via `setdefault` | many | LIVE |
| `feature_flags` | `pipeline_runner.py:343` (seed only) | `igdb_client.py:145`, `render_gaming_video.py:213`, `extract_gaming_media.py:60` | LIVE (niche-specific consumers only) |
| `niche_config` | `pipeline_runner.py:344` (seed) | many (qc_gates, virality_scoring, validate_videos, preflight_dedup, performance_learner, run_report, etc.) | LIVE |
| `reasoning_trace` | `pipeline_runner.py:362` (seed) + `reasoning_trace.py:107` (append helper) | `reasoning_trace.py:104` | LIVE |
| `metrics` | `pipeline_runner.py:381` | `run_report.py:301`, `push_to_backlog.py:2321`, `relevance_gate.py:102`, `video_gate.py:262` | LIVE |
| `sources_config` | `pipeline_runner.py:353` (via `_load_sources_yaml(niche_root)`) | `fetch_tmdb_trailers.py:124`, `fetch_reddit_clips.py:51`, `fetch_scorebat.py:73`, `fetch_twitch_clips.py:209`, `fetch_anime_promos.py:163`, `fetch_steam_trailers.py:83`, `trending_video_fetcher.py:1342` | LIVE (fixed in `2ac8dbb2`, 2026-06-30) |
| `backlog_client` | **NEVER POPULATED — DEAD** | `fetch_insights.py:78` (graceful fallback: `if not client: return context`) | DEAD — consumer has graceful skip |
| `backlog_config_path` | **NEVER POPULATED** (consumer falls back to `context["niche_root"] / config / lists_config.yaml`) | `push_to_backlog.py:1084` | INTENTIONAL FALLBACK — keep with comment |
| `clip_index` | `download_top_videos.py:635 / 644 / 673` (stage writer) | `push_to_backlog.py:1704`, `video_gate.py:105`, `base_writing.py:461`, BB/CW/SR/FD `visual_render.py` | LIVE (niche-stage populated) |
| `trending_steam_app_ids` | **NEVER POPULATED** (consumer falls back to `_DEFAULT_APP_IDS[:max_games]`) | `fetch_steam_trailers.py:99` | INTENTIONAL FALLBACK — keep with comment |
| `trending_game_ids` | **NEVER POPULATED** (consumer falls back to `_DEFAULT_GAME_IDS[:max_games]`) | `fetch_twitch_clips.py:235` | INTENTIONAL FALLBACK — keep with comment |
| `existing_titles` | `push_to_backlog.py:1482` (stage writer) | `push_to_backlog.py:1484` | LIVE (within-stage write+read; could arguably be local) |

## Findings & actions

### Candidate for removal (DEAD field with no harmful consumer)

1. **`backlog_client: BacklogClient | None`** — declared in `StageContext`, **zero writers**, single
   reader at `fetch_insights.py:78` which gracefully skips when missing (`if not client:
   return context`). This means `FetchInsights` has been a permanent no-op in every production
   run since the field was declared. Two possible fixes:

   - **(A) Remove the field + delete the stage's no-op consumer.** This makes the dead branch
     visible. If FetchInsights actually needs to do work, it should construct its own
     BacklogClient inline (the same pattern `engagement/comment_processor.py:_get_backlog_client`
     uses). Recommended.
   - **(B) Wire population in `pipeline_runner.py`** with a lazy construction. Riskier because
     it would suddenly start hitting SharePoint on every pipeline run for every niche, and
     `FetchInsights` was never observed in production — there may be a reason it was disabled.

   COMMIT 2 will take approach **(A)**: remove the field from `StageContext`, remove the
   unused `BacklogClient` TYPE_CHECKING import, and leave `FetchInsights` consumer untouched
   (it already gracefully skips). A separate decision on whether FetchInsights should be
   re-enabled is out of scope for this hardening commit.

### Candidates to KEEP with explicit "intentional fallback" comments

These fields are declared, never written by the runner, and have consumers that **silently
fall back to sensible defaults** when missing. They are not bugs — they exist for cases
where a future caller wants to inject overrides. Comment them as such so the next audit
doesn't flag them as dead.

2. **`backlog_config_path: str`** — `push_to_backlog.py:1078-1087` reads it as an explicit
   override; falls back to `context["niche_root"] / config / lists_config.yaml`. Keep.
3. **`trending_steam_app_ids: list[str]`** — `fetch_steam_trailers.py:99` reads it, falls
   back to `_DEFAULT_APP_IDS[:max_games]`. Keep — the contract is "if upstream gaming
   discovery fills this in, prefer it over hardcoded defaults".
4. **`trending_game_ids: list[str]`** — `fetch_twitch_clips.py:235` reads it, falls
   back to `_DEFAULT_GAME_IDS[:max_games]`. Same shape as `trending_steam_app_ids`. Keep.

### Niche-specific stage-populated fields (LIVE, leave alone)

5. **`clip_index: dict[str, Any]`** — populated by `download_top_videos.py` (a stage that
   runs for all 5 niches via `DownloadTopVideos`); read by `push_to_backlog`, `video_gate`,
   `base_writing`, and all four channel `visual_render.py` strategies. Live and load-bearing.
6. **`existing_titles: list[str]`** — within-stage state of `push_to_backlog` (line 1482
   writes, line 1484 reads). Arguably could be a local variable, but harmless as-is.

## Summary

- **18 declared fields total.**
- **1 DEAD field to remove**: `backlog_client`.
- **3 intentional-fallback fields to comment**: `backlog_config_path`, `trending_steam_app_ids`,
  `trending_game_ids`.
- **No other silent-fail candidates found** — every other field is either seeded by the
  runner or written explicitly by a stage that runs in production.

The fact that only one DEAD field surfaced (and it has a graceful-skip consumer rather than
a silent no-op like `sources_config` did) means the `sources_config` bug pattern does NOT
have known siblings sitting in the codebase today.

To prevent future regressions of this same pattern, COMMIT 3 adds a
`check_fetcher_stage_silent_failures()` health monitor that scans recent `metrics.jsonl`
files for fetcher stages running in <1ms across multiple consecutive runs.
