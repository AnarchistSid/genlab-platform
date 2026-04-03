"""SpliceReel visual rendering strategy.

Cinematic Pexels B-roll queries + film rating overlay via genlab-core compositor.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import yaml
from genlab_core.media.audio_probe import extract_audio_track, has_meaningful_audio
from genlab_core.media.frame_compositor import FrameCompositor
from genlab_core.media.whisper_timing import align_words, transcribe_words
from genlab_core.rendering.overlay_compositor import OverlaySpec, composite_overlay
from genlab_core.strategies import VisualRenderStrategy

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


_CINEMATIC_QUERIES = [
    "cinema movie theater dark",
    "film clapper board production",
    "popcorn movie night dark",
    "hollywood walk of fame night",
    "red carpet premiere crowd",
    "spotlight stage performance",
]


class MovieVisualRenderStrategy(VisualRenderStrategy):
    """Render visual assets — cinematic B-roll + film rating overlay."""

    def __init__(self) -> None:
        logger.info("[movies] MovieVisualRenderStrategy initialized")
        self._sources_config: dict | None = None
        self._visuals_config: dict | None = None

    def _ensure_config(self) -> None:
        if self._sources_config is None:
            self._sources_config = _load_yaml(NICHE_ROOT / "config" / "sources.yaml")
        if self._visuals_config is None:
            self._visuals_config = _load_yaml(NICHE_ROOT / "config" / "visuals.yaml")

    def _get_whisper_config(self) -> dict:
        """Get whisper_sync config from visuals.yaml."""
        self._ensure_config()
        animation = self._visuals_config.get("animation", {})
        wbw = animation.get("word_by_word", {})
        return wbw.get("whisper_sync", {"enabled": False})

    def prepare_whisper_words(
        self,
        clip_path: Path,
        caption_text: str,
    ) -> list[dict] | None:
        """Attempt Whisper transcription on clip audio for synced captions.

        Returns aligned word list for WordByWordAnimator, or None to fall back
        to WPM timing. Movie trailers and clips almost always have dialogue/narration.
        """
        ws_config = self._get_whisper_config()
        if not ws_config.get("enabled", False):
            return None

        if not has_meaningful_audio(
            clip_path,
            silence_threshold_db=ws_config.get("silence_threshold_db", -40),
        ):
            logger.info("[movies] No meaningful audio in %s — WPM fallback", clip_path)
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "audio.wav"
            extracted = extract_audio_track(clip_path, wav_path)
            if extracted is None:
                return None

            whisper_words = transcribe_words(
                extracted,
                model_size=ws_config.get("model_size", "base"),
                min_confidence=ws_config.get("min_confidence", 0.3),
            )
            if whisper_words is None:
                return None

            return align_words(caption_text, whisper_words)

    def _compose_frame(self, clip_path: Path, story: dict, context: dict) -> str:
        """Compose video through FrameCompositor. Returns empty string on failure."""
        run_dir = context.get("run_dir", "")
        if not run_dir:
            return ""

        hook_text = (story.get("content") or {}).get("hook", story.get("title", ""))
        sid = story.get("story_id", "unknown")
        output_dir = Path(run_dir) / "visuals" / sid
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"{sid[:16]}_reel.mp4")

        try:
            from genlab_core.media.frame_compositor import probe_video
            visuals_yaml = str(NICHE_ROOT / "config" / "visuals.yaml")
            compositor = FrameCompositor.from_visuals_yaml(visuals_yaml)
            try:
                info = probe_video(str(clip_path))
                dur = min(info.duration_seconds, 60) if info.duration_seconds > 0 else 55
            except Exception:
                dur = 55
            return compositor.compose(
                source_video_path=str(clip_path),
                hook_text=hook_text,
                output_path=output_path,
                duration_seconds=dur,
            )
        except Exception as e:
            logger.error("[movies] FrameCompositor failed: %s", e)
            return ""

    def _build_pexels_queries(self, story: dict) -> list[str]:
        """Generate cinematic Pexels search queries."""
        self._ensure_config()

        configured = (
            self._sources_config
            .get("media", {})
            .get("pexels", {})
            .get("cinematic_queries", _CINEMATIC_QUERIES)
        )

        film_title = story.get("film_title", "") or story.get("title", "")
        queries = list(configured[:3])

        if film_title:
            queries.insert(0, f"{film_title} movie")

        return queries[:3]

    def apply_overlay(self, input_video_path: Path, meta: dict) -> Path:
        """Apply film rating overlay to a rendered video.

        Returns overlaid video path, or original if overlay fails.
        """
        film_title = meta.get("film_title", "")
        rt_score = meta.get("rt_score")
        imdb_score = meta.get("imdb_score")

        if not film_title:
            logger.debug("[visual] no film_title — skipping overlay")
            return input_video_path

        spec = OverlaySpec.film_rating(
            title=film_title,
            rt_score=rt_score,
            imdb_score=imdb_score,
        )

        output_path = input_video_path.parent / (
            input_video_path.stem + "_overlay" + input_video_path.suffix
        )

        return composite_overlay(input_video_path, output_path, spec)

    def _render_story(self, story: dict) -> dict:
        queries = self._build_pexels_queries(story)

        media = story.setdefault("media", {})
        media["pexels_queries"] = queries
        media["overlay_enabled"] = True
        media["render_status"] = "pexels_queries_ready"

        # Log overlay spec for dry-run visibility
        film_title = story.get("film_title", "")
        rt_score = story.get("rt_score")
        imdb_score = story.get("imdb_score")
        if film_title:
            logger.info(
                "[visual] film_rating overlay spec: title=%s, rt_score=%s, imdb=%s",
                film_title, rt_score, imdb_score,
            )

        return story

    def execute(self, context: Any) -> Any:
        self._ensure_config()

        stories = context.get("stories", [])
        if not stories:
            logger.info("[movies] VisualRenderStrategy: no stories to render")
            context.setdefault("run_stats", {})["render"] = {
                "status": "no_stories",
                "rendered": 0,
            }
            return context

        # Check clip_index for downloaded videos (set by DownloadTopVideos stage)
        clip_index = context.get("clip_index", {})
        clips = clip_index.get("clips", {})

        rendered = 0
        videos_found = 0
        for story in stories:
            try:
                sid = story.get("story_id", "")
                clip_entry = clips.get(sid, {})

                if clip_entry.get("success") and clip_entry.get("clip_path"):
                    clip_path = Path(clip_entry["clip_path"])
                    if clip_path.exists():
                        composed = self._compose_frame(clip_path, story, context)
                        if composed:
                            story.setdefault("media", {})["rendered_path"] = composed
                            story["media"]["render_status"] = "video_ready"
                            story["media"]["overlay_enabled"] = True
                            story["media"]["compositor"] = "frame_compositor"
                            videos_found += 1
                            rendered += 1
                        else:
                            story.setdefault("media", {})["render_status"] = "render_failed"
                            logger.warning("[movies] Render failed for '%s' — staying DRAFTED", story.get("title", "")[:50])
                        continue

                # No downloaded video — fall back to Pexels query generation
                self._render_story(story)
                story.setdefault("media", {})["render_status"] = "no_video"
                rendered += 1
            except Exception:
                logger.exception("[movies] Failed to render: %s", story.get("title", "?"))

        context.setdefault("run_stats", {})["render"] = {
            "rendered": rendered,
            "total": len(stories),
            "videos_found": videos_found,
            "method": "cinematic_broll",
            "overlay_enabled": True,
        }

        logger.info(
            "[movies] VisualRender: %d/%d stories have video, %d rendered total",
            videos_found, len(stories), rendered,
        )
        return context
