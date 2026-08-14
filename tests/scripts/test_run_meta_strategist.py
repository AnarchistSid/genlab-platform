"""Pin Phase 2.G meta-strategist runner shape:

  * `_monday_of` returns Monday of the given week
  * `_build_user_prompt` includes proposal-type accuracy math
  * `_parse_verdict` fails-open to grade='B' on bad JSON
  * `_parse_verdict` strips markdown fences
  * SYSTEM_PROMPT includes the five-grade scale
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

# Load script by path (scripts/ isn't a package)
_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "run_meta_strategist",
    _ROOT / "scripts" / "run_meta_strategist.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["run_meta_strategist"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestMondayOf:
    def test_wednesday_returns_that_weeks_monday(self):
        # 2026-08-12 was a Wednesday
        assert _MOD._monday_of(date(2026, 8, 12)) == date(2026, 8, 10)

    def test_monday_returns_itself(self):
        assert _MOD._monday_of(date(2026, 8, 10)) == date(2026, 8, 10)

    def test_sunday_returns_prior_monday(self):
        # 2026-08-16 is a Sunday
        assert _MOD._monday_of(date(2026, 8, 16)) == date(2026, 8, 10)


class TestUserPromptShape:
    def test_no_verdicts_notes_cold_start(self):
        signal = {"per_type": {}, "totals": {"reviewed": 0, "verdicts": 0}}
        prompt = _MOD._build_user_prompt(date(2026, 8, 10), signal)
        assert "no verdicts available" in prompt

    def test_includes_accuracy_math(self):
        signal = {
            "per_type": {
                "arm_add": {"total": 10, "improved": 7, "unchanged": 1, "regressed": 2},
            },
            "totals": {"reviewed": 10, "verdicts": 10},
        }
        prompt = _MOD._build_user_prompt(date(2026, 8, 10), signal)
        # 7 / (7 + 2) = 77.777...% → rounds to 78%
        assert "78%" in prompt
        assert "arm_add" in prompt

    def test_zero_denominator_shows_na(self):
        """All-unchanged bucket has no signal for improved/regressed accuracy."""
        signal = {
            "per_type": {
                "arm_add": {"total": 5, "improved": 0, "unchanged": 5, "regressed": 0},
            },
            "totals": {"reviewed": 5, "verdicts": 5},
        }
        prompt = _MOD._build_user_prompt(date(2026, 8, 10), signal)
        assert "N/A" in prompt


class TestParseVerdict:
    def test_valid_json_parses(self):
        raw = '{"overall_grade":"A","per_type_grades":{"arm_add":"A"},"recommendations":["ship more"]}'
        v = _MOD._parse_verdict(raw)
        assert v["overall_grade"] == "A"
        assert v["per_type_grades"] == {"arm_add": "A"}
        assert v["recommendations"] == ["ship more"]

    def test_bad_json_fails_open_to_B(self):
        v = _MOD._parse_verdict("not json at all")
        assert v["overall_grade"] == "B"
        assert v["per_type_grades"] == {}
        assert v["recommendations"] == []

    def test_strips_markdown_fences(self):
        raw = '```json\n{"overall_grade":"C","per_type_grades":{},"recommendations":[]}\n```'
        v = _MOD._parse_verdict(raw)
        assert v["overall_grade"] == "C"

    def test_grade_normalized_to_uppercase(self):
        raw = '{"overall_grade":"a"}'
        v = _MOD._parse_verdict(raw)
        assert v["overall_grade"] == "A"

    def test_missing_recommendations_defaults_empty(self):
        raw = '{"overall_grade":"B"}'
        v = _MOD._parse_verdict(raw)
        assert v["recommendations"] == []
        assert v["per_type_grades"] == {}


class TestSystemPromptContract:
    def test_includes_five_grade_scale(self):
        for g in ("'A'", "'B'", "'C'", "'D'", "'F'"):
            assert g in _MOD.META_SYSTEM_PROMPT, f"missing grade {g}"

    def test_forbids_prose_output(self):
        assert "JSON only" in _MOD.META_SYSTEM_PROMPT

    def test_cold_start_guidance(self):
        assert "insufficient data" in _MOD.META_SYSTEM_PROMPT
