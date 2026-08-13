"""4-tier automatic gaming clip sourcer.

Waterfall: Steam → YouTube → Twitch → Pexels.
Each tier returns None on failure — never raises, always falls through.

Usage:
    sourcer = GamingClipSourcer.from_config(project_root)
    result = sourcer.source_clip(game_title="Elden Ring", steam_app_id="1245620")
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ClipResult(BaseModel):
    """Result of a successful clip sourcing operation."""

    file_path: str
    source_tier: str  # "steam", "youtube", "twitch", "pexels"
    source_url: str
    duration_seconds: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    aspect_ratio: str = ""
    attribution: str | None = None


class ClipSourcerConfig(BaseModel):
    """Configuration for the clip sourcer, loaded from sources.yaml."""

    max_duration_seconds: int = Field(default=60)
    # 2026-08-12: default was 5, allowing 6-9s clips to reach
    # RenderGamingVideo then hard-fail at ValidateVideos (which
    # enforces SPEC.min_duration = 15.0). Sibling code path
    # `genlab_core.pipeline.stages.fetch_twitch_clips._MIN_CLIP_DURATION_SECONDS`
    # already correctly defaulted to 15; this default now agrees.
    # Niche configs can still override via sources.yaml if they
    # have a legitimate reason for a different value.
    min_duration_seconds: int = Field(default=15)
    target_resolution: str = Field(default="1080")
    trim_start_pct: float = Field(default=0.25)
    output_dir: str = Field(default=".tmp/clips")
    banned_fragments: list[str] = Field(
        default_factory=lambda: ["reaction", "review", "explained", "ranking"]
    )
    pexels_query_template: str = Field(default="{game_title} gameplay")
    youtube_search_template: str = Field(default="{game_title} official trailer")
    twitch_clip_limit: int = Field(default=5)
    min_views: int = Field(default=1000)


# ---------------------------------------------------------------------------
# Scored download gate — reject bad clips before full download
# ---------------------------------------------------------------------------


@dataclass
class StageScore:
    """Score from a single download gate stage."""

    stage: int  # 1-4
    score: float  # 0.0-1.0
    passed: bool
    reason: str = ""
    bytes_downloaded: int = 0


class ScoredDownloadGate:
    """4-stage progressive download gate for bandwidth savings.

    Stages:
        1. Metadata only (0 bytes) — duration, views, banned fragments
        2. Thumbnail (~50KB) — validates video is accessible
        3. Preview (15s at 720p, ~5-20MB) — resolution check
        4. Full download — existing behaviour (not handled here)
    """

    THRESHOLDS = (0.30, 0.50, 0.70, 0.0)  # Stage 1-4

    def __init__(self, config: ClipSourcerConfig):
        self._config = config
        self._min_views = config.min_views

    def stage1_metadata(self, url: str) -> StageScore:
        """Fetch metadata without downloading. Cost: 0 bytes."""
        cmd = [
            "yt-dlp",
            url,
            "--no-download",
            "--print",
            "%(title)s|||%(duration)s|||%(view_count)s|||%(like_count)s",
            "--quiet",
            "--no-warnings",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception as exc:
            return StageScore(stage=1, score=0.0, passed=False, reason=f"metadata cmd error: {exc}")

        if result.returncode != 0:
            return StageScore(stage=1, score=0.0, passed=False, reason="metadata fetch failed")

        parts = result.stdout.strip().split("|||")
        if len(parts) < 4:
            return StageScore(stage=1, score=0.0, passed=False, reason="incomplete metadata")

        title, duration_str, views_str, likes_str = parts[:4]

        try:
            duration = float(duration_str) if duration_str not in ("NA", "None", "") else 0
        except (ValueError, TypeError):
            duration = 0
        try:
            views = int(views_str) if views_str not in ("NA", "None", "") else 0
        except (ValueError, TypeError):
            views = 0
        try:
            likes = int(likes_str) if likes_str not in ("NA", "None", "") else 0
        except (ValueError, TypeError):
            likes = 0

        # Duration filter
        if duration > 0:
            if (
                duration < self._config.min_duration_seconds
                or duration > self._config.max_duration_seconds
            ):
                return StageScore(
                    stage=1,
                    score=0.0,
                    passed=False,
                    reason=f"duration {duration}s outside [{self._config.min_duration_seconds}-{self._config.max_duration_seconds}]",
                )

        # View count filter
        if 0 < views < self._min_views:
            return StageScore(stage=1, score=0.1, passed=False, reason=f"only {views} views")

        # Banned fragment check
        title_lower = title.lower()
        for frag in self._config.banned_fragments:
            if frag in title_lower:
                return StageScore(
                    stage=1, score=0.0, passed=False, reason=f"banned fragment: {frag}"
                )

        # Score: normalized views + likes ratio
        view_score = min(1.0, views / 100_000) if views > 0 else 0.3  # Unknown = moderate
        like_ratio = (likes / views) if views > 0 and likes > 0 else 0.03
        score = 0.5 * view_score + 0.5 * min(1.0, like_ratio / 0.05)
        score = max(0.1, min(1.0, score))

        passed = score >= self.THRESHOLDS[0]
        return StageScore(
            stage=1, score=score, passed=passed, reason=f"views={views} likes={likes}"
        )

    def stage2_thumbnail(self, url: str, output_dir: Path) -> StageScore:
        """Download thumbnail only. Cost: ~50KB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                "yt-dlp",
                url,
                "--write-thumbnail",
                "--skip-download",
                "--convert-thumbnails",
                "jpg",
                "-o",
                f"{tmpdir}/%(id)s.%(ext)s",
                "--quiet",
                "--no-warnings",
            ]
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            except Exception as exc:
                return StageScore(
                    stage=2, score=0.0, passed=False, reason=f"thumbnail cmd error: {exc}"
                )

            thumbs = list(Path(tmpdir).glob("*.jpg"))
            if not thumbs:
                return StageScore(
                    stage=2, score=0.3, passed=False, reason="no thumbnail", bytes_downloaded=0
                )

            size = thumbs[0].stat().st_size
            return StageScore(
                stage=2, score=0.7, passed=True, reason="thumbnail ok", bytes_downloaded=size
            )

    def stage3_preview(self, url: str, output_dir: Path) -> StageScore:
        """Download first 15 seconds at 720p. Cost: ~5-20MB."""
        preview_path = output_dir / "preview_clip.mp4"
        cmd = [
            "yt-dlp",
            url,
            "--download-sections",
            "*0-15",
            "--format",
            "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]",
            "--merge-output-format",
            "mp4",
            "-o",
            str(preview_path),
            "--quiet",
            "--no-warnings",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except Exception as exc:
            return StageScore(stage=3, score=0.0, passed=False, reason=f"preview cmd error: {exc}")

        if result.returncode != 0 or not preview_path.exists():
            return StageScore(stage=3, score=0.0, passed=False, reason="preview download failed")

        size = preview_path.stat().st_size

        # Probe for resolution
        probe = probe_video(str(preview_path))
        preview_path.unlink(missing_ok=True)  # Clean up preview

        if not probe or probe.get("height", 0) < 480:
            return StageScore(
                stage=3, score=0.2, passed=False, reason="resolution too low", bytes_downloaded=size
            )

        score = 0.8 if probe.get("height", 0) >= 720 else 0.6
        passed = score >= self.THRESHOLDS[2]
        return StageScore(
            stage=3, score=score, passed=passed, reason=f"{probe['height']}p", bytes_downloaded=size
        )

    def run_gates(self, url: str, output_dir: Path) -> tuple:
        """Run stages 1-3 in order, short-circuit on failure.

        Returns:
            (passed: bool, stages: list[StageScore])
        """
        stages: list[StageScore] = []

        s1 = self.stage1_metadata(url)
        stages.append(s1)
        if not s1.passed:
            logger.info("[Gate] Stage 1 rejected: %s", s1.reason)
            return False, stages

        s2 = self.stage2_thumbnail(url, output_dir)
        stages.append(s2)
        if not s2.passed:
            logger.info("[Gate] Stage 2 rejected: %s", s2.reason)
            return False, stages

        s3 = self.stage3_preview(url, output_dir)
        stages.append(s3)
        if not s3.passed:
            logger.info("[Gate] Stage 3 rejected: %s", s3.reason)
            return False, stages

        total_bytes = sum(s.bytes_downloaded for s in stages)
        logger.info("[Gate] All gates passed, pre-download bytes: %d", total_bytes)
        return True, stages


# ---------------------------------------------------------------------------
# Tier 1 — Steam trailer (free, best quality for game trailers)
# ---------------------------------------------------------------------------


class SteamTrailerFetcher:
    """Fetch game trailers from Steam's public appdetails API."""

    APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"

    def fetch(self, steam_app_id: str, output_dir: Path) -> str | None:
        if not steam_app_id:
            logger.debug("[Steam] No steam_app_id provided, skipping")
            return None
        try:
            resp = requests.get(
                self.APPDETAILS_URL,
                params={"appids": steam_app_id},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            app_data = data.get(steam_app_id, {})
            if not app_data.get("success"):
                logger.info("[Steam] App %s not found or not public", steam_app_id)
                return None

            movies = app_data.get("data", {}).get("movies", [])
            if not movies:
                logger.info("[Steam] No trailers for app %s", steam_app_id)
                return None

            # Prefer max quality mp4
            movie = movies[0]
            mp4_urls = movie.get("mp4", {})
            video_url = mp4_urls.get("max") or mp4_urls.get("480")
            if not video_url:
                logger.info("[Steam] No MP4 URL in trailer data for app %s", steam_app_id)
                return None

            output_path = output_dir / f"steam_{steam_app_id}.mp4"
            logger.info("[Steam] Downloading trailer for app %s", steam_app_id)
            video_resp = requests.get(video_url, timeout=60, stream=True)
            video_resp.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in video_resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            return str(output_path)

        except Exception as e:
            logger.warning("[Steam] Failed for app %s: %s", steam_app_id, e)
            return None


# ---------------------------------------------------------------------------
# Tier 2 — YouTube trailer search (yt-dlp ytsearch)
# ---------------------------------------------------------------------------


def _yt_dlp_cookies_args() -> list[str]:
    """Return `--cookies /path` args when YT_DLP_COOKIES_FILE env var
    points at a real file. Empty list otherwise (yt-dlp runs without
    auth). Consolidated helper so all 3 yt-dlp call sites in this
    module (direct-URL Tier 0 + YT search info + YT search download)
    stay in lockstep.

    Operator setup (one-time):
      1. Install a browser extension like "Get cookies.txt LOCALLY"
      2. Visit youtube.com while logged in
      3. Export cookies.txt to /opt/genlab/.runtime/yt_cookies.txt
      4. Add YT_DLP_COOKIES_FILE=/opt/genlab/.runtime/yt_cookies.txt to .env
      5. chmod 600 the file; sudo chown genlab:genlab it (rule #15)

    Without this setup, YT downloads fail from datacenter IPs with
    "Sign in to confirm you're not a bot" — verified 2026-08-13.
    Twitch/Steam tiers unaffected (different backends).
    """
    import os as _os

    path = _os.environ.get("YT_DLP_COOKIES_FILE", "").strip()
    if not path:
        return []
    if not Path(path).is_file():
        return []
    return ["--cookies", path]


_BOT_CHECK_MARKERS = (
    "sign in to confirm you're not a bot",
    "sign in to confirm your age",
    "confirm you're not a bot",
    "use --cookies-from-browser or --cookies",
    "http error 429",
)


def _is_bot_check_stderr(stderr: str) -> bool:
    """True when yt-dlp stderr contains YouTube's datacenter-IP bot
    detection signature. Used to route the failure into a WARNING-level
    log path with a stable error_code so cookies-stale conditions surface
    on Mission Control instead of being buried in INFO-level noise.
    Class-of-bug: [[class-of-bug-datacenter-ip-bot-detection]]."""
    low = (stderr or "").lower()
    return any(marker in low for marker in _BOT_CHECK_MARKERS)


def _emit_cookies_stale_alert(niche_id: str, url: str, stderr_tail: str) -> None:
    """Best-effort insert into pipeline_alerts. Dedupe by (niche_id,
    check_name) so we don't fill the table when 10 candidates in a row
    fail from the same stale cookie state. Fail-open: alert emission
    never propagates back into the caller. Mirrors the shape of
    hook_classifier._emit_training_failure_alert (rule #19 sibling)."""
    try:
        import json as _json
        import os as _os

        from genlab_core.storage.tenant_context import pg_connect

        dsn = _os.environ.get("DATABASE_URL", "")
        if not dsn:
            return
        message = (
            f"yt-dlp bot-check hit for {niche_id} — YT_DLP_COOKIES_FILE is "
            f"stale, missing, or unset. Re-run the cookies export "
            f"(Playwright + SCP flow) to unblock. URL sample: {url[:120]}"
        )
        with pg_connect(dsn, niche_id=niche_id, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM pipeline_alerts WHERE check_name = %s "
                    "AND niche_id = %s AND resolved_at IS NULL",
                    ("yt_cookies_stale", niche_id),
                )
                if cur.fetchone():
                    return
                cur.execute(
                    "INSERT INTO pipeline_alerts "
                    "(niche_id, check_name, severity, message, details) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        niche_id,
                        "yt_cookies_stale",
                        "warning",
                        message,
                        _json.dumps({"url": url[:200], "stderr_tail": stderr_tail[:500]}),
                    ),
                )
                conn.commit()
    except Exception:
        pass


_TRADEMARK_CHARS = re.compile(r"[™®©℗℠]")
"""Trademark/copyright/service-mark symbols to strip from search
queries. YouTube's search index doesn't tokenize these consistently
and their presence often returns 0 results."""

_VERBOSE_SUFFIX_PATTERNS = re.compile(
    r"\s+("
    r"LAUNCH\s+TRAILER"
    r"|OFFICIAL\s+(LAUNCH\s+)?TRAILER"
    r"|OFFICIAL\s+(GAME\s+)?(OVERVIEW\s+)?TRAILER"
    r"|OFFICIAL\s+RELEASE"
    r"|OFFICIAL\s+GAMEPLAY(\s+OVERVIEW)?"
    r"|GAMEPLAY\s+TRAILER"
    r"|GAMEPLAY\s+OVERVIEW"
    r"|LEGACY\s+EDITION"
    r"|DEFINITIVE\s+EDITION"
    r"|(REVEAL|ANNOUNCE(MENT)?|CINEMATIC)\s+TRAILER"
    r")\s*$",
    re.IGNORECASE,
)
"""Verbose suffixes that make queries too specific for YT search.
Games are indexed by their common name (e.g., "Dawn of War 4") not
brand-complete titles ("Warhammer 40,000: Dawn of War 4 - Official
Release"). Strip trailing marketing tags. `re.IGNORECASE` handles
"LAUNCH TRAILER" / "Launch Trailer" / "launch trailer" alike."""

_WHITESPACE = re.compile(r"\s+")


def _normalize_search_title(title: str) -> str:
    """Prepare a game title for YouTube search.

    Strips trademark symbols + verbose marketing suffixes + normalizes
    whitespace. Idempotent (running twice gives the same result).

    Examples:
      "The Lord of the Rings™ War in the North™ Legacy Edition LAUNCH TRAILER"
        -> "The Lord of the Rings War in the North"
      "ACE COMBAT 8 The Art of Aircraft Trailer"
        -> "ACE COMBAT 8 The Art of Aircraft"  (no matching suffix)
      "Warhammer 40,000: Dawn of War 4 - Official Release"
        -> "Warhammer 40,000: Dawn of War 4 -"
    """
    if not title:
        return ""
    cleaned = _TRADEMARK_CHARS.sub("", title)
    # Strip verbose suffixes iteratively — some titles have layered
    # suffixes ("LAUNCH TRAILER" preceded by "LEGACY EDITION").
    for _ in range(3):
        stripped = _VERBOSE_SUFFIX_PATTERNS.sub("", cleaned)
        if stripped == cleaned:
            break
        cleaned = stripped
    return _WHITESPACE.sub(" ", cleaned).strip()


class YouTubeTrailerFetcher:
    """Search YouTube for game trailers via yt-dlp."""

    def __init__(
        self,
        banned_fragments: list[str],
        search_template: str,
        gate: ScoredDownloadGate | None = None,
    ):
        self._banned = banned_fragments
        self._search_template = search_template
        self._gate = gate

    def fetch(self, game_title: str, output_dir: Path) -> str | None:
        if not game_title:
            return None
        # Normalize the title before passing to YT search. Live 2026-08-13
        # discovery: gaming pipeline dropped 5/5 stories because titles
        # like "The Lord of the Rings™ War in the North™ Legacy Edition
        # LAUNCH TRAILER" returned 0 YouTube search results. Two failure
        # modes: (a) trademark symbols confuse the search index,
        # (b) verbose suffixes ("Legacy Edition LAUNCH TRAILER") make
        # queries too specific — YT indexes by common names not brand-
        # complete titles. Strip both classes before searching.
        clean_title = _normalize_search_title(game_title)
        try:
            query = self._search_template.format(game_title=clean_title)
            output_path = output_dir / f"yt_{clean_title.replace(' ', '_')[:40]}.mp4"

            [
                "yt-dlp",
                f"ytsearch3:{query}",
                "--format",
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]",
                "--merge-output-format",
                "mp4",
                "--no-playlist",
                "--max-downloads",
                "1",
                "--output",
                str(output_path),
                "--quiet",
                "--no-warnings",
                "--print",
                "%(title)s",
            ]

            # First, get info to check for banned fragments.
            # 2026-08-13: added player_client=ios,web_embedded for the
            # same bot-detection bypass as Tier 0 direct download.
            # YouTube blocks default web-client from datacenter IPs.
            info_cmd = [
                "yt-dlp",
                f"ytsearch3:{query}",
                "--no-download",
                "--extractor-args",
                "youtube:player_client=ios,web_embedded",
                "--print",
                "%(title)s",
                "--quiet",
                "--no-warnings",
            ]
            info_cmd.extend(_yt_dlp_cookies_args())
            info_result = subprocess.run(
                info_cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if info_result.returncode != 0:
                if _is_bot_check_stderr(info_result.stderr or ""):
                    logger.warning(
                        "[YouTube] YT bot-check on search for '%s' — cookies "
                        "stale or missing; stderr tail: %s",
                        game_title,
                        "\n".join((info_result.stderr or "").splitlines()[-2:]),
                    )
                    _emit_cookies_stale_alert(
                        "gaming",
                        f"ytsearch:{game_title}",
                        (info_result.stderr or "")[-500:],
                    )
                else:
                    logger.info("[YouTube] Search returned no results for '%s'", game_title)
                return None

            titles = info_result.stdout.strip().split("\n")
            # Find first title without banned fragments
            clean_title = None
            for title in titles:
                title_lower = title.lower()
                if not any(frag in title_lower for frag in self._banned):
                    clean_title = title
                    break

            if not clean_title:
                logger.info("[YouTube] All results contained banned fragments for '%s'", game_title)
                return None

            # Run scored download gate before full download (if configured)
            search_url = f"ytsearch1:{clean_title}"
            if self._gate:
                passed, stages = self._gate.run_gates(search_url, output_dir)
                if not passed:
                    rejected_at = stages[-1] if stages else None
                    reason = rejected_at.reason if rejected_at else "unknown"
                    logger.info(
                        "[YouTube] Gate rejected '%s' at stage %d: %s",
                        game_title,
                        rejected_at.stage if rejected_at else 0,
                        reason,
                    )
                    return None

            # Download the clean result (bot-check bypass same as info_cmd)
            dl_cmd = [
                "yt-dlp",
                search_url,
                "--format",
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]",
                "--merge-output-format",
                "mp4",
                "--no-playlist",
                "--extractor-args",
                "youtube:player_client=ios,web_embedded",
                "--output",
                str(output_path),
                "--quiet",
                "--no-warnings",
            ]
            dl_cmd.extend(_yt_dlp_cookies_args())
            result = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                if _is_bot_check_stderr(result.stderr or ""):
                    logger.warning(
                        "[YouTube] YT bot-check on download for '%s' — cookies "
                        "stale or missing; stderr tail: %s",
                        game_title, (result.stderr or "")[-200:],
                    )
                    _emit_cookies_stale_alert(
                        "gaming", search_url, (result.stderr or "")[-500:],
                    )
                else:
                    logger.warning(
                        "[YouTube] Download failed for '%s': %s",
                        game_title, result.stderr[:200],
                    )
                return None

            if output_path.exists():
                # YouTube increasingly serves AV1 — normalize to H.264 if needed
                normalized = _normalize_to_h264(str(output_path))
                logger.info("[YouTube] Downloaded trailer for '%s'", game_title)
                return normalized

            return None

        except Exception as e:
            logger.warning("[YouTube] Failed for '%s': %s", game_title, e)
            return None


