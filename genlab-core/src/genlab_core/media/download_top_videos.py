"""Download videos for top-N ranked stories using VideoSourcer fallback chain.

Two execution modes:
    1. **CLI** — called from BB's daily_intel.sh::

           python -m genlab_core.media.download_top_videos \
               --run-id RUN_ID --niche ai_creators

    2. **Pipeline stage** — loaded by GenericPipelineRunner from niche.yaml::

           pipeline:
             stages:
               - class: genlab_core.media.download_top_videos.DownloadTopVideos
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from genlab_core.config.tuning import get_tuning_config  # noqa: E402


# 2026-07-22 late: WARP flap resilience via stage-runner retry.
#
# `LocalStageRunner` in `pipeline/stage_runner.py:164` fires the
# `retries: N, retry_delay_seconds: N` config from niche.yaml ONLY on
# `Exception`. `DownloadTopVideos.execute()` used to catch every download
# error internally (returns `{success: False, error: ...}` in the entries
# dict), never raising. Result: `retries: 1, retry_delay_seconds: 30` at
# `pipeline_template.yaml:92` has been a DEAD KNOB since it landed.
#
# This exception makes the knob live. `execute()` raises after the download
# loop when EVERY download failed AND at least one carries a SOCKS5-shaped
# error string (Host unreachable, SOCKS5, connection refused). The stage
# runner then triggers its retry with the configured delay, giving WARP
# a chance to recover before the pipeline gives up.
class ProxyOutageDetected(RuntimeError):
    """All downloads failed with SOCKS5-shaped errors — likely WARP outage.

    Raised from `DownloadTopVideos.execute()` when 100% of attempted
    downloads returned SOCKS5-family failures. Caught + retried by
    `LocalStageRunner` per the stage's `retries:` config in niche.yaml.

    Passing this to the runner's retry path (rather than catching it
    internally) is the whole reason the `retries: N, retry_delay_seconds: N`
    config in the pipeline template exists — before this fix that config
    was dormant because `execute()` never raised.
    """


# Match tokens that indicate a proxy-layer failure vs a real YouTube block
# or a per-video 404. Anchored to lowercase substring match.
_SOCKS5_ERROR_TOKENS: tuple[str, ...] = (
    "host unreachable",
    "socks5",
    "connection refused",
    "connect timeout",
    "errno 4",  # POSIX EHOSTUNREACH — surfaced by the socket layer under WARP flap
)


# Download-error patterns that indicate the primary URL is blocked (bot
# detection, auth wall, geo-restriction, deleted) rather than a transient
# network/proxy failure. When one of these fires on a ``direct_url`` backend
# we ask VideoSourcer for an alternative (Reddit → TMDB → YouTube search).
#
# Substring match, case-insensitive. Extend when new platform-side blocks
# surface. All entries are backed by a regression test in
# tests/media/test_download_fallback_triggers.py.
#
# 2026-08-06: added "authentication is required" after Reddit locked down
# anonymous v.redd.it downloads. All 5 sports blueprints shipped 0-content
# because ``direct_url`` yt-dlp calls returned "Account authentication is
# required" and the trigger list didn't recognize it → no fallback fired.
# Same class of bug as CLAUDE.md rule #17 (silent handler misses a signal
# that would have unlocked the recovery path).
_DOWNLOAD_FALLBACK_TRIGGERS: tuple[str, ...] = (
    "sign in to confirm",
    "not a bot",
    "private video",
    "video unavailable",
    "not available in your country",
    "authentication is required",  # Reddit v.redd.it + generic auth walls
    "403 client error: blocked",   # Reddit JSON API + Cloudflare-style blocks
)


def _should_try_alternative(err: str | None) -> bool:
    """True if this download error suggests trying an alternative source.

    Pure function, case-insensitive substring match against
    ``_DOWNLOAD_FALLBACK_TRIGGERS``. Empty / None safely returns False —
    the caller only invokes the alternative path when the download
    already failed for SOME reason, so empty-error is a bug elsewhere.
    """
    if not err or not isinstance(err, str):
        return False
    err_lower = err.lower()
    return any(pattern in err_lower for pattern in _DOWNLOAD_FALLBACK_TRIGGERS)


def _is_socks5_shaped_error(err: str) -> bool:
    """True if the error string looks like a proxy/WARP failure.

    Explicitly EXCLUDES per-video errors (404, bot-detection wall,
    signature-decryption failures) — those wouldn't benefit from a
    proxy-recovery retry.
    """
    if not err:
        return False
    low = err.lower()
    return any(token in low for token in _SOCKS5_ERROR_TOKENS)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Niche → YouTube search keywords
# ---------------------------------------------------------------------------
_NICHE_KEYWORDS: dict[str, list[str]] = {
    "ai_creators": ["AI", "artificial intelligence", "machine learning"],
    "anime": ["anime", "manga", "otaku"],
    "movies": ["movie", "film", "cinema", "trailer"],
    "sports": ["sports", "highlights", "game", "match"],
    "gaming": ["gaming", "gameplay", "esports"],
}

# Minimum file size (bytes) for a valid download
_MIN_FILE_SIZE = 100 * 1024  # 100 KB

# Minimum clip duration (seconds). Matches validate_videos.SPEC.min_duration
# so we reject at download-probe time rather than after the compose+render
# pipeline has burned work. See 2026-07-15 Twitch min-duration follow-up
# — same class-of-bug prevention, but source-agnostic (catches Reddit,
# TMDB, and any future fetcher without needing a per-source filter).
_MIN_DURATION_SECONDS = 15.0

# yt-dlp download timeout (seconds)
_DOWNLOAD_TIMEOUT: int = get_tuning_config().download.timeout_seconds


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


def _download_video(url: str, output_path: str) -> dict[str, Any]:
    """Download a video using yt-dlp subprocess.

    Uses the Android/iOS player clients which bypass most bot detection
    (the web client triggers "sign in to confirm you're not a bot" on
    data center IPs). Also reads cookies from .youtube_cookies.txt if
    present, for videos that still require authentication.

    Returns:
        {"success": bool, "duration": float, "error": str}
    """
    project_root = os.environ.get("GENLAB_PROJECT_ROOT", "/opt/genlab")

    # Build extractor_args — combine player_client list with visitor_data if
    # we have it from a real browser session (bypasses bot detection).
    #
    # QB-FIX-01 F2 supplement (2026-08-06): mweb added FIRST to unblock
    # SABR-only streaming. Discovery: with the prior stack (ios,tv,
    # web_safari,android,web), `yt-dlp --list-formats` returned ONLY
    # format 18 (mp4 640x360 240p progressive) for every YouTube URL,
    # regardless of cookie state — even 1590 valid Chrome session cookies
    # + WARP SOCKS proxy could not unlock higher-res formats. This is
    # YouTube's SABR-only + poToken enforcement rolled out mid-2024:
    # web/android/ios clients now REQUIRE a poToken proof-of-origin to
    # serve DASH streams. Progressive format 18 remains as unauthed
    # fallback.
    #
    # `mweb` (mobile web) client uses HLS m3u8 streams that Google still
    # serves without poToken as of 2026-08. A live test on Ramayana
    # trailer (1zip1rNaNYs) with mweb+web_safari returned formats:
    #   91: 256x144, 92: 426x240, 93: 640x360, 94: 854x480,
    #   95: 1280x720, 96: 1920x1080  ← up to 1080p available
    # vs the prior stack which returned only format 18 (640x360).
    # Putting mweb first + keeping the older clients as fallback preserves
    # the SABR-workaround the 2026-05 comment was chasing without
    # regressing on videos where mweb has no formats.
    extractor_args = "youtube:player_client=mweb,web_safari,ios,tv,android,web"
    session_path = os.path.join(project_root, ".youtube_session.json")
    if os.path.exists(session_path):
        try:
            import json as _json

            with open(session_path) as fh:
                session = _json.load(fh)
            visitor_data = session.get("visitor_data", "")
            if visitor_data:
                # yt-dlp accepts visitor_data via extractor-args
                extractor_args += f";visitor_data={visitor_data}"
        except (OSError, ValueError):
            pass

    # QB-FIX-01 F2 (2026-08-06): tiered format selection that prefers
    # >=1080p vertical-usable source.
    #
    # Prior spec was `bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/…`
    # which combined two problems: (a) `height<=1080` gave yt-dlp a CEILING
    # but no floor, (b) it assumed DASH-split streams available, which
    # SABR+poToken enforcement has removed from most YouTube URLs. Every
    # measured source clip landed at 640x360 (single-file baked-audio mp4
    # fallback = legacy format 18), upscaled to 1080x1920 for the reel —
    # losing all high-frequency detail before the encoder ran. Audit
    # F-QB-0101 traced the 0.9-2.4 Mbps output bitrates to this softness.
    #
    # F2 supplement: with the `mweb` client added to player_client (above),
    # YouTube serves HLS m3u8 single-file streams up to 1080p (formats 91-
    # 96). These are muxed video+audio, so `best[height>=X]` matches them
    # rather than `bestvideo+bestaudio` (which wants split streams). Tier
    # order below prefers highest single-file first, then falls through
    # to split streams (which may still be served by web_safari with valid
    # cookies), then to best-available as last resort.
    #
    # Tier order:
    #   1) single-file best >=1080p (mweb HLS m3u8 — the SABR-friendly path)
    #   2) split-stream best >=1080p mp4+m4a (DASH — needs valid poToken)
    #   3) single-file best >=720p (mweb HLS)
    #   4) split-stream best >=720p mp4+m4a
    #   5) single-file best >=480p (progressive/HLS)
    #   6) any best (last resort — accept low-res 240p+ fallback)
    # `--print after_move:[F2] ...` logs which format tier fired per clip.
    cmd = [
        "yt-dlp",
        "-f",
        (
            "best[height>=1080]/"
            "bestvideo[height>=1080][ext=mp4]+bestaudio[ext=m4a]/"
            "best[height>=720]/"
            "bestvideo[height>=720][ext=mp4]+bestaudio[ext=m4a]/"
            "best[height>=480]/"
            "best"
        ),
        "--print",
        "after_move:[F2] format=%(format_id)s res=%(resolution)s "
        "vcodec=%(vcodec)s acodec=%(acodec)s vbr=%(vbr)s abr=%(abr)s",
        "-o",
        output_path,
        "--no-playlist",
        # 2026-07-22 WARP-flap safety net: bumped from 30→60 sec socket timeout
        # and 2→4 retries. Today's 09:00 IST + 15:51 IST WARP outages killed
        # 100% of movies-pipeline downloads because SOCKS5 host-unreachable
        # errors exhausted retries in ~90 sec while WARP was down for 20-30
        # min. yt-dlp's built-in backoff between retries scales up to ~30 sec
        # each, so 4 retries at 60s timeout gives ~5 min coverage — catches
        # short flaps without punishing the healthy path. Longer WARP outages
        # still hit the ERROR path at line 799 → rule #26 exit-code 2 →
        # OnFailure alert. Deeper defense (raise-on-SOCKS5 to trigger the
        # stage-runner retry loop; template retry_delay_seconds 30→180)
        # deferred to a dedicated session per 2026-07-22 audit.
        "--socket-timeout",
        "60",
        "--retries",
        "4",
        "--extractor-args",
        extractor_args,
        # User agent matching a real Android YouTube app
        "--user-agent",
        "com.google.android.youtube/19.09.37 (Linux; U; Android 14) gzip",
        # Sleep between requests to avoid triggering rate limit / bot detection
        "--sleep-requests",
        "2",
        "--sleep-interval",
        "5",
        "--max-sleep-interval",
        "15",
    ]

    # Route through Cloudflare WARP SOCKS proxy when available.
    # WARP uses Cloudflare's network which has better IP reputation than
    # data-center IPs, bypassing YouTube's "Sign in to confirm" wall.
    warp_proxy = os.environ.get("YT_DLP_PROXY", "")
    if not warp_proxy and os.path.exists("/run/cloudflare-warp"):
        # Default WARP SOCKS proxy port (we configured this to 40000)
        warp_proxy = "socks5://127.0.0.1:40000"
    if warp_proxy:
        cmd.extend(["--proxy", warp_proxy])

    cmd.append(url)

    # Use cookies if available. .youtube_cookies.txt should contain at least
    # __Secure-3PAPISID and PREF (captured from a fresh browser session)
    cookies_path = os.path.join(project_root, ".youtube_cookies.txt")
    has_real_cookies = False
    if os.path.exists(cookies_path):
        try:
            with open(cookies_path) as fh:
                content = fh.read()
            has_real_cookies = any(
                line.strip() and not line.startswith("#") for line in content.splitlines()
            )
        except OSError:
            pass
        if has_real_cookies:
            cmd.extend(["--cookies", cookies_path])

    # Use browser TLS impersonation via curl_cffi (real Chrome fingerprint)
    try:
        import curl_cffi  # noqa: F401

        cmd.extend(["--impersonate", "chrome-136"])
    except ImportError:
        pass
    t0 = time.monotonic()
    ytdlp_error = ""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_DOWNLOAD_TIMEOUT,
        )
        elapsed = time.monotonic() - t0
        if result.returncode == 0:
            # QB-FIX-01 F2: surface the `[F2] format=... res=...` line yt-dlp
            # emits via `--print after_move` so the pipeline log records
            # which format tier fired per download. Without this the print
            # output is silently discarded on success.
            for _line in (result.stdout or "").splitlines():
                if "[F2]" in _line:
                    logger.info("[download] %s", _line.strip())
            return {"success": True, "duration": elapsed, "error": ""}
        ytdlp_error = (result.stderr or result.stdout or "unknown error").strip()
        if len(ytdlp_error) > 500:
            ytdlp_error = ytdlp_error[:500] + "..."
        logger.warning("yt-dlp failed for %s: %s", url, ytdlp_error[:200])
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        ytdlp_error = "download timed out"
        logger.warning("yt-dlp timed out after %ds for %s", _DOWNLOAD_TIMEOUT, url)
    except FileNotFoundError:
        elapsed = time.monotonic() - t0
        logger.error("yt-dlp not found in PATH")
        # No fallback possible if the subprocess can't even start.
        return {"success": False, "duration": elapsed, "error": "yt-dlp not installed"}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        ytdlp_error = str(exc)
        logger.warning("yt-dlp unexpected error for %s: %s", url, exc)

    # yt-dlp lost. There is currently no second leg for YouTube downloads;
    # the public Cobalt API was shut down on 2024-11-11 (see issue #860 on
    # imputnet/cobalt) and self-hosting Cobalt is the only legitimate path
    # to a real second downloader. That's an operator decision (extra
    # service to maintain) not made yet, so we fail cleanly here rather
    # than masking the gap. The wave 7 client reorder
    # (ios,tv,web_safari,android,web) is the resilience we get inside the
    # single yt-dlp call — yt-dlp itself tries those clients in sequence.
    return {"success": False, "duration": elapsed, "error": ytdlp_error}


def _probe_duration(path: str) -> float:
    """Get video duration in seconds using ffprobe.

    Returns 0.0 on any failure.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as exc:
        # 2026-07-14 (video audit F6): dropped bare `Exception` from
        # the tuple. Bare-Exception plus specific parent exceptions
        # was redundant AND, per audit, could swallow non-Exception
        # BaseException on some Python versions. Kept specific-only
        # + elevated to WARNING (was DEBUG). A ffprobe failure on
        # a downloaded video is real — either infra broken (missing
        # binary, PATH issue) or the download is corrupt (yt-dlp
        # wrote to unexpected extension). Silent 0.0 masked both.
        logger.warning("ffprobe failed for %s: %s", path, exc)
    return 0.0


