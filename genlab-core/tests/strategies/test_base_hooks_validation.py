"""R-52: the HookValidator must run on the LLM hook path too (it previously
only validated template hooks), and enforce a hard ≤60-char cap.

Also covers the validator's opt-in ``max_chars`` rule directly.
"""

from __future__ import annotations

from pathlib import Path

from genlab_core.intelligence.hook_validator import HookFailure, HookValidator
from genlab_core.strategies.base_hooks import BaseHookStrategy

_MINIMAL_TEMPLATES = """\
hooks:
  formulas:
    - "{game} did something"
  story_categories:
    default:
      formulas:
        - "{game} did something"
"""


class _StubHookStrategy(BaseHookStrategy):
    """Concrete strategy with the two abstract hooks stubbed out."""

    def _classify_story(self, story: dict) -> str:
        return "default"

    def _substitute_placeholders(self, formula: str, story: dict) -> str:
        return formula.replace("{game}", story.get("title", "the game"))


def _strategy(tmp_path: Path) -> _StubHookStrategy:
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "templates.yaml").write_text(_MINIMAL_TEMPLATES)
    return _StubHookStrategy("gaming", tmp_path)


def _ctx(hook: str) -> dict:
    return {
        "stories": [
            {
                "title": "Valve Deadlock patch",
                "content": {"hook": hook, "written_by": "llm"},
            }
        ]
    }


# ── R-52: validator max_chars rule ───────────────────────────────────


def test_validator_max_chars_flags_over_60() -> None:
    long_hook = "This hook is quite a bit longer than sixty characters for sure!"
    assert len(long_hook) > 60
    vr = HookValidator().validate(long_hook, platform="instagram", max_chars=60)
    assert not vr.passed
    assert HookFailure.HOOK_TOO_LONG in vr.failures


def test_validator_max_chars_default_off() -> None:
    # Without max_chars, the loose instagram 2200 limit applies (back-compat).
    long_hook = "x " * 50
    vr = HookValidator().validate(long_hook, platform="instagram")
    assert HookFailure.HOOK_TOO_LONG not in vr.failures


# ── R-52: LLM hook path now validated ────────────────────────────────


def test_valid_llm_hook_passes_through(tmp_path: Path) -> None:
    ctx = _ctx("Valve shipped a wild new Deadlock patch")
    out = _strategy(tmp_path).execute(ctx)
    assert out["stories"][0]["content"]["hook"] == "Valve shipped a wild new Deadlock patch"
    assert out["run_stats"]["hooks"]["skipped_llm"] == 1


def test_oversize_llm_hook_is_salvaged_to_60(tmp_path: Path) -> None:
    long_hook = "This LLM hook clearly runs way past the sixty character ceiling we enforce"
    ctx = _ctx(long_hook)
    out = _strategy(tmp_path).execute(ctx)
    result = out["stories"][0]["content"]["hook"]
    assert len(result) <= 60
    assert result  # not dropped


def test_markdown_llm_hook_is_cleaned(tmp_path: Path) -> None:
    ctx = _ctx("**Insane** new Valve update just dropped for fans")
    out = _strategy(tmp_path).execute(ctx)
    result = out["stories"][0]["content"]["hook"]
    assert "**" not in result
    assert result
