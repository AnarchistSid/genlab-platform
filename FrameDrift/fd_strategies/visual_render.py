"""FrameDrift visual rendering strategy.

Lifestyle Pexels B-roll queries — brand-safe only. NEVER use brand names
in Pexels queries (enforced by brand_sensitivity assertion).
No numeric overlay for anime (unlike sports scores or film ratings).
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
from genlab_core.strategies.base_visual_render import BaseVisualRenderStrategy
from genlab_core.tts.factory import build_tts_cascade

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent

_DEFAULT_ANIME_QUERIES = [
    "anime aesthetic lifestyle urban",
    "anime culture lifestyle",
    "anime editorial aesthetic",
]

# Protected brand names — never in Pexels queries (anime studios from niche.yaml)
_PROTECTED_BRANDS = frozenset(
    {
        "studio ghibli",
        "toei animation",
        "mappa",
        "ufotable",
        "bones",
        "cloverworks",
        "kyoto animation",
        "wit studio",
        "sunrise",
        "a-1 pictures",
        "shaft",
        "trigger",
        "crunchyroll",
        "funimation",
        "aniplex",
        "kadokawa",
        "viz media",
        "shueisha",
        "kodansha",
        "square enix",
    }
)


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


class AnimeVisualRenderStrategy(BaseVisualRenderStrategy):
    """Render visual assets — brand-safe Pexels B-roll, no overlay.

    R-70 part 2 PR 3: inherits from ``BaseVisualRenderStrategy``.
    The shared ``_get_whisper_config`` is now inherited (verified
    byte-identical with SR + CW at extraction time).
    """

    def __init__(self) -> None:
        # ``super().__init__()`` initializes ``self._visuals_config = None``
        # — the base relies on that attribute existing before any
        # inherited method runs (notably ``_get_whisper_config``).
        super().__init__()
        logger.info("[anime] AnimeVisualRenderStrategy initialized")
        self._sources_config: dict | None = None

    def _ensure_config(self) -> None:
        if self._sources_config is not None:
            return
        self._sources_config = _load_yaml(NICHE_ROOT / "config" / "sources.yaml")
        self._visuals_config = _load_yaml(NICHE_ROOT / "config" / "visuals.yaml")

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
            logger.error("[anime] FrameCompositor failed: %s", e)
            return ""

    def _build_pexels_queries(self, story: dict) -> list[str]:
        """Generate brand-safe Pexels search queries."""
        self._ensure_config()

        configured = (
            self._sources_config.get("media", {})
            .get("pexels", {})
            .get("anime_queries", _DEFAULT_ANIME_QUERIES)
        )

        queries = list(configured[:3])

        # Brand safety check — never include brand names
        safe_queries = []
        for q in queries:
            q_lower = q.lower()
            if any(brand in q_lower for brand in _PROTECTED_BRANDS):
                logger.warning(
                    "[visual] Brand name detected in Pexels query '%s' — replaced with fallback",
                    q,
                )
                safe_queries.append("anime aesthetic lifestyle urban")
            else:
                safe_queries.append(q)

        return safe_queries[:3]

    # ``_get_whisper_config`` now inherited from BaseVisualRenderStrategy.

    def prepare_whisper_words(
        self,
        clip_path: Path,
        caption_text: str,
    ) -> list[dict] | None:
        """Attempt Whisper sync -- with TTS fallback for silent clips (Path B).

        Anime clips are mixed: some clips have dialogue (Path A),
        silent clips need TTS voiceover first (Path B).
        """
        ws_config = self._get_whisper_config()
        if not ws_config.get("enabled", False):
            return None

        has_audio = has_meaningful_audio(
            clip_path,
            silence_threshold_db=ws_config.get("silence_threshold_db", -40),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            if has_audio:
                # Path A: Whisper on existing audio
                wav_path = tmpdir_path / "audio.wav"
                extracted = extract_audio_track(clip_path, wav_path)
                if extracted is None:
                    return None
                audio_for_whisper = extracted
            else:
                # Path B: Generate TTS -> Whisper on TTS
                tts_path = tmpdir_path / "tts_voiceover.wav"
                try:
                    tts = build_tts_cascade()
                    result = tts.synthesize(caption_text, tts_path)
                    if not result.success:
                        logger.info("[anime] TTS failed -- WPM fallback")
                        return None
                    audio_for_whisper = Path(result.output_path)
                except Exception as e:
                    logger.warning("[anime] TTS error: %s", e)
                    return None

            whisper_words = transcribe_words(
                audio_for_whisper,
                model_size=ws_config.get("model_size", "base"),
                min_confidence=ws_config.get("min_confidence", 0.3),
            )
            if whisper_words is None:
                return None

            return align_words(caption_text, whisper_words)

    def _render_story(self, story: dict) -> dict:
        queries = self._build_pexels_queries(story)

        media = story.setdefault("media", {})
        media["pexels_queries"] = queries
        media["overlay_enabled"] = False  # no numeric overlay for anime
        media["render_status"] = "pexels_queries_ready"

        return story

    def execute(self, context: Any) -> Any:
        self._ensure_config()

        stories = context.get("stories", [])
        if not stories:
            logger.info("[anime] VisualRenderStrategy: no stories to render")
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
                            story["media"]["overlay_enabled"] = False
                            story["media"]["compositor"] = "frame_compositor"
                            videos_found += 1
                            rendered += 1
                        else:
                            story.setdefault("media", {})["render_status"] = "render_failed"
                            logger.warning(
                                "[anime] Render failed for '%s' — staying DRAFTED",
                                story.get("title", "")[:50],
                            )
                        continue

                # No downloaded video — fall back to Pexels query generation
                self._render_story(story)
                story.setdefault("media", {})["render_status"] = "no_video"
                rendered += 1
            except Exception:
                logger.exception("[anime] Failed to render: %s", story.get("title", "?"))

        context.setdefault("run_stats", {})["render"] = {
            "rendered": rendered,
            "total": len(stories),
            "videos_found": videos_found,
            "method": "lifestyle_broll",
            "overlay_enabled": False,
        }

        logger.info(
            "[anime] VisualRender: %d/%d stories have video, %d rendered total",
            videos_found,
            len(stories),
            rendered,
        )
        return context
