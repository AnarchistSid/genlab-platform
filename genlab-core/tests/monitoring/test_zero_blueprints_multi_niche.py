"""Four zeros on one day is an outage; one zero is a Tuesday.

Fixture is the real 2026-09-20 shape: anime, gaming, movies and ai_creators
at zero, sports at 3, every insert failing with

    column "source_url" of relation "blueprints" does not exist

The per-niche check could not see it — it reasons about one niche at a time
and one niche at zero is ordinary. Counting NICHES is what makes it visible,
because the niches share no sources (YouTube categories, AniList, Reddit,
Steam, Twitch) but do share the writer, the store and the schema.
"""

from __future__ import annotations

import pytest
from genlab_core.monitoring.checks import pipeline as P

REAL_ERROR = 'column "source_url" of relation "blueprints" does not exist'

YESTERDAY = {"sports": 3, "anime": 0, "gaming": 0, "movies": 0, "ai_creators": 0}
TODAY = {"sports": 3, "anime": 3, "gaming": 2, "movies": 4, "ai_creators": 1}


class _Row(dict):
    def __getitem__(self, k):
        return dict.__getitem__(self, k)


def _fake_conn(counts: dict[str, int], errors: dict[str, str]):
    class _Cur:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _Conn:
        def execute(self, sql, params=None):
            if "error_message" in sql:
                return _Cur([_Row(niche_id=n, error_message=e) for n, e in errors.items()])
            return _Cur([_Row(niche_id=n, n=c) for n, c in counts.items() if c])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _Conn()


@pytest.fixture
def patched(monkeypatch):
    def _apply(counts, errors=None):
        monkeypatch.setattr(
            P, "pg_connect", lambda *a, **k: _fake_conn(counts, errors or {})
        )
    return _apply


class TestMultiNicheZero:
    def test_yesterdays_four_zeros_are_critical(self, patched):
        patched(YESTERDAY, {n: REAL_ERROR for n in ("anime", "gaming", "movies", "ai_creators")})
        alerts = P.check_zero_blueprints_across_niches()
        assert len(alerts) == 1
        a = alerts[0]
        assert a.severity == "critical"
        assert a.check == "zero_blueprints_multi_niche"
        assert a.details["silent_niches"] == ["ai_creators", "anime", "gaming", "movies"]

    def test_the_error_is_named_per_niche_not_just_counted(self, patched):
        """A CRITICAL reporting only a count sends the operator to the journal,
        which is exactly where this hid for a day."""
        patched(YESTERDAY, {n: REAL_ERROR for n in ("anime", "gaming", "movies", "ai_creators")})
        a = P.check_zero_blueprints_across_niches()[0]
        assert REAL_ERROR in a.message
        assert all(REAL_ERROR in v for v in a.details["errors"].values())

    def test_today_is_quiet(self, patched):
        patched(TODAY)
        assert P.check_zero_blueprints_across_niches() == []

    def test_one_silent_niche_is_a_data_outcome(self, patched):
        """Nothing trending, a relevance gate doing its job, a dedup day."""
        patched({**TODAY, "anime": 0})
        assert P.check_zero_blueprints_across_niches() == []

    def test_two_silent_niches_is_already_an_outage(self, patched):
        patched({**TODAY, "anime": 0, "movies": 0})
        alerts = P.check_zero_blueprints_across_niches()
        assert len(alerts) == 1 and alerts[0].severity == "critical"

    def test_a_niche_missing_from_the_result_set_counts_as_zero(self, patched):
        """GROUP BY returns no row for a niche with no blueprints at all."""
        patched({"sports": 3})
        a = P.check_zero_blueprints_across_niches()[0]
        assert set(a.details["silent_niches"]) == {"anime", "gaming", "movies", "ai_creators"}

    def test_it_says_so_when_no_error_was_recorded(self, patched):
        patched(YESTERDAY, {})
        assert "no error recorded" in P.check_zero_blueprints_across_niches()[0].message

    def test_a_db_failure_does_not_take_the_run_down(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(P, "pg_connect", _boom)
        assert P.check_zero_blueprints_across_niches() == []
