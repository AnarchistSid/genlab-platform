"""Pipeline stage: Generate voiceover audio via 4-tier TTS cascade.

Delegates to ``genlab_core.tts.TTSCascade`` which tries providers in order:
  1. ElevenLabs (highest quality, paid)
  2. OpenAI TTS (good quality, paid)
  3. Edge-TTS (free, decent)
  4. gTTS (free, basic fallback)

For each blueprint with hook + body text:
  1. Concatenate hook + body into script
  2. Run TTSCascade to generate WAV/MP3
  3. Store audio path in blueprint['media']['audio_path']

Non-fatal: TTS failures leave blueprints without audio (video publishes silent).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class GenerateAudio:
    """Generate voiceover audio for blueprints using TTS cascade.

    Reads: context['blueprints'], context['niche_config']
    Writes: context['blueprints'][*]['media']['audio_path'],
            context['run_stats']['audio']
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        blueprints = context.get("blueprints", [])
        if not blueprints:
            logger.info("[GenerateAudio] No blueprints for audio generation")
            return context

        config = context.get("niche_config", {})
        audio_cfg = config.get("audio", {})

        if not audio_cfg.get("enabled", True):
            logger.info("[GenerateAudio] Audio disabled in niche config")
            return context

        # Late import to avoid hard dependency when TTS providers aren't installed
        try:
            from genlab_core.tts.cascade import TTSCascade
        except ImportError:
            logger.warning("[GenerateAudio] genlab_core.tts not available, skipping")
            return context

        cascade = TTSCascade()
        generated = 0
        skipped = 0
        errors = 0

        run_dir = self._get_run_dir(context)

        for bp in blueprints:
            script = self._build_script(bp)
            if not script:
                skipped += 1
                continue

            # Skip if audio already exists
            media = bp.setdefault("media", {})
            if media.get("audio_path") and Path(media["audio_path"]).exists():
                skipped += 1
                continue

            try:
                voice = audio_cfg.get("voice", "default")
                out_path = run_dir / f"{bp.get('candidate_id', 'unknown')}_audio.mp3"

                result = cascade.synthesize(
                    text=script,
                    output_path=str(out_path),
                    voice=voice,
                )

                if result and out_path.exists():
                    media["audio_path"] = str(out_path)
                    media["audio_provider"] = getattr(result, "provider", "unknown")
                    generated += 1
                else:
                    skipped += 1
            except Exception:
                logger.exception(
                    "[GenerateAudio] TTS failed for %s",
                    bp.get("candidate_id", "unknown"),
                )
                errors += 1

        logger.info(
            "[GenerateAudio] %d generated, %d skipped, %d errors",
            generated, skipped, errors,
        )

        context.setdefault("run_stats", {})["audio"] = {
            "generated": generated,
            "skipped": skipped,
            "errors": errors,
        }

        return context

    @staticmethod
    def _build_script(bp: dict[str, Any]) -> str:
        """Build TTS script from hook + body text."""
        hook = bp.get("hook", "")
        body = bp.get("body", bp.get("caption", ""))
        if not isinstance(body, str):
            body = ""
        script = f"{hook}. {body}".strip() if hook else body.strip()
        return script if len(script) > 10 else ""

    @staticmethod
    def _get_run_dir(context: dict[str, Any]) -> Path:
        """Resolve or create run directory for audio artifacts."""
        niche_config = context.get("niche_config", {})
        niche_id = niche_config.get("niche_id", "unknown")
        run_stats = context.get("run_stats", {})
        run_id = run_stats.get("run_id", "manual")

        run_dir = Path(tempfile.gettempdir()) / "genlab_audio" / f"{niche_id}_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir
