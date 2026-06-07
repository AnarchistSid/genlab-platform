"""Pipeline stage: Burn Whisper-synced animated captions onto rendered videos.

Shared stage usable by any channel (CW, SR, FD). For each story with a
rendered video and hook text:
  1. Detect audio (ffprobe + volumedetect)
  2. If audio: extract WAV -> Whisper transcription -> align words
  3. If silent + TTS enabled: generate TTS -> Whisper on TTS -> align words
  4. Pass whisper_words to WordByWordAnimator
  5. Run FFmpeg with drawtext filters to burn captions
  6. Update story['media']['rendered_path'] to captioned video

Falls back to WPM timing when Whisper is unavailable or fails.
Failure is graceful -- video passes through uncaptioned.
"""

from __future__ import annotations

import logging
import random
import subprocess
import tempfile
from pathlib import Path

from genlab_core.media.audio_probe import extract_audio_track, has_meaningful_audio
from genlab_core.media.ffmpeg import get_ffmpeg_binary
from genlab_core.media.whisper_timing import align_words, transcribe_words
from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)


class RenderWhisperCaptions:
    """Burn Whisper-synced word-by-word captions onto rendered videos.

    Designed as a pipeline stage: ``stage.execute(context) -> context``.
    Processes stories in context['stories'] that have both:
      - media.rendered_path (an existing video file)
      - media.hook_text or story['hook'] (caption text to animate)

    Config is loaded from the channel's visuals.yaml:
      animation.word_by_word.whisper_sync.enabled = true|false
    """

    def execute(self, context: StageContext) -> StageContext:
        stories = context.get("stories", [])
        # The runner stores the loaded config under "niche_config" — that is
        # the canonical key in :class:`StageContext`. The legacy "config" key
        # is kept as a fallback for direct/test callers that pre-date the
        # TypedDict migration; new code should never write it. With a real
        # type checker the original ``context.get("config")`` reading bug
        # (PR #55) would have been caught here.
        config = context.get("niche_config") or context.get("config", {})  # type: ignore[typeddict-item]

        # Load whisper config from channel's visuals.yaml
        ws_config = self._get_whisper_config(config)
        if not ws_config.get("enabled", False):
            logger.info("[WHISPER_CAPTIONS] Whisper sync disabled -- skipping")
            return context

        ab_config = ws_config.get("ab_test", {})
        ab_enabled = ab_config.get("enabled", False)
        wpm_control_pct = float(ab_config.get("wpm_control_pct", 0.30))

        stats = {
            "captioned": 0,
            "wpm_fallback": 0,
            "skipped": 0,
            "failed": 0,
            "ab_synced": 0,
            "ab_wpm_control": 0,
        }

        for i, story in enumerate(stories):
            media = story.get("media") or {}
            rendered_path = media.get("rendered_path")

            if not rendered_path or not Path(rendered_path).exists():
                stats["skipped"] += 1
                continue

            # Get caption text -- try hook_text, then hook, then title
            caption_text = (
                media.get("hook_text") or story.get("hook") or story.get("title", "")
            ).strip()

            if not caption_text:
                stats["skipped"] += 1
                continue

            # A/B test assignment
            force_wpm = False
            if ab_enabled and random.random() < wpm_control_pct:
                force_wpm = True
                caption_mode = "wpm_control"
                stats["ab_wpm_control"] += 1
            else:
                caption_mode = "synced" if ab_enabled else "synced_no_ab"
                if ab_enabled:
                    stats["ab_synced"] += 1

            # Tag story with caption mode for downstream analytics
            story.setdefault("metadata", {})["caption_mode"] = caption_mode

            result = self._render_captions(
                video_path=Path(rendered_path),
                caption_text=caption_text,
                ws_config=ws_config,
                item_key=f"story_{i}",
                config=config,
                force_wpm=force_wpm,
                audio_path=media.get("audio_path"),
            )

            if result is None:
                stats["failed"] += 1
            elif result == rendered_path:
                stats["wpm_fallback"] += 1
            else:
                story["media"]["rendered_path"] = result
                stats["captioned"] += 1

        context.setdefault("run_stats", {})["whisper_captions"] = stats
        logger.info(
            "[WHISPER_CAPTIONS] %d synced, %d WPM fallback, %d skipped, %d failed"
            " | A/B: %d synced, %d wpm_control",
            stats["captioned"],
            stats["wpm_fallback"],
            stats["skipped"],
            stats["failed"],
            stats["ab_synced"],
            stats["ab_wpm_control"],
        )
        return context

    def _get_whisper_config(self, config: dict) -> dict:
        """Extract whisper_sync config from niche config."""
        animation = config.get("animation", {})
        if not animation:
            # Try loading from visuals config if nested
            visuals = config.get("visuals", {})
            animation = visuals.get("animation", {})
        wbw = animation.get("word_by_word", {})
        return wbw.get("whisper_sync", {"enabled": False})

    def _get_whisper_words(
        self,
        video_path: Path,
        caption_text: str,
        ws_config: dict,
    ) -> list[dict] | None:
        """Get Whisper word timestamps for a video clip.

        Returns aligned word list or None (caller uses WPM fallback).
        """
        threshold = ws_config.get("silence_threshold_db", -40)
        model_size = ws_config.get("model_size", "base")
        min_conf = ws_config.get("min_confidence", 0.3)

        has_audio = has_meaningful_audio(video_path, silence_threshold_db=threshold)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            if has_audio:
                wav_path = tmpdir_path / "audio.wav"
                extracted = extract_audio_track(video_path, wav_path)
                if extracted is None:
                    return None
                audio_for_whisper = extracted
            else:
                # Silent clip -- try TTS if available
                try:
                    from genlab_core.tts.factory import build_tts_cascade

                    tts_path = tmpdir_path / "tts_voiceover.wav"
                    tts = build_tts_cascade()
                    result = tts.synthesize(caption_text, tts_path)
                    if not result.success:
                        return None
                    audio_for_whisper = Path(result.output_path)
                except (ImportError, Exception) as e:
                    logger.debug("[WHISPER_CAPTIONS] TTS unavailable: %s", e)
                    return None

            whisper_words = transcribe_words(
                audio_for_whisper,
                model_size=model_size,
                min_confidence=min_conf,
            )
            if whisper_words is None:
                return None

            return align_words(caption_text, whisper_words)

    def _render_captions(
        self,
        video_path: Path,
        caption_text: str,
        ws_config: dict,
        item_key: str,
        config: dict,
        force_wpm: bool = False,
        audio_path: str | None = None,
    ) -> str | None:
        """Render captions onto video. Returns new path, original path (WPM), or None."""
        # Import WordByWordAnimator -- canonical location is genlab_core.rendering.word_animator.
        # Falls back to BlackboxBrief shim if import path not available.
        WordByWordAnimator = self._get_animator_class()
        if WordByWordAnimator is None:
            logger.warning("[WHISPER_CAPTIONS] WordByWordAnimator not available")
            return None

        # Get whisper words (may be None -> WPM fallback)
        if force_wpm:
            whisper_words = None
            logger.debug("[WHISPER_CAPTIONS] %s: A/B control — forcing WPM", item_key)
        else:
            whisper_words = self._get_whisper_words(video_path, caption_text, ws_config)

        # Find font
        font_path = self._find_font(config)

        animator = WordByWordAnimator(font_path=font_path)
        filters, duration, _ = animator.build_animated_filters(
            caption_text,
            text_type="hook",
            whisper_words=whisper_words,
        )

        if not filters:
            return None

        # Audio: keep the source track by default. Only when the clip is SILENT
        # (no meaningful source audio) and a generated TTS voiceover exists do
        # we mux the voiceover in — so a dead-air reel gets narration, while
        # clips with real audio (highlights, gameplay, trailers — the content
        # that actually performs) are never talked over.
        extra_inputs: list[str] = []
        audio_args = ["-c:a", "copy"]
        if audio_path and Path(audio_path).exists():
            threshold = ws_config.get("silence_threshold_db", -40)
            if not has_meaningful_audio(video_path, silence_threshold_db=threshold):
                extra_inputs = ["-i", str(audio_path)]
                audio_args = ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-shortest"]
                logger.info(
                    "[WHISPER_CAPTIONS] %s: silent clip — muxing TTS voiceover",
                    item_key,
                )

        # Burn filters onto video
        output_path = video_path.parent / (video_path.stem + "_captioned.mp4")
        try:
            ffmpeg = get_ffmpeg_binary()
            cmd = [
                ffmpeg,
                "-y",
                "-i",
                str(video_path),
                *extra_inputs,
                "-vf",
                filters,
                *audio_args,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode == 0 and output_path.exists():
                logger.info("[WHISPER_CAPTIONS] %s captioned -> %s", item_key, output_path)
                return str(output_path)
            else:
                logger.warning(
                    "[WHISPER_CAPTIONS] FFmpeg failed for %s (rc=%d)",
                    item_key,
                    proc.returncode,
                )
                return None
        except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("[WHISPER_CAPTIONS] FFmpeg error for %s: %s", item_key, e)
            return None

    @staticmethod
    def _get_animator_class():
        """Import WordByWordAnimator — canonical location is genlab_core.rendering.word_animator."""
        try:
            from genlab_core.rendering.word_animator import WordByWordAnimator

            return WordByWordAnimator
        except ImportError:
            pass
        # Legacy fallback: BlackboxBrief location (kept until shim is stable)
        try:
            from execution.utils.word_by_word_animator import WordByWordAnimator

            return WordByWordAnimator
        except ImportError:
            pass
        return None

    @staticmethod
    def _find_font(config: dict) -> str | None:
        """Find a suitable font for captions."""
        from genlab_core.settings import _PROJECT_ROOT

        candidates = [
            _PROJECT_ROOT / "assets" / "fonts" / "Inter-Bold.ttf",
            _PROJECT_ROOT / "assets" / "fonts" / "Montserrat-ExtraBold.ttf",
            Path("/System/Library/Fonts/Helvetica.ttc"),
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        return None
