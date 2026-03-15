"""Pipeline stage: Validate rendered videos meet platform specs.

Checks each rendered video against Instagram Reels / YouTube Shorts specs:
  - Dimensions: 1080×1920 (9:16) required
  - Codec: H.264 (libx264)
  - Pixel format: yuv420p
  - Color space: bt709
  - Audio: AAC, 48kHz stereo
  - Duration: 15-60 seconds
  - File size: < 100 MB

Auto-fix attempts re-encoding for codec/pixel format/color space mismatches.
VMAF check is optional (requires vmaf model, disabled by default).

Non-fatal: invalid videos are flagged but don't crash the pipeline.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from genlab_core.media.ffmpeg import get_ffmpeg_binary, get_ffprobe_binary

logger = logging.getLogger(__name__)

# Platform specs
SPEC = {
    "target_width": 1080,
    "target_height": 1920,
    "codec": "h264",
    "pix_fmt": "yuv420p",
    "color_space": "bt709",
    "audio_codec": "aac",
    "audio_sample_rate": 48000,
    "audio_channels": 2,
    "min_duration": 15.0,
    "max_duration": 60.0,
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

        # Dimensions — must be exactly 1080x1920
        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))
        if width != SPEC["target_width"] or height != SPEC["target_height"]:
            issues.append(f"wrong_dimensions:{width}x{height}")

        # Codec — must be H.264
        codec = video_stream.get("codec_name", "")
        if codec != SPEC["codec"]:
            issues.append(f"wrong_codec:{codec}")

        # Pixel format
        pix_fmt = video_stream.get("pix_fmt", "")
        if pix_fmt != SPEC["pix_fmt"]:
            issues.append(f"wrong_pix_fmt:{pix_fmt}")

        # Color space — must be bt709
        color_space = video_stream.get("color_space", "")
        if color_space and color_space != SPEC["color_space"]:
            issues.append(f"wrong_color_space:{color_space}")

        # Audio checks
        if not audio_stream:
            issues.append("no_audio_stream")
        else:
            # Audio codec — must be AAC
            audio_codec = audio_stream.get("codec_name", "")
            if audio_codec != SPEC["audio_codec"]:
                issues.append(f"wrong_audio_codec:{audio_codec}")

            # Audio sample rate — must be 48kHz
            sample_rate = int(audio_stream.get("sample_rate", 0))
            if sample_rate != SPEC["audio_sample_rate"]:
                issues.append(f"wrong_sample_rate:{sample_rate}")

            # Audio channels — must be stereo (2)
            channels = int(audio_stream.get("channels", 0))
            if channels != SPEC["audio_channels"]:
                issues.append(f"wrong_audio_channels:{channels}")

        # Duration — must be 15-60 seconds
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
        """Determine if issues are auto-fixable via re-encoding."""
        fixable = {
            "wrong_codec", "wrong_pix_fmt", "wrong_color_space",
            "wrong_audio_codec", "wrong_sample_rate", "wrong_audio_channels",
        }
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
        """Re-encode to fix codec/pix_fmt/color_space/audio issues."""
        ffmpeg = get_ffmpeg_binary()
        out = path.with_stem(f"{path.stem}_fixed")

        cmd = [
            ffmpeg, "-y",
            "-i", str(path),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-colorspace", "bt709",
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-c:a", "aac",
            "-ar", "48000",
            "-ac", "2",
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
