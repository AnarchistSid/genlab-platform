"""NARR-13 (2026-08-21) — GenerateAudio must not overwrite the writer's
specific degrade reason with the generic one.

The 2026-08-21 02:30 UTC fire degraded both ai_creators stories. The
journal said:

    [ai_creators] narration attempt 1 rejected (script_too_long) at wpm=141
    [ai_creators] narration attempt 2 also rejected (script_too_long)

The database said ``script_generation_failed``.

Those two reasons are opposites — one means the LLM produced nothing, the
other means it produced too much — and they call for opposite repairs
(loosen the budget vs. tighten the prompt). Reading the DB alone pointed at
the wrong one.

``base_writing._validate_narration_with_retry`` records the specific reason
at base_writing.py:842-843 and returns an empty script alongside it.
``GenerateAudio`` sees only the empty string, and used to stamp the generic
reason over the top.

These tests drive the real ``execute()`` path rather than reimplementing the
branch, so a guard cannot pass by sharing the defect's root cause.
"""
from __future__ import annotations

import pytest

from genlab_core.pipeline.stages.generate_audio import GenerateAudio


def _context(content: dict) -> dict:
    """Minimal StageContext (a TypedDict, so a plain dict is valid).

    The blueprint deliberately carries no hook/caption, so ``_build_script``
    returns "" and the loop hits ``skipped`` before touching TTS. The degrade
    branch under test runs first, which is all we need.
    """
    return {
        "stories": [{"candidate_id": "test-candidate", "content": content}],
        "niche_config": {"niche_id": "ai_creators", "audio": {"enabled": True}},
    }


@pytest.fixture
def narration_on(monkeypatch):
    """Force the gate open and stub TTS.

    Patches the SOURCE modules, not this stage — ``execute`` imports both
    lazily inside the function body, so no attribute exists on the importing
    module to patch (CLAUDE.md, 'Test patterns for lazy-imported dependencies').
    """
    monkeypatch.setattr(
        "genlab_core.publishing.narration_gate.is_narration_enabled_for",
        lambda niche_id, config: True,
    )
    monkeypatch.setattr(
        "genlab_core.tts.factory.build_tts_cascade",
        lambda *a, **k: object(),
    )


class TestDegradeReasonPreserved:
    def test_writer_reason_survives(self, narration_on):
        """The whole point: script_too_long must not become
        script_generation_failed."""
        content = {
            "narration_script": "",
            "narration_degraded": True,
            "narration_degraded_reason": "script_too_long",
        }
        ctx = _context(content)

        GenerateAudio().execute(ctx)

        assert content["narration_degraded_reason"] == "script_too_long", (
            "GenerateAudio overwrote the writer's specific reason. The DB "
            "will report 'LLM produced nothing' for a fire where the LLM "
            "produced too much, and the operator will tighten the prompt "
            "when they needed to loosen the budget."
        )

    @pytest.mark.parametrize(
        "reason",
        ["script_too_long", "contains_url", "contains_affiliate_pitch",
         "unsupported_first_person_claim"],
    )
    def test_every_writer_reason_survives(self, narration_on, reason):
        """Not just the fit reason — the compliance slugs matter as much,
        since each one names a different corrective instruction."""
        content = {"narration_script": "", "narration_degraded_reason": reason}
        GenerateAudio().execute(_context(content))
        assert content["narration_degraded_reason"] == reason

    def test_generic_reason_still_used_when_nobody_said_otherwise(
        self, narration_on
    ):
        """The fallback must survive the fix.

        A genuinely empty script with no upstream explanation — the legacy
        writer path, or any caller that never ran the validator — should
        still be labelled script_generation_failed rather than left blank.
        """
        content = {"narration_script": ""}
        GenerateAudio().execute(_context(content))
        assert content["narration_degraded"] is True
        assert content["narration_degraded_reason"] == "script_generation_failed"

    def test_blank_reason_is_treated_as_absent(self, narration_on):
        """An empty-or-whitespace reason is not an explanation."""
        content = {"narration_script": "", "narration_degraded_reason": "   "}
        GenerateAudio().execute(_context(content))
        assert content["narration_degraded_reason"] == "script_generation_failed"

    def test_narration_expected_still_stamped(self, narration_on):
        """Unrelated behaviour the mix-time WARN depends on — pinned so the
        reason fix doesn't disturb it."""
        content = {"narration_script": "", "narration_degraded_reason": "script_too_long"}
        GenerateAudio().execute(_context(content))
        assert content["narration_expected"] is True
