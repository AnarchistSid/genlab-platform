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
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Niche → YouTube search keywords
# ---------------------------------------------------------------------------
_NICHE_KEYWORDS: Dict[str, List[str]] = {
    "ai_creators": ["AI", "artificial intelligence", "machine learning"],
    "ai_news": ["AI", "artificial intelligence", "machine learning"],  # backward compat alias
    "anime": ["anime", "manga", "otaku"],
    "movies": ["movie", "film", "cinema", "trailer"],
    "sports": ["sports", "highlights", "game", "match"],
    "gaming": ["gaming", "gameplay", "esports"],
}

# Minimum file size (bytes) for a valid download
_MIN_FILE_SIZE = 100 * 1024  # 100 KB

# yt-dlp download timeout (seconds)
_DOWNLOAD_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download_video(url: str, output_path: str) -> Dict[str, Any]:
    """Download a video using yt-dlp subprocess.

    Returns:
        {"success": bool, "duration": float, "error": str}
    """
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
              "/best[height<=1080][ext=mp4]/best",
        "-o", output_path,
        "--no-playlist",
        "--socket-timeout", "30",
        "--retries", "2",
        url,
    ]
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_DOWNLOAD_TIMEOUT,
        )
        elapsed = time.monotonic() - t0
        if result.returncode != 0:
            err_msg = (result.stderr or result.stdout or "unknown error").strip()
            # Truncate very long error messages
            if len(err_msg) > 500:
                err_msg = err_msg[:500] + "..."
            logger.warning("yt-dlp failed for %s: %s", url, err_msg[:200])
            return {"success": False, "duration": elapsed, "error": err_msg}
        return {"success": True, "duration": elapsed, "error": ""}
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        logger.warning("yt-dlp timed out after %ds for %s", _DOWNLOAD_TIMEOUT, url)
        return {"success": False, "duration": elapsed, "error": "download timed out"}
    except FileNotFoundError:
        elapsed = time.monotonic() - t0
        logger.error("yt-dlp not found in PATH")
        return {"success": False, "duration": elapsed, "error": "yt-dlp not installed"}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.warning("yt-dlp unexpected error for %s: %s", url, exc)
        return {"success": False, "duration": elapsed, "error": str(exc)}


def _probe_duration(path: str) -> float:
    """Get video duration in seconds using ffprobe.

    Returns 0.0 on any failure.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
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
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, Exception) as exc:
        logger.debug("ffprobe failed for %s: %s", path, exc)
    return 0.0


def _has_video_stream(path: str) -> bool:
    """Return True if the file contains at least one video stream."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
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
    except Exception:
        return False


