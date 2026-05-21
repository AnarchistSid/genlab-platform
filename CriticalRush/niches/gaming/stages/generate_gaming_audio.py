"""Pipeline stage: Generate AI commentary audio for gaming videos.

Generates commentary text from templates (content_prompts.yaml), then
synthesizes TTS audio via ElevenLabs (primary) or edge-tts (fallback).
Writes audio file paths back to stories and/or blueprints.

SEPARATE stage from RENDER_TEXT_OVERLAYS — independent error handling.

Failure handling:
  - LLM/template script generation failure: log, set audio_path=None, continue
  - TTS synthesis failure: log, set audio_path=None, continue
  - One attempt only per story. Never retry inside the stage.
  - A failure here NEVER prevents the story from proceeding downstream.

Usage:
    stage = GenerateGamingAudio()
    context = stage.execute(context)
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any

import yaml
from genlab_core.tts import TTSCascade

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
NICHE_ROOT = Path(__file__).resolve().parent.parent

_cascade = None


def _get_tts_cascade() -> TTSCascade:
    """Build or return a cached TTSCascade.

    Uses genlab_core.tts.factory.build_tts_cascade() so this stage gets
    OpenAI TTS in the fallback chain along with ElevenLabs and the
    free providers. Previously this function built its own cascade
    with only ElevenLabs+EdgeTTS — but ELEVENLABS_API_KEY isn't set on
    Hetzner and edge-tts package isn't installed, so the cascade had
    ZERO working providers and every gaming reel shipped silently
    with no commentary. 2026-05-21 forensics: 5 TTS failures per
    gaming run, 0 generated.
    """
    global _cascade
    if _cascade is not None:
        return _cascade
    from genlab_core.tts.factory import build_tts_cascade

    _cascade = build_tts_cascade()
    return _cascade


def _load_prompts_config() -> dict:
    """Load content_prompts.yaml from niche config directory."""
    prompts_path = NICHE_ROOT / "config" / "content_prompts.yaml"
    if not prompts_path.exists():
        logger.warning("content_prompts.yaml not found at %s", prompts_path)
        return {}
    with open(prompts_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def generate_commentary_text(slot: dict, prompts_config: dict) -> str:
    """Generate commentary text for a given slot using template examples.

    For MVP, picks a random example from content_prompts.yaml for the
    matching style. No LLM call needed yet — templates are sufficient.
    """
    style = slot.get("style", "hype")
    styles = prompts_config.get("commentary_styles", {})
    style_config = styles.get(style, {})
    examples = style_config.get("examples", [])
    if not examples:
        logger.warning("No examples for commentary style '%s'", style)
        return ""
    return random.choice(examples)


class GenerateGamingAudio:
    """Generate AI commentary audio for gaming stories and compilations."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])
        blueprints = context.get("blueprints", [])

        run_id = "unknown"
        from genlab_core.context import get_current_context

        ctx = get_current_context()
        if ctx:
            run_id = ctx.run_id

        output_dir = PROJECT_ROOT / ".tmp" / "audio" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        prompts_config = _load_prompts_config()

        stats = {
            "generated": 0,
            "failed_script": 0,
            "failed_tts": 0,
            "skipped": 0,
        }

        # Process individual stories
        for i, story in enumerate(stories):
            rendered_path = (story.get("media") or {}).get("rendered_path")
            if not rendered_path:
                stats["skipped"] += 1
                continue

            self._generate_for_item(
                item=story,
                item_key=f"story_{i}",
                output_dir=output_dir,
                prompts_config=prompts_config,
                stats=stats,
            )

        # Process compilation blueprints
        for i, blueprint in enumerate(blueprints):
            rendered_path = blueprint.get("rendered_path")
            if not rendered_path:
                stats["skipped"] += 1
                continue

            self._generate_for_item(
                item=blueprint,
                item_key=f"comp_{i}",
                output_dir=output_dir,
                prompts_config=prompts_config,
                stats=stats,
            )

        context.setdefault("run_stats", {})["audio_generation"] = stats
        logger.info(
            "[AUDIO] %d generated, %d script failures, %d TTS failures, %d skipped",
            stats["generated"],
            stats["failed_script"],
            stats["failed_tts"],
            stats["skipped"],
        )
        return context

    def _generate_for_item(
        self,
        item: dict,
        item_key: str,
        output_dir: Path,
        prompts_config: dict,
        stats: dict,
    ) -> None:
        """Generate commentary audio for a single story or blueprint.

        Uses separate try/except for script generation and TTS synthesis.
        """
        # Build a commentary slot from the item
        slot = {"style": "hype"}
        hook = (item.get("content") or {}).get("hook", "")
        if hook:
            slot = {"style": "intro"}

        # --- Script generation (separate error domain) ---
        commentary_text = ""
        try:
            commentary_text = generate_commentary_text(slot, prompts_config)
        except Exception as e:
            logger.warning("[AUDIO] Script generation failed for %s: %s", item_key, e)
            stats["failed_script"] += 1
            item["commentary_audio_path"] = None
            return

        if not commentary_text:
            logger.debug("[AUDIO] Empty commentary text for %s — skipping TTS", item_key)
            stats["failed_script"] += 1
            item["commentary_audio_path"] = None
            return

        # --- TTS synthesis (separate error domain) ---
        audio_path = str(output_dir / f"{item_key}_commentary.mp3")
        try:
            result = _get_tts_cascade().synthesize(commentary_text, audio_path)
            success = result.success
        except Exception as e:
            logger.warning("[AUDIO] TTS failed for %s: %s", item_key, e)
            stats["failed_tts"] += 1
            item["commentary_audio_path"] = None
            return

        if success and Path(audio_path).exists():
            item["commentary_audio_path"] = audio_path
            stats["generated"] += 1
            logger.info("[AUDIO] %s → %s", item_key, audio_path)
        else:
            logger.warning("[AUDIO] TTS returned False for %s", item_key)
            stats["failed_tts"] += 1
            item["commentary_audio_path"] = None