# ---------------------------------------------------------------------------
# Tier 3 — Twitch clips (Helix API + yt-dlp)
# ---------------------------------------------------------------------------


class TwitchClipFetcher:
    """Fetch top Twitch clips for a game via Helix API."""

    CLIPS_URL = "https://api.twitch.tv/helix/clips"
    GAMES_URL = "https://api.twitch.tv/helix/games"

    def __init__(self, clip_limit: int = 5):
        self._clip_limit = clip_limit
        from genlab_core.settings import settings

        self._client_id = settings.twitch_client_id or ""
        self._client_secret = settings.twitch_client_secret or ""
        self._token: str | None = None
        self._token_expiry: float = 0.0

    def _get_token(self) -> str | None:
        if not self._client_id or not self._client_secret:
            return None
        import time

        if self._token and time.time() < self._token_expiry:
            return self._token
        try:
            resp = requests.post(
                "https://id.twitch.tv/oauth2/token",
                params={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._token_expiry = time.time() + data.get("expires_in", 3600) - 60
            return self._token
        except Exception as e:
            logger.warning("[Twitch] Token fetch failed: %s", e)
            return None

    def _headers(self) -> dict[str, str] | None:
        token = self._get_token()
        if not token:
            return None
        return {
            "Authorization": f"Bearer {token}",
            "Client-Id": self._client_id,
        }

    def fetch(self, game_title: str, igdb_game_id: str | None, output_dir: Path) -> str | None:
        if not game_title:
            return None
        headers = self._headers()
        if not headers:
            logger.debug("[Twitch] No credentials configured, skipping")
            return None

        try:
            # Look up Twitch game ID
            games_resp = requests.get(
                self.GAMES_URL,
                params={"name": game_title},
                headers=headers,
                timeout=10,
            )
            games_resp.raise_for_status()
            games_data = games_resp.json().get("data", [])
            if not games_data:
                logger.info("[Twitch] Game '%s' not found on Twitch", game_title)
                return None

            twitch_game_id = games_data[0]["id"]

            # Fetch top clips
            clips_resp = requests.get(
                self.CLIPS_URL,
                params={"game_id": twitch_game_id, "first": self._clip_limit},
                headers=headers,
                timeout=10,
            )
            clips_resp.raise_for_status()
            clips = clips_resp.json().get("data", [])
            if not clips:
                logger.info("[Twitch] No clips for '%s'", game_title)
                return None

            clip_url = clips[0]["url"]
            output_path = output_dir / f"twitch_{game_title.replace(' ', '_')[:40]}.mp4"

            dl_cmd = [
                "yt-dlp",
                clip_url,
                "--output",
                str(output_path),
                "--quiet",
                "--no-warnings",
            ]
            result = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                logger.warning("[Twitch] yt-dlp failed for clip: %s", result.stderr[:200])
                return None

            if output_path.exists():
                logger.info("[Twitch] Downloaded clip for '%s'", game_title)
                return str(output_path)

            return None

        except Exception as e:
            logger.warning("[Twitch] Failed for '%s': %s", game_title, e)
            return None


# ---------------------------------------------------------------------------
# Tier 4 — Pexels fallback (guaranteed, attribution required)
# ---------------------------------------------------------------------------


class PexelsFallbackFetcher:
    """Search Pexels for generic gaming footage as a last resort."""

    SEARCH_URL = "https://api.pexels.com/videos/search"

    def __init__(self, query_template: str):
        from genlab_core.settings import settings

        self._api_key = settings.pexels_api_key or ""
        self._query_template = query_template

    def fetch(self, game_title: str, output_dir: Path) -> dict[str, Any] | None:
        if not self._api_key:
            logger.debug("[Pexels] No PEXELS_API_KEY set, skipping")
            return None
        try:
            query = self._query_template.format(game_title=game_title)
            resp = requests.get(
                self.SEARCH_URL,
                params={"query": query, "per_page": 3, "orientation": "landscape"},
                headers={"Authorization": self._api_key},
                timeout=15,
            )
            resp.raise_for_status()
            videos = resp.json().get("videos", [])
            if not videos:
                logger.info("[Pexels] No videos for '%s'", game_title)
                return None

            video = videos[0]
            # Pick HD file
            video_files = video.get("video_files", [])
            hd_files = [f for f in video_files if f.get("height", 0) >= 720]
            chosen = hd_files[0] if hd_files else (video_files[0] if video_files else None)
            if not chosen:
                return None

            download_url = chosen["link"]
            output_path = output_dir / f"pexels_{game_title.replace(' ', '_')[:40]}.mp4"

            video_resp = requests.get(download_url, timeout=60, stream=True)
            video_resp.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in video_resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            photographer = video.get("user", {}).get("name", "Unknown")
            pexels_url = video.get("url", "")

            # Pexels clips can be VP9/WebM — normalize to H.264 for render compatibility
            normalized_path = _normalize_to_h264(str(output_path))

            logger.info("[Pexels] Downloaded fallback clip for '%s'", game_title)
            return {
                "file_path": normalized_path,
                "attribution": f"Video by {photographer} on Pexels ({pexels_url})",
                "source_url": pexels_url,
            }

        except Exception as e:
            logger.warning("[Pexels] Failed for '%s': %s", game_title, e)
            return None


# ---------------------------------------------------------------------------
# Video probing and trimming utilities
# ---------------------------------------------------------------------------


def _normalize_to_h264(file_path: str) -> str:
    """Re-encode to H.264/AAC if the clip uses VP9 or AV1 codec.

    Returns the (possibly new) file path. YouTube clips are typically
    already H.264 and skip transcoding.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        codec = result.stdout.strip().lower()
        if codec not in ("vp9", "av1", "vp8"):
            return file_path  # Already H.264 or compatible

        logger.info("[Normalize] Transcoding %s from %s to H.264", file_path, codec)
        normalized_path = file_path.replace(".mp4", "_h264.mp4")
        transcode = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                file_path,
                "-vcodec",
                "libx264",
                "-acodec",
                "aac",
                "-crf",
                "18",
                "-preset",
                "fast",
                "-pix_fmt",
                "yuv420p",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-colorspace",
                "bt709",
                normalized_path,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if transcode.returncode != 0:
            logger.warning(
                "[Normalize] Transcode failed (exit %d): %s",
                transcode.returncode,
                transcode.stderr[:200],
            )
            return file_path  # Fall back to original

        # Replace original with normalized version
        Path(file_path).unlink(missing_ok=True)
        return normalized_path

    except Exception as e:
        logger.warning("[Normalize] Failed for %s: %s", file_path, e)
        return file_path


def probe_video(file_path: str) -> dict[str, Any] | None:
    """Run ffprobe and return width, height, duration, aspect_ratio."""
    try:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            file_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        video_stream = None
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                video_stream = stream
                break

        if not video_stream:
            return None

        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))
        duration = float(data.get("format", {}).get("duration", 0))
        aspect = f"{width}:{height}" if width and height else ""

        # Extract fps from r_frame_rate (e.g. "30/1", "60000/1001")
        fps = 0.0
        r_frame_rate = video_stream.get("r_frame_rate", "0/1")
        try:
            num, den = r_frame_rate.split("/")
            fps = float(num) / float(den) if float(den) > 0 else 0.0
        except (ValueError, ZeroDivisionError):
            pass

        return {
            "width": width,
            "height": height,
            "duration": duration,
            "fps": round(fps, 2),
            "aspect_ratio": aspect,
        }
    except Exception as e:
        logger.warning("ffprobe failed for %s: %s", file_path, e)
        return None


def trim_to_highlight(
    file_path: str, start_pct: float = 0.25, max_duration: float = 60.0
) -> str | None:
    """Trim video starting at start_pct into the video using stream copy."""
    probe = probe_video(file_path)
    if not probe or probe["duration"] <= 0:
        return file_path  # Can't probe — return as-is

    duration = probe["duration"]
    if duration <= max_duration:
        return file_path  # Already short enough

    start_time = duration * start_pct
    clip_duration = min(max_duration, duration - start_time)

    trimmed_path = file_path.replace(".mp4", "_trimmed.mp4")
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_time),
            "-i",
            file_path,
            "-t",
            str(clip_duration),
            "-c",
            "copy",
            trimmed_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.warning("ffmpeg trim failed: %s", result.stderr[:200])
            return file_path

        # Delete the raw file, keep trimmed
        Path(file_path).unlink(missing_ok=True)
        return trimmed_path

    except Exception as e:
        logger.warning("Trim failed for %s: %s", file_path, e)
        return file_path


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class GamingClipSourcer:
    """Orchestrate the 4-tier clip sourcing waterfall."""

    def __init__(self, config: ClipSourcerConfig):
        self.config = config
        self._steam = SteamTrailerFetcher()
        self._gate = ScoredDownloadGate(config)
        self._youtube = YouTubeTrailerFetcher(
            banned_fragments=config.banned_fragments,
            search_template=config.youtube_search_template,
            gate=self._gate,
        )
        self._twitch = TwitchClipFetcher(clip_limit=config.twitch_clip_limit)
        self._pexels = PexelsFallbackFetcher(
            query_template=config.pexels_query_template,
        )

    @classmethod
    def from_config(cls, project_root: Path) -> GamingClipSourcer:
        """Load config from niches/gaming/config/sources.yaml."""
        config_path = project_root / "niches" / "gaming" / "config" / "sources.yaml"
        raw = {}
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                full = yaml.safe_load(f) or {}
            raw = full.get("clip_sourcer", {})
        config = ClipSourcerConfig(**raw)
        return cls(config)

    def _ensure_output_dir(self, project_root: Path) -> Path:
        output = project_root / self.config.output_dir
        output.mkdir(parents=True, exist_ok=True)
        return output

    def _direct_url_fetch(self, url: str, output_dir: Path) -> str | None:
        """Download an exact known video URL via yt-dlp. Used as Tier 0
        when the story already has `download_url` (e.g., from
        FetchTrendingVideos which returns YT trending gaming videos
        with their exact video_id + URL). Bypasses YT search entirely.

        Returns file path on success, None on any failure. Fail-open
        so downstream tiers still fire.
        """
        # Slug the URL into a safe filename
        import hashlib as _hash

        slug = _hash.sha1(url.encode("utf-8")).hexdigest()[:12]
        output_path = output_dir / f"direct_{slug}.mp4"

        # 2026-08-13: YouTube's bot detection blocks default yt-dlp
        # web-client requests from datacenter IPs (Hetzner VPS returns
        # "Sign in to confirm you're not a bot" on every YT URL).
        # Live-tested bypasses:
        #   * player_client=ios,web_embedded — still blocks (verified)
        #   * player_client=web_embedded     — still blocks (verified)
        #   * --cookies /path/to/cookies.txt — WORKS (operator setup)
        #
        # Cookies-file support: when YT_DLP_COOKIES_FILE env var is set
        # and the path exists, yt-dlp uses those cookies which pass
        # YouTube's bot check. Operator exports cookies once via a
        # browser extension (e.g., "Get cookies.txt LOCALLY"), drops
        # the file on prod, sets env var → all YT downloads unlocked.
        # Without cookies, YT tier fails but Twitch/Steam still work.
        cmd = [
            "yt-dlp",
            url,
            "--format",
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]",
            "--merge-output-format",
            "mp4",
            "--no-playlist",
            "--extractor-args",
            "youtube:player_client=ios,web_embedded",
            "--output",
            str(output_path),
            "--quiet",
            "--no-warnings",
        ]
        cmd.extend(_yt_dlp_cookies_args())
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            logger.warning("[DirectURL] Timeout downloading %s", url)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[DirectURL] Failed to download %s: %s", url, exc)
            return None
        if result.returncode != 0:
            stderr_tail = "\n".join((result.stderr or "").strip().splitlines()[-2:])
            if _is_bot_check_stderr(result.stderr or ""):
                logger.warning(
                    "[DirectURL] YT bot-check hit for %s (cookies stale or missing) "
                    "— stderr tail: %s",
                    url, stderr_tail,
                )
                _emit_cookies_stale_alert("gaming", url, stderr_tail)
            else:
                logger.info(
                    "[DirectURL] yt-dlp exit=%d for %s — stderr tail: %s",
                    result.returncode, url, stderr_tail,
                )
            return None
        if not output_path.exists() or output_path.stat().st_size < 1024:
            logger.info(
                "[DirectURL] Empty/missing output for %s (size=%d)",
                url,
                output_path.stat().st_size if output_path.exists() else 0,
            )
            return None
        logger.info("[DirectURL] Downloaded %s -> %s", url, output_path.name)
        return str(output_path)

    def _post_process(self, file_path: str) -> ClipResult | None:
        """Probe, trim, and build a ClipResult."""
        trimmed = trim_to_highlight(
            file_path,
            start_pct=self.config.trim_start_pct,
            max_duration=float(self.config.max_duration_seconds),
        )
        if not trimmed:
            return None

        probe = probe_video(trimmed)
        if not probe:
            # Return with minimal info
            return ClipResult(
                file_path=trimmed,
                source_tier="unknown",
                source_url="",
            )

        return ClipResult(
            file_path=trimmed,
            source_tier="",  # Caller sets this
            source_url="",  # Caller sets this
            duration_seconds=probe["duration"],
            width=probe["width"],
            height=probe["height"],
            fps=probe.get("fps", 0.0),
            aspect_ratio=probe["aspect_ratio"],
        )

    def source_clip(
        self,
        game_title: str,
        steam_app_id: str | None = None,
        igdb_game_id: str | None = None,
        project_root: Path | None = None,
        download_url: str | None = None,
    ) -> ClipResult | None:
        """Try all tiers in order, return first success or None.

        Tier order (2026-08-13 restructure):
          0. Direct download URL (when story already has an exact
             video URL from FetchTrendingVideos). Avoids the fragile
             YT search step entirely when we already know which
             video to download.
          1. Steam trailer via app_id
          2. YouTube search via game_title (fragile — needs canonical
             name; IGDB enrichment helps but often fails on marketing
             titles)
          3. Twitch clips via IGDB id
          4. (Pexels stock footage — permanently disabled per
             clip_sourcer.py:932-937)
        """
        output_dir = self._ensure_output_dir(
            project_root or Path("."),
        )

        # Tier 0: Direct download URL short-circuit (2026-08-13 fix).
        # ROOT ARCHITECTURAL ISSUE: stories from FetchTrendingVideos
        # arrive with `download_url` = the exact YouTube video URL
        # (e.g., https://www.youtube.com/watch?v=vPqLcA9LQMo for
        # "ACE COMBAT 8"). But the historical tier order ignored that
        # and did a fresh `ytsearch3:{title}` — which returns 0 hits
        # for marketing-suffix-laden titles ("The Art of Aircraft
        # Trailer") and drops the candidate. Result: 5-of-7 candidates
        # dropped daily despite already having valid YT URLs.
        #
        # This tier just downloads the known URL via yt-dlp. No search,
        # no matching, no IGDB dependency — the story told us exactly
        # which video it wants.
        if download_url:
            path = self._direct_url_fetch(download_url, output_dir)
            if path:
                result = self._post_process(path)
                if result:
                    result.source_tier = "direct_url"
                    result.source_url = download_url
                    return result

        # Tier 1: Steam
        if steam_app_id:
            path = self._steam.fetch(steam_app_id, output_dir)
            if path:
                result = self._post_process(path)
                if result:
                    result.source_tier = "steam"
                    result.source_url = f"https://store.steampowered.com/app/{steam_app_id}"
                    return result

        # Tier 2: YouTube search (fragile on marketing titles)
        path = self._youtube.fetch(game_title, output_dir)
        if path:
            result = self._post_process(path)
            if result:
                result.source_tier = "youtube"
                result.source_url = f"ytsearch:{game_title}"
                return result

        # Tier 3: Twitch
        if igdb_game_id:
            path = self._twitch.fetch(game_title, igdb_game_id, output_dir)
            if path:
                result = self._post_process(path)
                if result:
                    result.source_tier = "twitch"
                    result.source_url = f"twitch:clips:{game_title}"
                    return result

        # Tier 4: Pexels — DISABLED.
        # Stock footage has no relation to the actual story and produces
        # low-quality reels that hurt channel credibility. If no real
        # gameplay clip exists, the story stays at DRAFTED (video_gate).
        logger.warning("All clip sourcing tiers failed for '%s' — no Pexels fallback", game_title)
        return None