def _validate_download(path: str) -> Dict[str, Any]:
    """Validate a downloaded video file.

    Checks:
        1. File exists
        2. File size > 100 KB
        3. Has a video stream (ffprobe)

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
    return {"valid": True, "reason": "", "duration_seconds": duration}


# ---------------------------------------------------------------------------
# Core download function
# ---------------------------------------------------------------------------

def download_videos_for_stories(
    stories: List[Dict[str, Any]],
    run_dir: str | Path,
    niche_id: str,
    max_stories: int = 10,
    youtube_api_key: str | None = None,
) -> Dict[str, Dict[str, Any]]:
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

    # Apply top-N cutoff
    top_stories = stories[:max_stories]

    keywords = _NICHE_KEYWORDS.get(niche_id, [])
    sourcer = VideoSourcer(
        niche_id=niche_id,
        niche_keywords=keywords,
        youtube_api_key=youtube_api_key,
    )

    entries: Dict[str, Dict[str, Any]] = {}

    for i, story in enumerate(top_stories, 1):
        story_id = str(story.get("story_id", "")).strip()
        title = story.get("title", "untitled")
        if not story_id:
            # Generate stable story_id from title hash — niche strategies
            # don't always set story_id before the download stage.
            import hashlib
            story_id = hashlib.sha256(title.encode()).hexdigest()
            story["story_id"] = story_id
            logger.debug("Story %d: generated story_id from title hash", i)

        logger.info(
            "[%d/%d] Sourcing video for: %s (story_id=%s)",
            i, len(top_stories), title[:60], story_id[:12],
        )

        # Find a video URL via the fallback chain
        try:
            result = sourcer.find_video_for_story(story)
        except Exception as exc:
            logger.warning("VideoSourcer error for story %s: %s", story_id[:12], exc)
            entries[story_id] = {
                "story_id": story_id,
                "success": False,
                "clip_path": "",
                "source_url": "",
                "backend": "",
                "duration_seconds": 0.0,
                "error": f"sourcer error: {exc}",
            }
            continue

        if result is None:
            logger.info("  No video found for story %s", story_id[:12])
            entries[story_id] = {
                "story_id": story_id,
                "success": False,
                "clip_path": "",
                "source_url": "",
                "backend": "",
                "duration_seconds": 0.0,
                "error": "no video found",
            }
            continue

        video_url = result.url
        backend = result.backend
        logger.info("  Found video via %s: %s", backend, video_url[:80])

        # Download the video
        # Use story_id prefix for unique filenames
        safe_id = story_id[:16].replace("/", "_")
        output_path = str(clips_dir / f"{safe_id}.mp4")

        dl_result = _download_video(video_url, output_path)

        if not dl_result["success"]:
            entries[story_id] = {
                "story_id": story_id,
                "success": False,
                "clip_path": "",
                "source_url": video_url,
                "backend": backend,
                "duration_seconds": 0.0,
                "error": dl_result["error"],
            }
            continue

        # yt-dlp may add format extension — find the actual file
        actual_path = _find_downloaded_file(output_path)
        if not actual_path:
            entries[story_id] = {
                "story_id": story_id,
                "success": False,
                "clip_path": "",
                "source_url": video_url,
                "backend": backend,
                "duration_seconds": 0.0,
                "error": "downloaded file not found on disk",
            }
            continue

        # Validate the download
        validation = _validate_download(actual_path)

        if not validation["valid"]:
            logger.warning(
                "  Validation failed for %s: %s", actual_path, validation["reason"],
            )
            entries[story_id] = {
                "story_id": story_id,
                "success": False,
                "clip_path": "",
                "source_url": video_url,
                "backend": backend,
                "duration_seconds": 0.0,
                "error": f"validation: {validation['reason']}",
            }
            continue

        logger.info(
            "  Downloaded: %s (%.1fs, %s)",
            Path(actual_path).name,
            validation["duration_seconds"],
            backend,
        )

        entries[story_id] = {
            "story_id": story_id,
            "success": True,
            "clip_path": actual_path,
            "source_url": video_url,
            "backend": backend,
            "duration_seconds": validation["duration_seconds"],
            "error": "",
        }

    # Log sourcer stats
    stats = sourcer.get_stats()
    logger.info(
        "VideoSourcer stats: %s",
        ", ".join(f"{k}={v}" for k, v in stats.items()),
    )

    return entries


def _find_downloaded_file(expected_path: str) -> Optional[str]:
    """Find the actual downloaded file, handling yt-dlp extension variations.

    yt-dlp sometimes appends format suffixes or uses different extensions.
    """
    p = Path(expected_path)
    if p.exists():
        return str(p)

    # Check common yt-dlp output variations
    parent = p.parent
    stem = p.stem
    for ext in (".mp4", ".mkv", ".webm", ".mp4.part"):
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
    entries: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
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

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
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
        max_stories = (
            config.get("pipeline", {}).get("max_items_per_run", 10)
        )

        if not stories:
            logger.warning("[DownloadTopVideos] No stories in context")
            context["clip_index"] = build_clip_index(
                context.get("run_id", "unknown"), {},
            )
            context["clip_index_path"] = ""
            return context

        if not run_dir:
            logger.error("[DownloadTopVideos] No run_dir in context")
            context["clip_index"] = build_clip_index(
                context.get("run_id", "unknown"), {},
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

        return context

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
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
        raise SystemExit(2)

    stories = trend_pack.get("stories", [])
    if not stories:
        logger.warning("No stories found in trend_pack.json")
        raise SystemExit(2)

    logger.info(
        "Processing %d stories (max %d) for niche '%s'",
        len(stories), args.max_stories, args.niche,
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

    logger.info(
        "Wrote clip_index.json: %d/%d videos downloaded, %d failed",
        clip_index["videos_downloaded"],
        clip_index["videos_total"],
        clip_index["videos_failed"],
    )


if __name__ == "__main__":
    main()