def _has_video_stream(path: str) -> bool:
    """Return True if the file contains at least one video stream."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0 and "video" in (result.stdout or "").lower()
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        # 2026-07-14 (video audit F6): specific catches + WARNING.
        # Silent False on ffprobe failure masked infra bugs as
        # content bugs — the download appeared to have "no video
        # stream" when ffprobe itself was broken.
        logger.warning("_has_video_stream ffprobe failed for %s: %s", path, exc)
        return False


def _validate_download(path: str) -> dict[str, Any]:
    """Validate a downloaded video file.

    Checks:
        1. File exists
        2. File size > 100 KB
        3. Has a video stream (ffprobe)
        4. Duration >= 15s (validate_videos.SPEC.min_duration)

    Returns:
        {"valid": bool, "reason": str, "duration_seconds": float}
    """
    p = Path(path)
    if not p.exists():
        return {"valid": False, "reason": "file not found", "duration_seconds": 0.0}
    if p.stat().st_size < _MIN_FILE_SIZE:
        return {
            "valid": False,
            "reason": f"file too small ({p.stat().st_size} bytes)",
            "duration_seconds": 0.0,
        }
    if not _has_video_stream(path):
        return {"valid": False, "reason": "no video stream", "duration_seconds": 0.0}

    duration = _probe_duration(path)
    # 2026-07-15: reject clips shorter than the platform min_duration
    # here, at probe time, rather than after compose+render burns work
    # and leaves a stuck DRAFTED blueprint. Source-agnostic — catches
    # short Twitch clips, short Reddit videos, TMDB trailer teasers,
    # and any future fetcher regardless of whether it reports duration
    # upstream. Duration=0 is preserved as a legit ffprobe-failure
    # signal (not treated as "too short") so callers can distinguish
    # a probe error from a genuinely-short clip.
    if 0 < duration < _MIN_DURATION_SECONDS:
        return {
            "valid": False,
            "reason": f"too_short:{duration:.1f}s (min {_MIN_DURATION_SECONDS:.0f}s)",
            "duration_seconds": duration,
        }
    return {"valid": True, "reason": "", "duration_seconds": duration}


# ---------------------------------------------------------------------------
# Core download function
# ---------------------------------------------------------------------------


def download_videos_for_stories(
    stories: list[dict[str, Any]],
    run_dir: str | Path,
    niche_id: str,
    max_stories: int = 10,
    youtube_api_key: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Download videos for top-N ranked stories.

    Args:
        stories: List of story dicts (must have ``story_id``, ``title``).
        run_dir: Directory for this run's artifacts.
        niche_id: Niche identifier for keyword/subreddit selection.
        max_stories: Maximum number of stories to process.
        youtube_api_key: Optional YouTube Data API key override.

    Returns:
        Dict mapping story_id to clip entry dict with keys:
        ``story_id``, ``success``, ``clip_path``, ``source_url``,
        ``backend``, ``duration_seconds``, ``error``.
    """
    from genlab_core.media.video_sourcer import VideoSourcer

    run_dir = Path(run_dir)
    clips_dir = run_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    # Video-first: prioritise stories that already have a video URL
    # (from FetchTrendingVideos) before applying the top-N cutoff.
    # Without this, RSS-sourced stories can outscore trending clips
    # and push them past the limit — wasting the whole pipeline run.
    video_first = [s for s in stories if s.get("_trending_video")]
    rest = [s for s in stories if not s.get("_trending_video")]
    ordered = video_first + rest
    top_stories = ordered[:max_stories]

    keywords = _NICHE_KEYWORDS.get(niche_id, [])
    sourcer = VideoSourcer(
        niche_id=niche_id,
        niche_keywords=keywords,
        youtube_api_key=youtube_api_key,
    )

    # Per-story work is fully self-contained — source URL, download, fallback,
    # validate — so we fan out across a ThreadPoolExecutor. Each yt-dlp call
    # is subprocess-bound (no GIL contention) and ~50 MB RSS; 4-way concurrency
    # cuts the per-run wall-clock by ~3-4x in practice. Configured via
    # tuning.yaml::download.parallel_workers (capped at 8 by the schema).
    parallel_workers = get_tuning_config().download.parallel_workers
    total = len(top_stories)
    entries: dict[str, dict[str, Any]] = {}

    if parallel_workers <= 1 or total <= 1:
        # Sequential path — preserved for ops who want the old behaviour
        # (set parallel_workers: 1 in tuning.yaml) and for the 1-story case
        # where pool overhead isn't worth it.
        for i, story in enumerate(top_stories, 1):
            story_id, entry = _process_one_story(story, i, total, sourcer, clips_dir)
            entries[story_id] = entry
    else:
        with ThreadPoolExecutor(
            max_workers=parallel_workers,
            thread_name_prefix="dl",
        ) as pool:
            futures = [
                pool.submit(_process_one_story, story, i, total, sourcer, clips_dir)
                for i, story in enumerate(top_stories, 1)
            ]
            for fut in as_completed(futures):
                # Each worker catches its own per-story exceptions and returns
                # a "failed" entry — fut.result() raising would mean a coding
                # bug in _process_one_story itself, which we want to surface.
                story_id, entry = fut.result()
                entries[story_id] = entry

    # Log sourcer stats (counters incremented inside per-story workers;
    # diagnostic only, so the small under-count risk from un-locked += isn't
    # worth the lock contention).
    stats = sourcer.get_stats()
    logger.info(
        "VideoSourcer stats: %s",
        ", ".join(f"{k}={v}" for k, v in stats.items()),
    )

    return entries


