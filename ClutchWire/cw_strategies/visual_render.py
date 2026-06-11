"""ClutchWire visual rendering strategy.

Generates Pexels B-roll search queries for sports stories.
Applies score overlays via the shared genlab-core compositor when
score data is available (home_score, away_score, teams).
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
from genlab_core.strategies.base_visual_render import BaseVisualRenderStrategy

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


_SPORT_QUERIES: dict[str, list[str]] = {
    "basketball": ["basketball court", "basketball dunk", "basketball crowd"],
    "football": ["american football stadium", "football touchdown", "nfl crowd"],
    "baseball": ["baseball stadium", "home run", "baseball crowd"],
    "soccer": ["soccer stadium", "football goal celebration", "soccer crowd"],
    "mma": ["mma octagon", "boxing ring", "fighting crowd"],
    "tennis": ["tennis court", "tennis serve"],
    "motorsport": ["formula one", "racing car", "race track"],
}

_DEFAULT_QUERIES = ["sports crowd cheering", "stadium lights", "sports highlights"]


class SportVisualRenderStrategy(BaseVisualRenderStrategy):
    """Render visual assets — Pexels B-roll + score overlay via compositor.

    R-70 part 2 PR 3: inherits from ``BaseVisualRenderStrategy``.
    The shared ``_get_whisper_config`` is now inherited (verified
    byte-identical with SR + FD at extraction time).
    """

    def __init__(self) -> None:
        # ``super().__init__()`` initializes ``self._visuals_config = None``
        # — the base relies on that attribute existing before any
        # inherited method runs (notably ``_get_whisper_config``).
        super().__init__()
        logger.info("[sports] SportVisualRenderStrategy initialized")

    def _ensure_config(self) -> None:
        if self._visuals_config is not None:
            return
        self._visuals_config = _load_yaml(NICHE_ROOT / "config" / "visuals.yaml")

    # ``_get_whisper_config`` now inherited from BaseVisualRenderStrategy.

    def prepare_whisper_words(
        self,
        clip_path: Path,
        caption_text: str,
    ) -> list[dict] | None:
        """Attempt Whisper transcription on clip audio for synced captions.

        Returns aligned word list for WordByWordAnimator, or None to fall back
        to WPM timing. Sports clips almost always have commentary audio.
        """
        ws_config = self._get_whisper_config()
        if not ws_config.get("enabled", False):
            return None

        if not has_meaningful_audio(
            clip_path,
            silence_threshold_db=ws_config.get("silence_threshold_db", -40),
        ):
            logger.info("[sports] No meaningful audio in %s — WPM fallback", clip_path)
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
            logger.error("[sports] FrameCompositor failed: %s", e)
            return ""

    def _build_pexels_queries(self, story: dict) -> list[str]:
        sport = story.get("sport", "").lower()
        queries = _SPORT_QUERIES.get(sport, _DEFAULT_QUERIES)
        teams = story.get("teams", [])
        if teams:
            queries = [f"{teams[0]} {q}" for q in queries[:2]] + queries[2:]
        return queries[:3]

    def apply_overlay(self, input_video_path: Path, meta: dict) -> Path:
        """Apply score overlay to a rendered video.

        Called after Pexels B-roll is assembled. Returns the overlaid video path,
        or original if overlay fails or no score data is available.
        """
        if meta.get("home_score") is None:
            logger.debug("[visual] no score data — skipping overlay")
            return input_video_path

        teams = meta.get("teams", [])
        spec = OverlaySpec.sports_score(
            home_team=teams[0] if len(teams) > 0 else meta.get("home_team", ""),
            away_team=teams[1] if len(teams) > 1 else meta.get("away_team", ""),
            home_score=meta.get("home_score", 0),
            away_score=meta.get("away_score", 0),
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
        teams = story.get("teams", [])
        if teams and len(teams) >= 2:
            logger.info(
                "[visual] overlay spec: %s %s vs %s",
                story.get("title", "?"),
                teams[0],
                teams[1],
            )

        return story

    def execute(self, context: Any) -> Any:
        self._ensure_config()

        stories = context.get("stories", [])
        if not stories:
            logger.info("[sports] VisualRenderStrategy: no stories to render")
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
                            logger.warning(
                                "[sports] Render failed for '%s' — staying DRAFTED",
                                story.get("title", "")[:50],
                            )
                        continue

                # No downloaded video — fall back to Pexels query generation
                self._render_story(story)
                story.setdefault("media", {})["render_status"] = "no_video"
                rendered += 1
            except Exception:
                logger.exception("[sports] Failed to render: %s", story.get("title", "?"))

        context.setdefault("run_stats", {})["render"] = {
            "rendered": rendered,
            "total": len(stories),
            "videos_found": videos_found,
            "method": "pexels_broll",
            "overlay_enabled": True,
        }

        logger.info(
            "[sports] VisualRender: %d/%d stories have video, %d rendered total",
            videos_found,
            len(stories),
            rendered,
        )
        return context
