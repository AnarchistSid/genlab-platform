"""Pins caption fitting: never sever the credit line to meet a char limit.

2026-09-17. Threads hard-failed on "Param text must be at most 500 characters
long" (movies 09-15 + 09-11, gaming 09-14). The naive fix -- caption[:500] --
would cut the credit line off the END of the caption, AFTER Layer 4 had already
validated it, producing silently uncredited posts. Instagram ships that exact
defect today as caption[:2200].
"""

import pytest
from genlab_core.platforms.caption_fit import fit_caption
from genlab_core.platforms.caption_validation import validate_caption_has_attribution

CREDIT = "🎬 Original: @ufc — https://youtube.com/watch?v=abc123"
TAGS = "#ufc #mma #knockout #fightnight #octagon"


def _caption(body_words: int) -> str:
    body = " ".join("Khabib" if i % 3 else "dominates" for i in range(body_words))
    return f"{body}\n\n{CREDIT}\n\n{TAGS}"


def test_short_caption_is_returned_untouched():
    c = _caption(3)
    r = fit_caption(c, 500)
    assert r.text == c and r.changed is False


@pytest.mark.parametrize("limit", [500, 280, 180])
def test_over_limit_caption_always_keeps_the_credit_line(limit):
    r = fit_caption(_caption(200), limit)
    assert len(r.text) <= limit, f"still over limit: {len(r.text)}"
    assert r.credit_preserved is True
    ok, reason = validate_caption_has_attribution(r.text, source_url=None)
    assert ok, f"Layer 4 would reject the fitted caption: {reason}"


def test_the_url_survives_not_just_the_marker():
    """A marker without its URL credits nobody."""
    r = fit_caption(_caption(200), 500)
    assert "https://youtube.com/watch?v=abc123" in r.text


def test_body_is_trimmed_before_hashtags_are_dropped():
    """Hashtags are discovery surface — only sacrificed when trimming isn't enough."""
    r = fit_caption(_caption(60), 500)
    assert r.dropped_hashtags is False
    assert "#ufc" in r.text


def test_hashtags_are_dropped_when_body_trimming_cannot_fit_it():
    tight = len(CREDIT) + len(TAGS) - 10
    r = fit_caption(_caption(200), tight)
    assert len(r.text) <= tight
    assert r.dropped_hashtags is True
    assert r.credit_preserved is True


def test_naive_tail_cut_would_have_failed_this_same_input():
    """Guards the ACTUAL regression: [:limit] severs the credit, fit_caption doesn't."""
    c = _caption(200)
    naive_ok, _ = validate_caption_has_attribution(c[:500], source_url=None)
    assert naive_ok is False, "fixture no longer reproduces the bug being pinned"
    assert fit_caption(c, 500).credit_preserved is True


def test_credit_longer_than_limit_reports_failure_rather_than_lying():
    r = fit_caption(_caption(50), 20)
    assert r.credit_preserved is False and len(r.text) <= 20


def test_caption_with_no_credit_line_still_fits():
    r = fit_caption("word " * 400, 500)
    assert len(r.text) <= 500 and r.changed is True