def _process_one_story(
    story: dict[str, Any],
    story_index: int,
    total: int,
    sourcer: Any,  # VideoSourcer — typed as Any to avoid the top-level import cost.
    clips_dir: Path,
) -> tuple[str, dict[str, Any]]:
    """Source + download + validate one story.

    Fully self-contained so it can be safely run inside a ThreadPoolExecutor.
    Returns ``(story_id, entry_dict)`` — the caller writes the dict into the
    shared ``entries`` map keyed by ``story_id``.
    """
    story_id = str(story.get("story_id", "")).strip()
    title = story.get("title", "untitled")
    if not story_id:
        # Generate stable story_id from title hash — niche strategies
        # don't always set story_id before the download stage.
        import hashlib

        story_id = hashlib.sha256(title.encode()).hexdigest()
        story["story_id"] = story_id
        logger.debug("Story %d: generated story_id from title hash", story_index)

    logger.info(
        "[%d/%d] Sourcing video for: %s (story_id=%s)",
        story_index,
        total,
        title[:60],
        story_id[:12],
    )

    # Find a video URL via the fallback chain
    try:
        result = sourcer.find_video_for_story(story)
    except Exception as exc:
        logger.warning("VideoSourcer error for story %s: %s", story_id[:12], exc)
        return story_id, {
            "story_id": story_id,
            "success": False,
            "clip_path": "",
            "source_url": "",
            "backend": "",
            "duration_seconds": 0.0,
            "error": f"sourcer error: {exc}",
        }

    if result is None:
        logger.info("  No video found for story %s", story_id[:12])
        return story_id, {
            "story_id": story_id,
            "success": False,
            "clip_path": "",
            "source_url": "",
            "backend": "",
            "duration_seconds": 0.0,
            "error": "no video found",
        }

    video_url = result.url
    backend = result.backend
    logger.info("  Found video via %s: %s", backend, video_url[:80])

    # Download the video — story_id prefix keeps filenames unique even when
    # workers race.
    safe_id = story_id[:16].replace("/", "_")
    output_path = str(clips_dir / f"{safe_id}.mp4")

    dl_result = _download_video(video_url, output_path)

    # FALLBACK: if the primary URL failed with a "blocked / auth wall /
    # geo-restricted / deleted" shape, ask VideoSourcer for an alternative
    # backend and retry once. Pattern list in _DOWNLOAD_FALLBACK_TRIGGERS —
    # extend that constant, not this call site, when new block classes appear.
    if not dl_result["success"]:
        err = dl_result.get("error", "")
        if _should_try_alternative(err) and backend == "direct_url":
            logger.info(
                "  Primary URL failed (%s) — trying alternative source",
                err[:50],
            )
            try:
                alt_result = sourcer.source_alternative(story, exclude_url=video_url)
            except AttributeError:
                alt_result = None
            except Exception as exc:
                logger.warning("  Alternative source failed: %s", exc)
                alt_result = None

            if alt_result is not None and alt_result.url != video_url:
                video_url = alt_result.url
                backend = alt_result.backend
                logger.info(
                    "  Retrying with %s: %s",
                    backend,
                    video_url[:80],
                )
                dl_result = _download_video(video_url, output_path)

    if not dl_result["success"]:
        return story_id, {
            "story_id": story_id,
            "success": False,
            "clip_path": "",
            "source_url": video_url,
            "backend": backend,
            "duration_seconds": 0.0,
            "error": dl_result["error"],
        }

    # yt-dlp may add format extension — find the actual file
    actual_path = _find_downloaded_file(output_path)
    if not actual_path:
        return story_id, {
            "story_id": story_id,
            "success": False,
            "clip_path": "",
            "source_url": video_url,
            "backend": backend,
            "duration_seconds": 0.0,
            "error": "downloaded file not found on disk",
        }

    validation = _validate_download(actual_path)

    if not validation["valid"]:
        logger.warning(
            "  Validation failed for %s: %s",
            actual_path,
            validation["reason"],
        )
        return story_id, {
            "story_id": story_id,
            "success": False,
            "clip_path": "",
            "source_url": video_url,
            "backend": backend,
            "duration_seconds": 0.0,
            "error": f"validation: {validation['reason']}",
        }

    logger.info(
        "  Downloaded: %s (%.1fs, %s)",
        Path(actual_path).name,
        validation["duration_seconds"],
        backend,
    )

    return story_id, {
        "story_id": story_id,
        "success": True,
        "clip_path": actual_path,
        "source_url": video_url,
        "backend": backend,
        "duration_seconds": validation["duration_seconds"],
        "error": "",
    }


