"""Pins the writer's LLM-refusal detector (task #526, 2026-07-06).

Real bugs caught on 2026-07-05/06 during publish-day watch:

* anime      "I need to stop here and flag a critical issue. The..."
* ai_creators "I need the Story title and Summary to write a hook for."

Both were persisted as blueprint hooks. Nightly-schedule cron in PR
#702 filtered them at scheduling time (defense-in-depth), but the
underlying writer should reject them at generation. This pin encodes
the upstream reject.

If these tests regress, the writer will persist LLM refusal preambles
as reel hooks — a 100% funnel leak because publisher renders the
refusal onto video and ships to all 5 channels.
"""

from __future__ import annotations

from genlab_core.writing.video_content_writer import (
    _LLM_REFUSAL_PREFIXES,
    _is_llm_refusal,
)


# ── prefix inventory ────────────────────────────────────────────────


def test_prefixes_include_the_two_real_bug_patterns():
    """Both patterns caught in prod 2026-07-05/06 must be matched.
    If either drops from _LLM_REFUSAL_PREFIXES, that specific
    refusal-shape passes through the writer's validation."""
    prefixes_lower = {p.lower() for p in _LLM_REFUSAL_PREFIXES}
    # anime real bug
    assert "i need to stop" in prefixes_lower
    # ai_creators real bug
    assert "i need the" in prefixes_lower


def test_prefixes_include_classic_anthropic_refusals():
    """Defense in depth — Anthropic safety refusals evolve, but the
    common preambles are stable. Dropping any of these lets a
    safety-triggered refusal ship as a reel hook."""
    prefixes_lower = {p.lower() for p in _LLM_REFUSAL_PREFIXES}
    for expected in (
        "i cannot",
        "i can't help",
        "i can't provide",
        "i can't write",
        "i am unable",
        "i'm sorry",
        "i apologize",
    ):
        assert expected in prefixes_lower, (
            f"missing refusal prefix {expected!r} — Anthropic safety "
            "refusal starting with this phrase would ship as reel hook."
        )


# ── _is_llm_refusal behavior ────────────────────────────────────────


def test_real_anime_refusal_detected():
    hook = "I need to stop here and flag a critical issue. The..."
    assert _is_llm_refusal(hook) is True


def test_real_ai_creators_refusal_detected():
    hook = "I need the Story title and Summary to write a hook for."
    assert _is_llm_refusal(hook) is True


def test_case_insensitive():
    """LLM output capitalization varies — match must be case-insensitive."""
    assert _is_llm_refusal("i need to stop here") is True
    assert _is_llm_refusal("I NEED TO STOP HERE") is True
    assert _is_llm_refusal("I Need The Story") is True


def test_leading_whitespace_tolerated():
    """LLM JSON output sometimes prefixes whitespace or newlines."""
    assert _is_llm_refusal("   I need to stop here") is True
    assert _is_llm_refusal("\n\nI cannot help with that") is True


def test_legitimate_hook_containing_phrase_mid_sentence_is_NOT_refusal():
    """The detector matches PREFIXES only. A legitimate hook that
    happens to contain a refusal phrase later in the sentence isn't
    a refusal."""
    hook = "The reason I cannot stop thinking about this is wild"
    assert _is_llm_refusal(hook) is False


def test_normal_hook_is_NOT_refusal():
    """Sanity — a real hook doesn't trigger."""
    assert (
        _is_llm_refusal("The secret technique hiding on every Star Wars ship")
        is False
    )


def test_empty_string_is_NOT_refusal():
    """Empty hook is already handled by other validators; refusal
    check must not crash on it."""
    assert _is_llm_refusal("") is False


def test_none_is_NOT_refusal():
    """The writer may pass None through — must not crash."""
    assert _is_llm_refusal(None) is False  # type: ignore[arg-type]


def test_non_string_is_NOT_refusal():
    """Defense against upstream type drift."""
    assert _is_llm_refusal(123) is False  # type: ignore[arg-type]
    assert _is_llm_refusal([]) is False  # type: ignore[arg-type]


# ── source-grep pin: writer actually calls the detector ──────────────


def test_writer_source_calls_is_llm_refusal():
    """Source-grep pin: ``video_content_writer.write_video_content``
    must invoke ``_is_llm_refusal`` on the returned hook. A refactor
    that adds a new refusal check function but forgets to wire it into
    write_video_content wouldn't catch the real bug this task closes.

    Preferred to an integration test because write_video_content's
    real signature requires an ``llm_client`` mock that duplicates a
    lot of production plumbing for a single conditional's coverage.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "src" / "genlab_core" / "writing" / "video_content_writer.py"
    )
    content = src.read_text()
    assert "_is_llm_refusal(hook)" in content, (
        "write_video_content no longer calls _is_llm_refusal(hook) — "
        "refusal-shape LLM output will ship as reel hook again."
    )
    assert 'content["hook"] = ""' in content, (
        "The refusal branch must clear the hook (empty string signal to "
        "downstream skip). If this line changes shape, verify manually."
    )
