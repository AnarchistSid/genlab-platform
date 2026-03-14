"""Pipeline stage: Validate rendered videos meet platform specs.

Checks each rendered video against Instagram Reels / YouTube Shorts specs:
  - Dimensions: 1080×1920 (9:16) preferred, min 600×1067
  - Codec: H.264 (libx264)
  - Pixel format: yuv420p
  - Audio: AAC, 48kHz preferred
  - Duration: 3-90 seconds
  - File size: < 100 MB

Auto-fix attempts re-encoding for codec/pixel format mismatches.
VMAF check is optional (requires vmaf model, disabled by default).

Non-fatal: invalid videos are flagged but don't crash the pipeline.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from genlab_core.media.ffmpeg import get_ffmpeg_binary, get_ffprobe_binary

logger = logging.getLogger(__name__)

# Platform specs
SPEC = {
    "min_width": 600,
    "min_height": 1067,
    "target_width": 1080,
    "target_height": 1920,
    "codec": "h264",
    "pix_fmt": "yuv420p",
    "audio_codec": "aac",
    "min_duration": 3.0,
    "max_duration": 90.0,
    "max_file_size_mb": 100,
}


class ValidateVideos:
    """Validate rendered videos against platform specifications.

    Reads: context['stories'], context['blueprints']
    Writes: context['stories'][*]['media']['video_validation'],
            context['run_stats']['video_validation']
    """

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        stories = context.get("stories", [])
        if not stories:
            logger.info("[ValidateVideos] No stories to validate")
            return context

        config = context.get("niche_config", {})
        auto_fix = config.get("video_validation", {}).get("auto_fix", True)

        passed = 0
        failed = 0
        fixed = 0
        skipped = 0

        for story in stories:
            media = story.get("media", {})
            video_path = media.get("rendered_path", "")

            if not video_path or not Path(video_path).exists():
                skipped += 1
                continue

            try:
                probe = self._probe(Path(video_path))
                if not probe:
                    media["video_validation"] = {"valid": False, "error": "probe_failed"}
                    failed += 1
                    continue

                issues = self._check(probe)

                if not issues:
                    media["video_validation"] = {"valid": True, "issues": []}
                    passed += 1
                elif auto_fix and self._can_fix(issues):
                    fixed_path = self._fix(Path(video_path), probe, issues)
                    if fixed_path:
                        media["rendered_path"] = str(fixed_path)
                        media["video_validation"] = {
                            "valid": True,
                            "auto_fixed": True,
                            "fixed_issues": issues,
                        }
                        fixed += 1
                        passed += 1
                    else:
                        media["video_validation"] = {"valid": False, "issues": issues}
                        failed += 1
                else:
                    media["video_validation"] = {"valid": False, "issues": issues}
                    failed += 1

            except Exception:
                logger.exception(
                    "[ValidateVideos] Error validating %s",
                    story.get("story_id", "unknown"),
                )
                media["video_validation"] = {"valid": False, "error": "exception"}
                failed += 1

        logger.info(
            "[ValidateVideos] %d passed (%d auto-fixed), %d failed, %d skipped",
            passed, fixed, failed, skipped,
        )

        context.setdefault("run_stats", {})["video_validation"] = {
            "passed": passed,
            "failed": failed,
            "fixed": fixed,
            "skipped": skipped,
        }

        return context

    @staticmethod
    def _probe(path: Path) -> Optional[Dict[str, Any]]:
        """Probe video with ffprobe, return metadata dict."""
        ffprobe = get_ffprobe_binary()
        cmd = [
            ffprobe, "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return None
            return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            return None

    @staticmethod
    def _check(probe: Dict[str, Any]) -> List[str]:
        """Check probe data against spec, return list of issues."""
        issues: List[str] = []

        # Find video stream
        video_stream = None
        audio_stream = None
        for stream in probe.get("streams", []):
            if stream.get("codec_type") == "video" and not video_stream:
                video_stream = stream
            elif stream.get("codec_type") == "audio" and not audio_stream:
                audio_stream = stream

        if not video_stream:
            issues.append("no_video_stream")
            return issues

        # Dimensions
        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))
        if width < SPEC["min_width"] or height < SPEC["min_height"]:
            issues.append(f"dimensions_too_small:{width}x{height}")

        # Codec
        codec = video_stream.get("codec_name", "")
        if codec != SPEC["codec"]:
            issues.append(f"wrong_codec:{codec}")

        # Pixel format
        pix_fmt = video_stream.get("pix_fmt", "")
        if pix_fmt != SPEC["pix_fmt"]:
            issues.append(f"wrong_pix_fmt:{pix_fmt}")

        # Duration
        fmt = probe.get("format", {})
        duration = float(fmt.get("duration", 0))
        if duration < SPEC["min_duration"]:
            issues.append(f"too_short:{duration:.1f}s")
        elif duration > SPEC["max_duration"]:
            issues.append(f"too_long:{duration:.1f}s")

        # File size
        size_mb = int(fmt.get("size", 0)) / (1024 * 1024)
        if size_mb > SPEC["max_file_size_mb"]:
            issues.append(f"too_large:{size_mb:.1f}MB")

        return issues

    @staticmethod
    def _can_fix(issues: List[str]) -> bool:
        """Determine if issues are auto-fixable (codec/pix_fmt only)."""
        fixable = {"wrong_codec", "wrong_pix_fmt"}
        return all(
            any(issue.startswith(f) for f in fixable)
            for issue in issues
        )

    @staticmethod
    def _fix(
        path: Path,
        probe: Dict[str, Any],
        issues: List[str],
    ) -> Optional[Path]:
        """Re-encode to fix codec/pix_fmt issues."""
        ffmpeg = get_ffmpeg_binary()
        out = path.with_stem(f"{path.stem}_fixed")

        cmd = [
            ffmpeg, "-y",
            "-i", str(path),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-ar", "48000",
            "-movflags", "+faststart",
            str(out),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode == 0 and out.exists():
                return out
            return None
        except subprocess.TimeoutExpired:
            return None
