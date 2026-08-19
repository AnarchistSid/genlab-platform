"""NARR-11 (2026-08-20) — the validator is actually reachable, and retries.

``validate_narration_script`` shipped with 20 pin tests and zero production
callers. Every one of those tests passed while the function was dead code —
the fifth built-never-wired bug of this arc. Unit tests prove a function is
correct; they say nothing about whether anything calls it.
"""
from __future__ import annotations

import inspect
import pathlib
import re
from typing import Any

import pytest

from genlab_core.strategies import base_writing
from genlab_core.strategies.base_writing import BaseWritingStrategy

_SRC = pathlib.Path(base_writing.__file__).resolve().parents[1]


class _S(BaseWritingStrategy):
    def _model_route_key(self) -> str:
        return "test"


def _strategy(tmp_path, **narration) -> _S:
    s = _S("ai_creators", tmp_path)
    s._niche_config = {"narration": {"enabled": True, **narration}}
    return s


class TestReachability:
    """The assertion whose absence let a dead validator ship.

    Generalised deliberately — this is the shape the #222 contract cycle
    should adopt: *every safety check has a production caller.*
    """

    SAFETY_FUNCTIONS = ("validate_narration_script",)

    @pytest.mark.parametrize("func", SAFETY_FUNCTIONS)
    def test_safety_check_has_a_production_caller(self, func: str):
        defining = f"def {func}("
        callers: list[str] = []
        for path in _SRC.rglob("*.py"):
            text = path.read_text(errors="ignore")
            if defining in text:
                continue  # the module that defines it doesn't count
            if re.search(rf"\b{re.escape(func)}\s*\(", text):
                callers.append(str(path.relative_to(_SRC)))

        assert callers, (
            f"{func} has NO production caller. It is dead code with passing "
            "unit tests — exactly the state narration_validator shipped in "
            "for two days while narration scripts went unvalidated."
        )

    def test_write_path_reaches_the_validator(self):
        """Specific to the writer: the landing point invokes the wire."""
        src = inspect.getsource(BaseWritingStrategy._write_story_llm)
        assert "_validate_narration_with_retry" in src
        wire = inspect.getsource(
            BaseWritingStrategy._validate_narration_with_retry
        )
        assert "validate_narration_script" in wire


class TestRetryThenDegrade:
    """One retry, then degrade with the validator's own reason slug."""

    def _patch_writer(self, monkeypatch, scripts: list[str]) -> dict:
        """Feed successive narration_script values to the retry path."""
        calls: dict[str, Any] = {"targets": [], "instructions": []}

        def fake_write(**kwargs):
            calls["targets"].append(kwargs.get("narration_target_seconds"))
            calls["instructions"].append(kwargs.get("extra_instructions", ""))
            return {"narration_script": scripts.pop(0) if scripts else ""}

        monkeypatch.setattr(
            "genlab_core.writing.video_content_writer.write_video_content",
            fake_write,
        )
        return calls

    def test_too_long_retries_with_tightened_budget_then_passes(
        self, monkeypatch, tmp_path
    ):
        long_script = " ".join(["word"] * 200)   # ~80s at 150wpm
        short_script = " ".join(["word"] * 20)   # ~8s
        calls = self._patch_writer(monkeypatch, [short_script])
        s = _strategy(tmp_path)

        out, reason = s._validate_narration_with_retry(
            script=long_script, target_seconds=16.0, video={},
            llm_client=object(), existing_hooks=[], extra_instructions="",
        )

        assert reason == "", f"expected recovery, degraded with {reason}"
        assert out == short_script
        assert calls["targets"] == [pytest.approx(16.0 * 0.85)], (
            "the retry must tighten the budget to 85% of the original"
        )

    def test_too_long_twice_degrades_with_reason(self, monkeypatch, tmp_path):
        long_script = " ".join(["word"] * 200)
        self._patch_writer(monkeypatch, [long_script])
        s = _strategy(tmp_path)

        out, reason = s._validate_narration_with_retry(
            script=long_script, target_seconds=16.0, video={},
            llm_client=object(), existing_hooks=[], extra_instructions="",
        )

        assert out == ""
        assert reason == "script_too_long"

    def test_compliance_failure_appends_corrective_instruction(
        self, monkeypatch, tmp_path
    ):
        """URL rejection retries with an explicit correction in the prompt."""
        bad = "Check out https://example.com for the full breakdown of this."
        good = " ".join(["word"] * 20)
        calls = self._patch_writer(monkeypatch, [good])
        s = _strategy(tmp_path)

        out, reason = s._validate_narration_with_retry(
            script=bad, target_seconds=16.0, video={},
            llm_client=object(), existing_hooks=[], extra_instructions="base",
        )

        assert reason == ""
        assert out == good
        assert calls["targets"] == [16.0], (
            "a compliance retry must NOT tighten the budget — only a fit "
            "failure does"
        )
        assert "NO URLs" in calls["instructions"][0]
        assert calls["instructions"][0].startswith("base")

    def test_empty_script_degrades_without_retry(self, monkeypatch, tmp_path):
        calls = self._patch_writer(monkeypatch, [])
        s = _strategy(tmp_path)

        out, reason = s._validate_narration_with_retry(
            script="  ", target_seconds=16.0, video={},
            llm_client=object(), existing_hooks=[], extra_instructions="",
        )

        assert (out, reason) == ("", "script_generation_failed")
        assert calls["targets"] == [], "no LLM spend on an empty script"

    def test_writer_exception_degrades_rather_than_raising(
        self, monkeypatch, tmp_path
    ):
        def boom(**_kwargs):
            raise RuntimeError("llm down")

        monkeypatch.setattr(
            "genlab_core.writing.video_content_writer.write_video_content", boom
        )
        s = _strategy(tmp_path)

        out, reason = s._validate_narration_with_retry(
            script=" ".join(["word"] * 200), target_seconds=16.0, video={},
            llm_client=object(), existing_hooks=[], extra_instructions="",
        )
        assert (out, reason) == ("", "script_too_long")


