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
        try:
            query = self._search_template.format(game_title=game_title)
            output_path = output_dir / f"yt_{game_title.replace(' ', '_')[:40]}.mp4"

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

            # First, get info to check for banned fragments
            info_cmd = [
                "yt-dlp",
                f"ytsearch3:{query}",
                "--no-download",
                "--print",
                "%(title)s",
                "--quiet",
                "--no-warnings",
            ]
            info_result = subprocess.run(
                info_cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if info_result.returncode != 0:
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

            # Download the clean result
            dl_cmd = [
                "yt-dlp",
                search_url,
                "--format",
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]",
                "--merge-output-format",
                "mp4",
                "--no-playlist",
                "--output",
                str(output_path),
                "--quiet",
                "--no-warnings",
            ]
            result = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                logger.warning(
                    "[YouTube] Download failed for '%s': %s", game_title, result.stderr[:200]
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
    ) -> ClipResult | None:
        """Try all tiers in order, return first success or None."""
        output_dir = self._ensure_output_dir(
            project_root or Path("."),
        )

        # Tier 1: Steam
        if steam_app_id:
            path = self._steam.fetch(steam_app_id, output_dir)
            if path:
                result = self._post_process(path)
                if result:
                    result.source_tier = "steam"
                    result.source_url = f"https://store.steampowered.com/app/{steam_app_id}"
                    return result

        # Tier 2: YouTube
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
