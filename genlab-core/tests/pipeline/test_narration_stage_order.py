"""NARR-05 (2026-08-19) — pin the stage ORDER that makes narration reach
the final mix, and the per-story VO filename that keeps it uncontaminated.

Class of bug guarded here: **producer scheduled after its only consumer.**

Every piece of the NARR-01 wire was individually correct — the writer
emitted ``narration_script``, ``GenerateAudio`` synthesised it,
``base_visual_render`` threaded ``narration_audio_path`` into
``blueprint_context``, and ``transformation_orchestrator`` built the
3-input mix. But ``GenerateAudio`` sat in the ``post_render`` parallel
group, four stages AFTER ``phase4_visual_render``, so the consumer always
read ``None``. Unit tests of each component passed; every published BB
reel was silent. Only the assembled ORDER was wrong, so only an
order-level assertion can catch it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_TEMPLATE = Path(__file__).resolve().parents[2] / "config" / "pipeline_template.yaml"

_GENERATE_AUDIO = "genlab_core.pipeline.stages.generate_audio.GenerateAudio"


def _stage_sequence() -> list[str]:
    """Flatten the backbone template into an ordered list of identifiers.

    ``class:`` entries contribute their dotted path; ``inject:`` entries
    contribute ``inject:<slot>``. Order is what matters, not resolution —
    the niche loader splices injects in place, so relative position in the
    template is relative position at runtime.
    """
    doc = yaml.safe_load(_TEMPLATE.read_text())
    sequence: list[str] = []
    for entry in doc["pipeline"]["stages"]:
        if "class" in entry:
            sequence.append(entry["class"])
        elif "inject" in entry:
            sequence.append(f"inject:{entry['inject']}")
    return sequence


class TestGenerateAudioPrecedesRender:
    def test_generate_audio_runs_before_visual_render(self):
        """The load-bearing assertion.

        ``base_visual_render._compose_frame`` reads
        ``media["audio_path"]`` as ``narration_audio_path`` and hands it
        to ``apply_post_render_transformations``. If GenerateAudio has
        not run by then, the NARR-01 3-input mix silently degrades to the
        legacy 2-input mix and the reel publishes mute.
        """
        sequence = _stage_sequence()
        assert _GENERATE_AUDIO in sequence, "GenerateAudio missing from backbone"
        assert "inject:phase4_visual_render" in sequence

        audio_at = sequence.index(_GENERATE_AUDIO)
        render_at = sequence.index("inject:phase4_visual_render")

        assert audio_at < render_at, (
            f"GenerateAudio (index {audio_at}) must run BEFORE "
            f"phase4_visual_render (index {render_at}) — the render is the "
            f"only consumer of the VO it produces. Full order: {sequence}"
        )

    def test_generate_audio_runs_after_qc_gates(self):
        """Placement floor: QCGates drops ``_skip_llm`` / incomplete
        stories, so synthesising before it would spend paid TTS calls on
        stories that never reach a renderer.
        """
        sequence = _stage_sequence()
        qc_at = sequence.index("genlab_core.pipeline.stages.qc_gates.QCGates")
        audio_at = sequence.index(_GENERATE_AUDIO)
        assert qc_at < audio_at, (
            "GenerateAudio must run AFTER QCGates so TTS is not spent on "
            "stories QCGates would have dropped"
        )

    def test_generate_audio_not_in_post_render_group(self):
        """The specific defect: ``parallel_group: post_render`` is what
        placed the producer after its consumer. Guard the exact shape.
        """
        doc = yaml.safe_load(_TEMPLATE.read_text())
        for entry in doc["pipeline"]["stages"]:
            if entry.get("class") == _GENERATE_AUDIO:
                assert entry.get("parallel_group") != "post_render", (
                    "GenerateAudio must not rejoin the post_render group — "
                    "that group runs after phase4_visual_render, which is "
                    "the consumer of its output"
                )
                return
        pytest.fail("GenerateAudio not found in backbone template")


class TestVoFilenameIsPerStory:
    """The collision that the re-ordering made dangerous.

    ``out_path`` was keyed on ``bp["candidate_id"]``, which is first
    assigned in ``push_to_backlog`` — four stages further downstream than
    GenerateAudio. The key never existed at synthesis time, so every
    story in every run wrote the literal path ``unknown_audio.mp3``.

    Inert while nothing read the file. Once GenerateAudio moved above the
    render so the mix consumes it, a multi-story run would mix story N's
    voice-over into story N-1's reel.
    """

    def test_distinct_stories_get_distinct_stems(self):
        from genlab_core.pipeline.stages.generate_audio import GenerateAudio

        a = GenerateAudio._audio_stem({"story_id": "a" * 64})
        b = GenerateAudio._audio_stem({"story_id": "b" * 64})
        assert a != b, "two stories must not share one VO artifact path"

    def test_story_id_preferred_over_absent_candidate_id(self):
        """At stage time ``candidate_id`` does not exist yet; ``story_id``
        is set by the ingestion fetchers and is what the renderer already
        uses to name composites.
        """
        from genlab_core.pipeline.stages.generate_audio import GenerateAudio

        stem = GenerateAudio._audio_stem({"story_id": "03348d8f9e0e30d0"})
        assert stem == "03348d8f9e0e30d0"

    def test_no_story_id_does_not_collide_into_shared_literal(self):
        """Regression guard on the exact observed prod artifact.

        Prod showed exactly one file per niche, ever:
        ``/tmp/genlab_audio/{niche}_manual/unknown_audio.mp3``. A story
        carrying either identifier must never resolve to that stem.
        """
        from genlab_core.pipeline.stages.generate_audio import GenerateAudio

        assert GenerateAudio._audio_stem({"candidate_id": "cand-123"}) != "unknown"
        assert GenerateAudio._audio_stem({"story_id": "s-1"}) != "unknown"

    def test_run_dir_uses_context_run_id_not_run_stats(self):
        """``run_id`` lives at ``context["run_id"]`` (pipeline_runner.py:358),
        never inside ``run_stats``. The old lookup made every run for a
        niche share one directory.
        """
        from genlab_core.pipeline.stages.generate_audio import GenerateAudio

        run_dir = GenerateAudio._get_run_dir(
            {
                "niche_id": "ai_creators",
                "run_id": "ai_creators_20260819_072015",
                "niche_config": {"niche_id": "ai_creators"},
                "run_stats": {},
            }
        )
        assert run_dir.name == "ai_creators_ai_creators_20260819_072015"
        assert "manual" not in run_dir.name


# ── Addition B (NARR-08) ──────────────────────────────────────────────
#
# The hoist is held by a line's INDEX in a YAML list. Four niches inherit
# the backbone template; gaming writes its own ``pipeline.stages`` and does
# NOT pick up template edits. Asserting the template alone would have let
# the fix apply to 4 niches of 5 and reported success.
#
# Resolved through the real loader (``merge_pipeline_template``), not by
# reading YAML, so inject expansion is exercised the way the runner does it.

_REPO = Path(__file__).resolve().parents[3]

_NICHE_CONFIGS = {
    "ai_creators": ("BlackboxBrief/config/niche.yaml", "BBVisualRenderStrategy"),
    "sports": ("ClutchWire/config/niche.yaml", "SportVisualRenderStrategy"),
    "movies": ("SpliceReel/config/niche.yaml", "MovieVisualRenderStrategy"),
    "anime": ("FrameDrift/config/niche.yaml", "AnimeVisualRenderStrategy"),
    "gaming": (
        "CriticalRush/niches/gaming/config/niche.yaml",
        "RenderGamingVideo",
    ),
}


def _effective_stages(rel_path: str) -> list[str]:
    from genlab_core.niche_loader import merge_pipeline_template

    raw = yaml.safe_load((_REPO / rel_path).read_text())
    merged = merge_pipeline_template(raw)
    return [s.get("class", "") for s in merged["pipeline"]["stages"]]


@pytest.mark.parametrize("niche", sorted(_NICHE_CONFIGS))
class TestEveryNicheOrdersAudioBeforeRender:
    def test_generate_audio_precedes_render(self, niche: str):
        rel_path, render_cls = _NICHE_CONFIGS[niche]
        stages = _effective_stages(rel_path)

        audio_at = next(
            (i for i, c in enumerate(stages) if c.endswith(".GenerateAudio")),
            None,
        )
        render_at = next(
            (i for i, c in enumerate(stages) if c.endswith("." + render_cls)),
            None,
        )
        assert audio_at is not None, f"{niche}: no GenerateAudio stage"
        assert render_at is not None, f"{niche}: no {render_cls} stage"
        assert audio_at < render_at, (
            f"{niche}: GenerateAudio at #{audio_at + 1} runs AFTER "
            f"{render_cls} at #{render_at + 1}. The render is the only "
            f"consumer of the VO this stage produces, so the mix will "
            f"silently fall back to the legacy 2-input path."
        )


class TestGamingKeepsCommentaryAudioAfterRender:
    """The counter-case that stops the fix from over-applying.

    ``GenerateGamingAudio`` reads ``media["rendered_path"]``
    (``generate_gaming_audio.py:113``) and skips any story without one.
    Hoisting it alongside the generic stage would make it skip every story
    silently — zero commentary, no error, no failing test. It writes
    ``commentary_audio_path``, a different key from the generic stage's
    ``media["audio_path"]``, so the two do not collide and do not need to
    move together.
    """

    def test_gaming_commentary_audio_still_after_render(self):
        stages = _effective_stages(_NICHE_CONFIGS["gaming"][0])
        commentary_at = next(
            (i for i, c in enumerate(stages) if c.endswith(".GenerateGamingAudio")),
            None,
        )
        render_at = next(
            (i for i, c in enumerate(stages) if c.endswith(".RenderGamingVideo")),
            None,
        )
        assert commentary_at is not None and render_at is not None
        assert commentary_at > render_at, (
            "GenerateGamingAudio must stay AFTER RenderGamingVideo — it "
            "depends on media['rendered_path'] and would silently skip "
            "every story if hoisted"
        )