class TestRateConfig:
    def test_missing_config_falls_back_to_safe_default(self):
        from genlab_core.publishing.narration_gate import get_tts_rate_wpm

        assert get_tts_rate_wpm(None) in (141, 150)
        assert get_tts_rate_wpm({"narration": {}}) in (141, 150)
        assert get_tts_rate_wpm({"narration": {"tts_rates": "junk"}}) == 150

    def test_inworld_rate_is_the_measured_value(self, monkeypatch):
        from genlab_core.publishing.narration_gate import get_tts_rate_wpm

        monkeypatch.setenv("GENLAB_INFSH_TTS_ENABLED", "1")
        assert get_tts_rate_wpm(None) == 141, (
            "Inworld measured ~6% slower than 150wpm on 2026-08-20; "
            "under-predicting the rate is what produced two rejected renders"
        )
        monkeypatch.setenv("GENLAB_INFSH_TTS_ENABLED", "0")
        assert get_tts_rate_wpm(None) == 150


class TestPromptCapMatchesValidatorRate:
    """The prompt's word cap and the validator's rate are one contract.

    NARR-11 created this divergence and it blocked round 3 entirely: the cap
    was hardcoded at 150 wpm while the validator moved to a measured 141, so
    every compliant script projected over budget and was rejected — including
    every retry, which inherits the mismatch.
    """

    def test_cap_computed_at_the_validator_rate_fits_the_budget(self):
        import re

        from genlab_core.writing.narration_validator import (
            project_tts_duration_seconds,
            validate_narration_script,
        )
        from genlab_core.writing.video_content_writer import _build_narration_hint

        target, wpm, tail = 16.0, 141, 2.0
        hint = _build_narration_hint(target, wpm)
        cap = int(re.search(r"HARD word cap: (\d+)", hint).group(1))

        at_cap = " ".join(["word"] * cap)
        projected = project_tts_duration_seconds(at_cap, wpm)
        assert projected <= target - tail, (
            f"a script at the prompt's own cap ({cap} words) projects "
            f"{projected:.2f}s against a {target - tail:.1f}s budget — the "
            "prompt is asking for something the validator will always reject"
        )

        ok, reason = validate_narration_script(at_cap, target, wpm, tail)
        assert ok, f"script at the advertised cap was rejected: {reason}"

    def test_mismatched_rate_is_what_breaks_it(self):
        """Characterise the defect so the fix above is anchored."""
        import re

        from genlab_core.writing.narration_validator import (
            project_tts_duration_seconds,
        )
        from genlab_core.writing.video_content_writer import _build_narration_hint

        cap_150 = int(
            re.search(r"HARD word cap: (\d+)", _build_narration_hint(16.0, 150)).group(1)
        )
        assert project_tts_duration_seconds(" ".join(["w"] * cap_150), 141) > 14.0, (
            "the 150-wpm cap must overrun a 141-wpm budget — if this stops "
            "holding, the rates have converged and this test is obsolete"
        )
