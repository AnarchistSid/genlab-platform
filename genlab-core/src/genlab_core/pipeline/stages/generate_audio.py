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

from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)


class GenerateAudio:
    """Generate voiceover audio for stories using TTS cascade.

    Reads: context['stories'], context['niche_config']
    Writes: context['stories'][*]['media']['audio_path'],
            context['run_stats']['audio']
    """

    def execute(self, context: StageContext) -> StageContext:
        blueprints = context.get("stories", [])
        if not blueprints:
            logger.info("[GenerateAudio] No stories for audio generation")
            return context

        config = context.get("niche_config", {})
        audio_cfg = config.get("audio", {})

        if not audio_cfg.get("enabled", True):
            logger.info("[GenerateAudio] Audio disabled in niche config")
            return context

        # Late import to avoid hard dependency when TTS providers aren't installed
        try:
            from genlab_core.tts.factory import build_tts_cascade
        except ImportError:
            logger.warning("[GenerateAudio] genlab_core.tts not available, skipping")
            return context

        # NARR-01 (2026-08-18): gate check happens ONCE at the top so
        # the per-blueprint loop reads narration_script when on. When
        # off, this stage's behavior is byte-identical to pre-NARR-01
        # (script built from hook+caption via _build_script).
        niche_id = config.get("niche_id", "unknown")
        narration_enabled = False
        try:
            from genlab_core.publishing.narration_gate import (
                is_narration_enabled_for,
            )

            narration_enabled = is_narration_enabled_for(niche_id, config)
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning(
                "[GenerateAudio] narration gate check raised: %s "
                "— falling back to legacy hook+caption script source",
                exc,
            )

        cascade = build_tts_cascade()
        generated = 0
        skipped = 0
        errors = 0

        run_dir = self._get_run_dir(context)

        for bp in blueprints:
            # NARR-01: when narration enabled AND the writer emitted a
            # non-empty narration_script, use that as the TTS input
            # (commentary voice-over). Otherwise fall back to the
            # legacy hook+caption concat. If narration_enabled but
            # the writer left narration_script empty, mark the
            # blueprint as degraded with reason=script_generation_failed
            # so the operator query in the plan §4 catches it.
            script = ""
            content = bp.get("content") if isinstance(bp.get("content"), dict) else {}
            if narration_enabled:
                # NARR-05 (2026-08-19): stamp the gate result onto the
                # story so downstream consumers can tell "this reel was
                # SUPPOSED to have narration" from "this niche never
                # wanted narration". ``transformation_orchestrator``
                # uses it to decide whether a missing VO at mix time is
                # an anomaly worth a WARN or just a non-canary niche.
                #
                # This is only reachable because GenerateAudio now runs
                # BEFORE phase4_visual_render — under the old ordering
                # the render had already finished by the time this ran.
                content["narration_expected"] = True
                narration_script = str(content.get("narration_script", "")).strip()
                if narration_script:
                    script = narration_script
                else:
                    content["narration_degraded"] = True
                    content["narration_degraded_reason"] = "script_generation_failed"
                    logger.warning(
                        "[GenerateAudio] narration enabled for %s but "
                        "writer emitted empty narration_script — "
                        "degrading to legacy audio path (bp=%s)",
                        niche_id,
                        bp.get("candidate_id", "?"),
                    )
            if not script:
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
                out_path = run_dir / f"{self._audio_stem(bp)}_audio.mp3"

                # 2026-06-14: dropped the ``voice=voice`` kwarg.
                # TTSCascade.synthesize(text, output_path, clean=True) has no
                # ``voice`` parameter — neither does TTSProvider.synthesize on
                # any of the cascade's providers (ElevenLabs / OpenAI / Edge /
                # gTTS). Voices are configured per-provider at construction
                # time (e.g. ELEVENLABS_VOICE_ID env var), not per-call.
                #
                # Until this fix, every TTS attempt raised
                # ``TypeError: TTSCascade.synthesize() got an unexpected
                # keyword argument 'voice'`` — surfaced live in prod logs
                # as 31 occurrences in 24h, one per pipeline run × niche.
                # The downstream silence-trim / ffmpeg branches never ran;
                # the pipeline degraded to no-audio renders.
                #
                # Per-call voice routing is a separate feature (would need
                # to extend both TTSCascade.synthesize and every
                # TTSProvider.synthesize) — out of scope here; we just
                # unblock the existing per-provider voice config.
                result = cascade.synthesize(
                    text=script,
                    output_path=str(out_path),
                )

                if result and out_path.exists():
                    # Sub-item B: Remove leading silence from TTS output
                    trimmed_path = out_path.parent / f"{out_path.stem}_trimmed{out_path.suffix}"
                    try:
                        import subprocess

                        from genlab_core.media.ffmpeg import get_ffmpeg_binary

                        subprocess.run(
                            [
                                get_ffmpeg_binary(),
                                "-y",
                                "-i",
                                str(out_path),
                                "-af",
                                "silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB",
                                str(trimmed_path),
                            ],
                            check=True,
                            capture_output=True,
                            timeout=30,
                        )
                        if trimmed_path.exists() and trimmed_path.stat().st_size > 100:
                            trimmed_path.replace(out_path)
                            logger.debug(
                                "[GenerateAudio] Trimmed leading silence from %s", out_path.name
                            )
                    except Exception:
                        pass  # Non-fatal — use untrimmed audio

                    # NARR-01 A4 (2026-08-18): post-synth VO duration
                    # probe. The pre-synth wpm=150 projection is
                    # baseline-optimistic — Edge-TTS often runs slower
                    # than 150 wpm on longer phrases. ffprobe the
                    # actual duration against the clip fit-window and
                    # mark vo_overrun if it exceeds. Consumer
                    # (transformation_orchestrator) reads this marker
                    # and skips the 3-input mix, degrading to legacy
                    # 2-input path.
                    if narration_enabled:
                        try:
                            self._check_vo_overrun(
                                out_path=out_path,
                                bp=bp,
                                niche_id=niche_id,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "[GenerateAudio] vo_overrun probe raised "
                                "for %s: %s — narration proceeds (probe "
                                "failures don't block VO)",
                                bp.get("candidate_id", "?"),
                                exc,
                            )

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
            generated,
            skipped,
            errors,
        )

        context.setdefault("run_stats", {})["audio"] = {
            "generated": generated,
            "skipped": skipped,
            "errors": errors,
        }

        return context

    @staticmethod
    def _build_script(bp: dict[str, Any]) -> str:
        """Build TTS script from hook + caption body text.

        Caption storage canonicalisation (RENDER #5 fix, 2026-06-13). The
        canonical store is ``bp["content"]["caption"]`` (set by
        :meth:`genlab_core.strategies.base_writing.BaseWritingStrategy._populate_content`
        at base_writing.py:238). The pre-fix code read ``bp["body"]`` or
        ``bp["caption"]`` directly, which both default to empty in every
        production blueprint shape — meaning TTS narrated ONLY the hook
        (~63 chars) instead of the full caption (150-300 chars) for all
        5 niches since the strategy unification in Sprint 64. Same shape
        bug lives at qc_gates.py:218; virality_scoring.py:180 already has
        the correct lookup pattern.

        Hook similarly canonicalises through ``hook_text`` (Postgres column
        name) and ``hook`` (legacy dict key) — both are populated by the
        writer.
        """
        hook = bp.get("hook") or bp.get("hook_text") or ""

        content = bp.get("content")
        if isinstance(content, dict):
            body = content.get("caption") or ""
        else:
            body = ""

        # Backward-compat fallbacks for older blueprint shapes (e.g. legacy
        # BlackboxBrief pre-strategy-unification dicts). Production blueprints
        # since Sprint 64 always go through the content[caption] path above.
        if not body:
            body = bp.get("body") or bp.get("caption") or ""

        if not isinstance(body, str):
            body = ""

        script = f"{hook}. {body}".strip() if hook else body.strip()
        return script if len(script) > 10 else ""

    @staticmethod
    def _check_vo_overrun(
        out_path: Path,
        bp: dict[str, Any],
        niche_id: str,
    ) -> None:
        """NARR-01 A4 (2026-08-18): probe actual VO duration and set
        the ``vo_overrun`` degraded marker when it exceeds the clip's
        fit window.

        Rationale for a post-synth probe on top of the pre-synth
        validator: the validator projects duration from wpm=150
        which is a baseline. Actual TTS output can be slower
        (Edge-TTS averages ~130 wpm on long phrases) or the
        provider added prosodic pauses the projection didn't
        account for. This catches those edge cases before the
        3-input mix is attempted.

        Never raises — non-fatal probe failures leave the blueprint
        unmarked and the caller (transformation_orchestrator) still
        attempts the VO mix. Only a definitive overrun sets the
        degraded marker.
        """
        media = bp.get("media", {})
        clip_duration = None
        for candidate in (
            media.get("clip_duration_seconds"),
            media.get("duration_seconds"),
            bp.get("duration_seconds"),
        ):
            if isinstance(candidate, (int, float)) and candidate > 0:
                clip_duration = float(candidate)
                break
        if clip_duration is None:
            return  # unknown clip length — can't compare

        # Same 2s tail buffer the validator uses. Kept in sync
        # deliberately — narration_gate.get_narration_config exposes
        # the value for callers that need to override it per niche.
        tail_buffer_seconds = 2.0
        fit_budget = clip_duration - tail_buffer_seconds

        import subprocess

        from genlab_core.media.ffmpeg import get_ffprobe_binary

        probe = subprocess.run(
            [
                get_ffprobe_binary(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        raw = (probe.stdout or "").strip()
        if not raw:
            return
        try:
            actual_seconds = float(raw)
        except ValueError:
            return

        if actual_seconds > fit_budget:
            content = bp.setdefault("content", {})
            content["narration_degraded"] = True
            content["narration_degraded_reason"] = "vo_overrun"
            logger.warning(
                "[GenerateAudio] vo_overrun for %s niche=%s: actual "
                "VO=%.2fs > fit_budget=%.2fs (clip=%.2fs, tail=%.1fs). "
                "Degrading to legacy 2-input mix — narration skipped.",
                bp.get("candidate_id", "?"),
                niche_id,
                actual_seconds,
                fit_budget,
                clip_duration,
                tail_buffer_seconds,
            )

    @staticmethod
    def _audio_stem(bp: dict[str, Any]) -> str:
        """Stable per-story filename stem for the VO artifact.

        NARR-05 (2026-08-19). The original stem was
        ``bp.get("candidate_id", "unknown")`` — but ``story["candidate_id"]``
        is first assigned in :mod:`push_to_backlog` (push_to_backlog.py:2310),
        which runs FOUR stages after this one. The key therefore never
        existed at synthesis time and every blueprint in every run wrote to
        the literal path ``unknown_audio.mp3``.

        That was inert while nothing consumed ``media["audio_path"]``. Once
        GenerateAudio moved above ``phase4_visual_render`` so the NARR-01
        3-input mix can read the VO, the collision became audience-facing:
        on a multi-story run, story N's voice-over would be mixed into
        story N-1's reel.

        ``story_id`` is assigned by the ingestion fetchers (stage 2-3, e.g.
        fetch_tmdb_trailers.py:167) and is the same key
        ``base_visual_render.py:151`` uses to name composite artifacts, so
        VO and video artifacts now share one identity.
        """
        for key in ("story_id", "candidate_id"):
            value = bp.get(key)
            if value:
                # Filesystem-safe + bounded: story_ids are 64-char hex, the
                # renderer truncates to 16 for its own artifacts.
                stem = "".join(ch for ch in str(value) if ch.isalnum() or ch in "-_")[:32]
                if stem:
                    return stem
        return "unknown"

    @staticmethod
    def _get_run_dir(context: dict[str, Any]) -> Path:
        """Resolve or create run directory for audio artifacts."""
        niche_config = context.get("niche_config", {})
        niche_id = niche_config.get("niche_id", "unknown")
        # NARR-05 (2026-08-19): read run_id from ``context["run_id"]`` —
        # where ``pipeline_runner`` actually writes it (pipeline_runner.py:358).
        # The previous lookup went to ``context["run_stats"]["run_id"]``, a key
        # no stage ever sets, so EVERY run for a niche resolved to the same
        # ``{niche}_manual`` directory. Combined with the candidate_id defect
        # below that produced exactly one ``unknown_audio.mp3`` per niche —
        # ever — silently overwritten on each run. ``run_stats`` is kept as a
        # fallback so any caller that does populate it keeps working.
        run_stats = context.get("run_stats", {})
        run_id = context.get("run_id") or run_stats.get("run_id") or "manual"

        run_dir = Path(tempfile.gettempdir()) / "genlab_audio" / f"{niche_id}_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir
