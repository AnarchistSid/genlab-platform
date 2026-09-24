"""Pins for the status-aware reveal (ANIME-17 §5, enforced)."""

from __future__ import annotations

from genlab_core.still import reveal as R


def _story(status, **kw):
    s = {"status": status, "start_date": {"year": 2025, "month": 7, "day": 11}}
    s.update(kw)
    return s


def test_an_airing_show_never_says_premiering():
    """TOUGEN ANKI has been airing since July 2025 and v4 slammed
    '11 JULY 2025' over it, on the PV's own broadcast-schedule frame."""
    for status in (R.RELEASING, R.FINISHED):
        rv = R.choose(_story(status, next_airing={"episode": 13, "airingAt": 1790949600}))
        assert rv is not None
        low = rv.text.lower()
        assert "premier" not in low
        assert "11 july" not in low and "2025" not in low


def test_an_airing_show_names_the_episode_a_viewer_can_watch():
    """nextAiringEpisode is the one that has NOT aired."""
    rv = R.choose(_story(R.RELEASING, next_airing={"episode": 13, "airingAt": 1790949600}))
    assert rv.text == "EP 12 OUT NOW"


def test_an_upcoming_show_does_give_the_date():
    rv = R.choose(_story(R.NOT_YET_RELEASED))
    assert rv.text == "11 JULY"
    assert rv.status == R.NOT_YET_RELEASED


def test_a_finished_show_says_where_to_watch():
    assert R.choose(_story(R.FINISHED)).text == "NOW STREAMING"


def test_an_unknown_status_says_nothing():
    assert R.choose(_story("CANCELLED")) is None
    assert R.choose(_story("")) is None