def _find_downloaded_file(expected_path: str) -> str | None:
    """Find the actual downloaded file, handling yt-dlp extension variations.

    yt-dlp sometimes appends format suffixes or uses different extensions.
    """
    p = Path(expected_path)
    if p.exists():
        return str(p)

    # Check common yt-dlp output variations
    parent = p.parent
    stem = p.stem
    for ext in (".mp4", ".mkv", ".webm"):
        candidate = parent / f"{stem}{ext}"
        if candidate.exists():
            return str(candidate)

    # Glob for anything with the same stem
    matches = list(parent.glob(f"{stem}.*"))
    if matches:
        # Prefer mp4
        for m in matches:
            if m.suffix == ".mp4":
                return str(m)
        return str(matches[0])

    return None


# ---------------------------------------------------------------------------
# Clip index builder
# ---------------------------------------------------------------------------


def build_clip_index(
    run_id: str,
    entries: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a clip_index.json structure.

    Args:
        run_id: Run identifier.
        entries: Dict mapping story_id to clip entry dicts.

    Returns:
        {
            "run_id": str,
            "videos_total": int,
            "videos_downloaded": int,
            "videos_failed": int,
            "clips": {story_id: entry_dict, ...}
        }
    """
    total = len(entries)
    downloaded = sum(1 for e in entries.values() if e.get("success"))
    failed = total - downloaded

    return {
        "run_id": run_id,
        "videos_total": total,
        "videos_downloaded": downloaded,
        "videos_failed": failed,
        "clips": entries,
    }


# ---------------------------------------------------------------------------
# Pipeline stage class
# ---------------------------------------------------------------------------


class DownloadTopVideos:
    """Pipeline stage for GenericPipelineRunner.

    Loaded via niche.yaml::

        pipeline:
          stages:
            - class: genlab_core.media.download_top_videos.DownloadTopVideos
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Download videos for top-N ranked stories.

        Reads from context:
            - ``stories``: list of story dicts
            - ``run_dir``: path to run directory (str or Path)
            - ``niche_id``: niche identifier
            - ``niche_config.pipeline.max_items_per_run``: max stories to process

        Sets on context:
            - ``clip_index``: the built clip_index dict
            - ``clip_index_path``: path to written clip_index.json
        """
        stories = context.get("stories", [])
        run_dir = context.get("run_dir", "")
        niche_id = context.get("niche_id", "")

        config = context.get("niche_config", context.get("config", {}))
        max_stories = config.get("pipeline", {}).get("max_items_per_run", 10)

        if not stories:
            logger.warning("[DownloadTopVideos] No stories in context")
            context["clip_index"] = build_clip_index(
                context.get("run_id", "unknown"),
                {},
            )
            context["clip_index_path"] = ""
            return context

        if not run_dir:
            logger.error("[DownloadTopVideos] No run_dir in context")
            context["clip_index"] = build_clip_index(
                context.get("run_id", "unknown"),
                {},
            )
            context["clip_index_path"] = ""
            return context

        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        entries = download_videos_for_stories(
            stories=stories,
            run_dir=run_dir,
            niche_id=niche_id,
            max_stories=max_stories,
        )

        run_id = context.get("run_id", run_dir.name)
        clip_index = build_clip_index(run_id, entries)

        clip_index_path = run_dir / "clip_index.json"
        with open(clip_index_path, "w") as f:
            json.dump(clip_index, f, indent=2)
        logger.info(
            "[DownloadTopVideos] Wrote clip_index.json: %d/%d downloaded",
            clip_index["videos_downloaded"],
            clip_index["videos_total"],
        )

        context["clip_index"] = clip_index
        context["clip_index_path"] = str(clip_index_path)

        # 2026-07-22 late: activate the stage-runner retry knob on
        # all-SOCKS5 failure. When every attempted download hit a
        # SOCKS5-shaped error, the culprit is almost always WARP flap
        # (as verified by the 09:00 IST + 15:51 IST movies-pipeline
        # incidents earlier today). Raising here triggers the
        # `retries: N, retry_delay_seconds: N` config in
        # `pipeline_template.yaml:92` — retry gives WARP a chance to
        # recover before we give up on the whole run. If the second
        # attempt still returns all-SOCKS5, we raise again → stage
        # runner marks failed → rule #26 exit-code 2 → operator paged.
        #
        # We only raise when EVERY entry has a SOCKS5-shaped error. A
        # partial success (some downloaded, some hit SOCKS5) is treated
        # as a normal partial result — those often mean per-video quirks
        # rather than a systemic proxy outage.
        if entries and clip_index["videos_downloaded"] == 0:
            all_socks5 = all(
                _is_socks5_shaped_error(entry.get("error") or "")
                for entry in entries.values()
                if not entry.get("success")
            )
            if all_socks5:
                logger.error(
                    "[DownloadTopVideos] All %d downloads failed with "
                    "SOCKS5-shaped errors — raising ProxyOutageDetected to "
                    "trigger stage-runner retry. WARP proxy likely down.",
                    len(entries),
                )
                raise ProxyOutageDetected(
                    f"All {len(entries)} downloads failed with SOCKS5 errors "
                    f"(likely WARP outage). Triggering stage-runner retry."
                )

        return context

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """Alias for execute() — provided for callers using 'run' convention."""
        return self.execute(context)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for download_top_videos."""
    parser = argparse.ArgumentParser(
        description="Download videos for top-N ranked stories",
    )
    parser.add_argument("--run-id", required=True, help="Unique run identifier")
    parser.add_argument("--niche", default="ai_creators", help="Niche ID")
    parser.add_argument(
        "--max-stories",
        type=int,
        default=10,
        help="Maximum stories to download videos for",
    )
    parser.add_argument(
        "--project-dir",
        default=os.environ.get("GENLAB_PROJECT_DIR", "."),
        help="Project root directory",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    project_dir = Path(args.project_dir).resolve()
    run_dir = project_dir / ".tmp" / "runs" / args.run_id

    # Read trend_pack.json
    trend_pack_path = run_dir / "trend_pack.json"
    if not trend_pack_path.exists():
        logger.error("trend_pack.json not found at %s", trend_pack_path)
        raise SystemExit(2)

    try:
        with open(trend_pack_path) as f:
            trend_pack = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read trend_pack.json: %s", exc)
        raise SystemExit(2) from exc

    stories = trend_pack.get("stories", [])
    if not stories:
        logger.warning("No stories found in trend_pack.json")
        raise SystemExit(2)

    logger.info(
        "Processing %d stories (max %d) for niche '%s'",
        len(stories),
        args.max_stories,
        args.niche,
    )

    entries = download_videos_for_stories(
        stories=stories,
        run_dir=run_dir,
        niche_id=args.niche,
        max_stories=args.max_stories,
    )

    run_id = args.run_id
    clip_index = build_clip_index(run_id, entries)

    clip_index_path = run_dir / "clip_index.json"
    with open(clip_index_path, "w") as f:
        json.dump(clip_index, f, indent=2)

    _downloaded = clip_index["videos_downloaded"]
    _total = clip_index["videos_total"]
    _failed = clip_index["videos_failed"]
    if _total > 0 and _downloaded == 0:
        # R-11: a total download wipeout (0/N) is a dark-day signal — usually
        # WARP/proxy down or a YouTube block — not routine INFO. Log it at ERROR
        # so the health monitor / alerting (R-01) surfaces it; the pipeline will
        # otherwise produce 0 blueprints silently.
        logger.error(
            "[download] ZERO videos downloaded (0/%d, %d failed) — likely WARP/proxy "
            "down or YouTube block; the pipeline will produce 0 blueprints",
            _total,
            _failed,
        )
    else:
        logger.info(
            "Wrote clip_index.json: %d/%d videos downloaded, %d failed",
            _downloaded,
            _total,
            _failed,
        )


if __name__ == "__main__":
    main()
