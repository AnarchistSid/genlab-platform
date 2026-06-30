"""Per-platform source-arm pins (2026-06-30).

Locks in the contract for splitting ``source:<source>`` arms into
``source:<source>__<platform>`` arms, driven by data showing 38×
asymmetry between Facebook avg reward (0.114) and YouTube avg reward
(0.003) on the SAME blueprint set. Aggregating across platforms
destroys that signal — the bandit can never learn that a source is
great for Facebook but terrible for YouTube.

Pins:

  * ``_arm_id_for(source, platform=None)`` legacy → ``source:<source>``
  * ``_arm_id_for(source, platform="instagram")`` →
    ``source:<source>__instagram``
  * ``record_source_outcome(..., platform=...)`` writes the
    per-platform arm via UPSERT with the correct arm_id
  * ``record_source_outcome(..., platform=None)`` legacy path
    UNCHANGED (still writes aggregated arm)
  * ``parse_source_arm_id`` round-trips both shapes
  * ``list_source_performance(niche_id, platform="instagram")``
    issues a ``LIKE 'source:%__instagram'`` query
  * Legacy aggregated arms are NEVER mutated by the per-platform
    path (the SQL arm_id binding differs, ON CONFLICT can't collide)
  * ``_update_source_arm_reward(..., platform=...)`` threads the
    platform through to ``record_source_outcome``
  * Live wire passes ``task_record.platform`` at the call site
  * Backfill ``--apply`` invocation runs BOTH passes by default
    (aggregated + per-platform), each with its own idempotency
    stamp so re-runs only target rows with NULL stamp
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ── arm_id helper ───────────────────────────────────────────────────


def test_arm_id_for_no_platform_returns_legacy_shape():
    """``_arm_id_for(source)`` with no platform → ``source:<source>``
    (PR #571 byte-identical shape). The 4 existing aggregated arms
    in production were written with this shape; the per-platform
    extension MUST NOT change it."""
    from genlab_core.learning.source_performance import _arm_id_for

    assert _arm_id_for("youtube_trending") == "source:youtube_trending"
    assert _arm_id_for("reddit:aivideo") == "source:reddit:aivideo"


def test_arm_id_for_with_platform_returns_double_underscore_shape():
    """``_arm_id_for(source, platform="instagram")`` →
    ``source:<source>__instagram``. Matches the canonical
    ``content_type__platform`` format from meta_prior.py:100."""
    from genlab_core.learning.source_performance import _arm_id_for

    assert (
        _arm_id_for("youtube_trending", platform="instagram")
        == "source:youtube_trending__instagram"
    )
    assert _arm_id_for("reddit:aivideo", platform="facebook") == "source:reddit:aivideo__facebook"


def test_arm_id_for_empty_platform_returns_legacy_shape():
    """Defensive: empty-string platform treated as None so callers
    can pass ``platform=request.args.get('platform')`` without
    pre-validating."""
    from genlab_core.learning.source_performance import _arm_id_for

    # Note: _arm_id_for itself doesn't normalize; the call sites
    # (record_source_outcome / get_source_performance) do. This pin
    # documents the raw helper behavior so future refactors don't
    # accidentally normalize in two places.
    assert _arm_id_for("youtube_trending", platform="") == "source:youtube_trending"


# ── parse_source_arm_id round-trip ──────────────────────────────────


def test_parse_source_arm_id_legacy_aggregated():
    """``source:youtube_trending`` parses to (source, None)."""
    from genlab_core.learning.source_performance import parse_source_arm_id

    assert parse_source_arm_id("source:youtube_trending") == ("youtube_trending", None)


def test_parse_source_arm_id_per_platform():
    """``source:youtube_trending__instagram`` parses to
    (youtube_trending, instagram)."""
    from genlab_core.learning.source_performance import parse_source_arm_id

    assert parse_source_arm_id("source:youtube_trending__instagram") == (
        "youtube_trending",
        "instagram",
    )
    assert parse_source_arm_id("source:reddit:aivideo__facebook") == (
        "reddit:aivideo",
        "facebook",
    )


def test_parse_source_arm_id_handles_missing_prefix():
    """Defensive: input without ``source:`` prefix returned verbatim
    (no platform extraction). Matches existing PR #571 _row_to_performance
    permissive parse for hand-written arm_ids."""
    from genlab_core.learning.source_performance import parse_source_arm_id

    assert parse_source_arm_id("youtube_trending") == ("youtube_trending", None)


def test_parse_source_arm_id_round_trip():
    """Composition pin: parse(arm_id(s, p)) == (s, p) for every
    (source, platform) pair the system will write."""
    from genlab_core.learning.source_performance import _arm_id_for, parse_source_arm_id

    for source in ("youtube_trending", "reddit:aivideo", "twitch_clips"):
        for platform in ("facebook", "instagram", "youtube", "threads", "twitter"):
            arm_id = _arm_id_for(source, platform=platform)
            assert parse_source_arm_id(arm_id) == (source, platform)


# ── record_source_outcome per-platform UPSERT ──────────────────────


def test_record_source_outcome_with_platform_writes_per_platform_arm(monkeypatch):
    """``record_source_outcome(..., platform="instagram")`` issues the
    UPSERT with arm_id=``source:youtube_trending__instagram``. The
    SQL parameters carry the per-platform arm_id; the aggregated arm
    row is NEVER touched."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        ok = source_performance.record_source_outcome(
            "gaming", "youtube_trending", 0.5, platform="instagram"
        )
    assert ok is True
    # Bound params: (niche, arm_id, reward, 1-reward, n_plays, reward, 1-reward, n_plays)
    bound = fake_conn.execute.call_args.args[1]
    assert bound[0] == "gaming"
    assert bound[1] == "source:youtube_trending__instagram"


def test_record_source_outcome_without_platform_writes_aggregated_arm(monkeypatch):
    """``record_source_outcome(...)`` with no platform writes the
    legacy aggregated arm — preserves PR #571b behaviour byte-for-byte
    for any caller that hasn't been updated."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        ok = source_performance.record_source_outcome("gaming", "youtube_trending", 0.5)
    assert ok is True
    bound = fake_conn.execute.call_args.args[1]
    assert bound[1] == "source:youtube_trending"  # legacy aggregated arm_id


def test_record_source_outcome_normalizes_empty_platform(monkeypatch):
    """Empty-string platform treated as None so the wire layer can
    pass through any platform value (including missing) safely."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        source_performance.record_source_outcome("gaming", "youtube_trending", 0.5, platform="")
    bound = fake_conn.execute.call_args.args[1]
    # Empty string → None → legacy aggregated arm_id
    assert bound[1] == "source:youtube_trending"


def test_record_source_outcome_per_platform_uses_same_upsert(monkeypatch):
    """Critical migration-safety pin: the SQL statement is the same
    INSERT ... ON CONFLICT (niche_id, arm_id) DO UPDATE shape; only
    the bound arm_id differs. This guarantees the aggregated arm
    (different arm_id binding) can never be touched by the per-platform
    update path — the ON CONFLICT can't match the aggregated row."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        source_performance.record_source_outcome(
            "gaming", "youtube_trending", 0.5, platform="instagram"
        )
    sql = str(fake_conn.execute.call_args.args[0])
    assert "ON CONFLICT (niche_id, arm_id)" in sql
    assert "INSERT INTO bandit_arms" in sql


# ── list_source_performance platform filter ────────────────────────


def test_list_source_performance_no_platform_returns_all_arms(monkeypatch):
    """Legacy call without platform kwarg → returns all source arms
    (aggregated + per-platform). Matches PR #571 SQL contract."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = []
    fake_conn.execute.return_value = fake_cursor

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        source_performance.list_source_performance("gaming")
    bound = fake_conn.execute.call_args.args[1]
    assert bound[1] == "source:%"  # matches aggregated AND per-platform arms


def test_list_source_performance_with_platform_filters_to_per_platform(monkeypatch):
    """Platform kwarg → LIKE pattern restricted to per-platform arms
    for that platform only. ``source:%__instagram`` matches
    ``source:youtube_trending__instagram`` but NOT
    ``source:youtube_trending`` (legacy) or ``source:foo__youtube``."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = []
    fake_conn.execute.return_value = fake_cursor

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        source_performance.list_source_performance("gaming", platform="instagram")
    bound = fake_conn.execute.call_args.args[1]
    assert bound[1] == "source:%__instagram"


def test_list_source_performance_empty_platform_treated_as_no_filter(monkeypatch):
    """Empty-string platform → no filter (matches all source arms).
    Lets the dashboard pass ``?platform=`` without special-casing."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = []
    fake_conn.execute.return_value = fake_cursor

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        source_performance.list_source_performance("gaming", platform="")
    bound = fake_conn.execute.call_args.args[1]
    assert bound[1] == "source:%"  # legacy LIKE pattern


def test_list_source_performance_dto_carries_platform(monkeypatch):
    """When the SELECT returns a per-platform row, the DTO surfaces
    the platform field for the dashboard. Aggregated rows have
    platform=None."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [
        {
            "niche_id": "ai_creators",
            "arm_id": "source:youtube_trending__instagram",
            "alpha": 8.0,
            "beta": 2.0,
            "n_plays": 10,
        },
        {
            "niche_id": "ai_creators",
            "arm_id": "source:youtube_trending",
            "alpha": 5.0,
            "beta": 5.0,
            "n_plays": 10,
        },
    ]
    fake_conn.execute.return_value = fake_cursor

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        rows = source_performance.list_source_performance("ai_creators")
    assert len(rows) == 2
    # Per-platform row
    assert rows[0].source == "youtube_trending"
    assert rows[0].platform == "instagram"
    # Aggregated row (legacy)
    assert rows[1].source == "youtube_trending"
    assert rows[1].platform is None


# ── get_source_performance per-platform lookup ─────────────────────


def test_get_source_performance_with_platform_looks_up_per_platform_arm(monkeypatch):
    """``get_source_performance(niche, source, platform="instagram")``
    queries the per-platform arm row, not the aggregated row."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = None
    fake_conn.execute.return_value = fake_cursor

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        source_performance.get_source_performance(
            "gaming", "youtube_trending", platform="instagram"
        )
    bound = fake_conn.execute.call_args.args[1]
    assert bound[1] == "source:youtube_trending__instagram"


def test_get_source_performance_without_platform_looks_up_aggregated(monkeypatch):
    """Backward-compat: no platform kwarg → legacy aggregated arm
    lookup (PR #571 shape)."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = None
    fake_conn.execute.return_value = fake_cursor

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        source_performance.get_source_performance("gaming", "youtube_trending")
    bound = fake_conn.execute.call_args.args[1]
    assert bound[1] == "source:youtube_trending"


# ── _update_source_arm_reward platform threading ───────────────────


def test_update_source_arm_reward_threads_platform_through():
    """Live wire helper with ``platform="instagram"`` forwards platform
    to ``record_source_outcome``."""
    from genlab_core.learning.metric_collector import _update_source_arm_reward

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {
        "id": "bp-1",
        "fields": {"source": "youtube_trending"},
    }
    with (
        patch("genlab_core.http.backlog_client.BacklogClient", return_value=fake_client),
        patch(
            "genlab_core.learning.source_performance.record_source_outcome",
            return_value=True,
        ) as mock_record,
    ):
        _update_source_arm_reward(
            niche_id="ai_creators",
            blueprint_id="bp-1",
            reward=0.7,
            platform="instagram",
        )
    mock_record.assert_called_once_with(
        niche_id="ai_creators",
        source="youtube_trending",
        reward=0.7,
        platform="instagram",
    )


def test_update_source_arm_reward_default_platform_is_none():
    """No-platform call site (legacy callers, if any remain) passes
    platform=None — preserves PR #571b aggregated-arm semantics."""
    from genlab_core.learning.metric_collector import _update_source_arm_reward

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {
        "id": "bp-1",
        "fields": {"source": "youtube_trending"},
    }
    with (
        patch("genlab_core.http.backlog_client.BacklogClient", return_value=fake_client),
        patch(
            "genlab_core.learning.source_performance.record_source_outcome",
            return_value=True,
        ) as mock_record,
    ):
        _update_source_arm_reward(
            niche_id="ai_creators",
            blueprint_id="bp-1",
            reward=0.7,
        )
    mock_record.assert_called_once_with(
        niche_id="ai_creators",
        source="youtube_trending",
        reward=0.7,
        platform=None,
    )


def test_live_call_site_passes_task_record_platform():
    """Source-text pin: the metric_collector call site at the 48h
    reward-window-close MUST pass ``platform=task_record.platform``.
    A future refactor that drops the kwarg silently regresses the
    bandit to single-aggregated-arm behavior — pin it."""
    from pathlib import Path

    import genlab_core.learning.metric_collector as mod

    src = Path(mod.__file__).read_text()
    # The call site is the LAST occurrence of _update_source_arm_reward(
    # in the file (the def comes earlier). Find the closing ) and
    # verify the body includes platform=task_record.platform.
    last_call_idx = src.rindex("_update_source_arm_reward(")
    # Search forward for the matching close-paren; the helper is called
    # with kwargs only so the body is bounded by the next ``)``.
    tail = src[last_call_idx : last_call_idx + 500]
    assert "platform=task_record.platform" in tail, (
        "metric_collector call site MUST pass platform=task_record.platform — "
        "otherwise the per-platform bandit silently regresses to single-arm."
    )


# ── migration safety: aggregated arms are never mutated ────────────


def test_per_platform_path_does_not_touch_aggregated_arm_id(monkeypatch):
    """Critical migration-safety pin: the per-platform UPSERT binds
    a different arm_id than the aggregated arm. Even though both rows
    share (niche, source) prefix, the UNIQUE constraint is
    (niche_id, arm_id) — different arm_ids → different rows → no
    cross-talk possible. This pins the property at the SQL-binding
    level so a future "let's combine the two paths" refactor surfaces."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    arm_ids_written: list[str] = []

    def _capture(sql, params):
        arm_ids_written.append(params[1])
        return MagicMock()

    fake_conn.execute.side_effect = _capture

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        # Per-platform write
        source_performance.record_source_outcome(
            "ai_creators", "youtube_trending", 0.5, platform="facebook"
        )
        # Per-platform write for different platform — distinct arm_id
        source_performance.record_source_outcome(
            "ai_creators", "youtube_trending", 0.0, platform="youtube"
        )

    assert "source:youtube_trending__facebook" in arm_ids_written
    assert "source:youtube_trending__youtube" in arm_ids_written
    # The legacy aggregated arm_id is NEVER bound on any per-platform
    # write — the 4 existing aggregated arms can't be touched.
    assert "source:youtube_trending" not in arm_ids_written


# ── backfill — per-platform pass exists & uses distinct stamp ──────


def _fake_row(*, pa_id, niche_id="gaming", platform="instagram", source="youtube_trending"):
    return {
        "pa_id": pa_id,
        "niche_id": niche_id,
        "platform": platform,
        "post_id": "vid-1",
        "published_at": "2026-06-25T10:00:00+00:00",
        "pa_views": 5000,
        "pa_likes": 100,
        "pa_comments": 20,
        "pa_shares": 10,
        "pa_saves": 5,
        "blueprint_id": "bp-" + pa_id[-1:],
        "source": source,
        "a_value": 5000.0,
        "a_extra": {"reach": 5000, "engagement_rate": 0.02},
    }


def _patch_psycopg(rows: list[dict]) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.rowcount = len(rows)
    cursor.__enter__ = lambda self: self
    cursor.__exit__ = lambda *a: False
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda *a: False
    return conn


def test_backfill_main_runs_both_passes_by_default(monkeypatch):
    """Default ``main(["--apply"])`` runs BOTH the aggregated pass
    AND the per-platform pass. For a single eligible row that's:
    1 aggregated write + 1 per-platform write = 2 record_source_outcome
    calls; aggregated platform=None, per-platform platform=<actual>."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    from genlab_core.scripts import backfill_bandit_from_history as mod

    rows = [_fake_row(pa_id="11111111-1111-1111-1111-111111111111")]
    conn = _patch_psycopg(rows)

    with (
        patch("psycopg.connect", return_value=conn),
        patch(
            "genlab_core.learning.source_performance.record_source_outcome",
            return_value=True,
        ) as mock_record,
    ):
        rc = mod.main(["--apply"])

    assert rc == 0
    # 2 calls = 1 aggregated + 1 per-platform
    assert mock_record.call_count == 2
    platforms_passed = [c.kwargs.get("platform") for c in mock_record.call_args_list]
    # One call has platform=None (aggregated pass), one has
    # platform=<row.platform> (per-platform pass). Order matches
    # main()'s pass ordering.
    assert None in platforms_passed
    assert "instagram" in platforms_passed


def test_backfill_per_platform_pass_uses_distinct_stamp_key(monkeypatch):
    """The per-platform pass MUST query for ``platform_backfilled_at
    IS NULL`` (its own stamp), not the legacy ``bandit_backfilled_at``.
    Otherwise rows backfilled-aggregated before this PR ship would
    skip the per-platform pass and never populate per-platform arms."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    from genlab_core.scripts import backfill_bandit_from_history as mod

    conn = _patch_psycopg([])
    with patch("psycopg.connect", return_value=conn):
        mod.main(["--apply", "--skip-aggregated"])

    cur = conn.cursor.return_value
    # The only SELECT issued was the per-platform pass — check its WHERE
    sql, params = cur.execute.call_args.args
    assert "platform_backfilled_at" in sql
    assert "bandit_backfilled_at" not in sql


def test_backfill_aggregated_pass_uses_legacy_stamp_key(monkeypatch):
    """Conversely, --skip-per-platform isolates the aggregated pass
    which MUST still use the PR #571b stamp key ``bandit_backfilled_at``
    so the existing 4 aggregated arms' idempotency state survives."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    from genlab_core.scripts import backfill_bandit_from_history as mod

    conn = _patch_psycopg([])
    with patch("psycopg.connect", return_value=conn):
        mod.main(["--apply", "--skip-per-platform"])

    cur = conn.cursor.return_value
    sql, params = cur.execute.call_args.args
    assert "bandit_backfilled_at" in sql
    assert "platform_backfilled_at" not in sql


def test_backfill_per_platform_pass_passes_platform_to_record(monkeypatch):
    """The per-platform pass forwards each row's platform column to
    record_source_outcome so per-platform arms are written."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    from genlab_core.scripts import backfill_bandit_from_history as mod

    rows = [
        _fake_row(pa_id="11111111-1111-1111-1111-111111111111", platform="facebook"),
        _fake_row(pa_id="22222222-2222-2222-2222-222222222222", platform="youtube"),
    ]
    conn = _patch_psycopg(rows)
    with (
        patch("psycopg.connect", return_value=conn),
        patch(
            "genlab_core.learning.source_performance.record_source_outcome",
            return_value=True,
        ) as mock_record,
    ):
        mod.main(["--apply", "--skip-aggregated"])

    # 2 rows × 1 pass (aggregated skipped) = 2 calls
    assert mock_record.call_count == 2
    platforms_passed = [c.kwargs.get("platform") for c in mock_record.call_args_list]
    assert sorted(platforms_passed) == ["facebook", "youtube"]


def test_backfill_aggregated_pass_passes_platform_none(monkeypatch):
    """The aggregated pass MUST pass platform=None even though each
    row has a platform column — this preserves the legacy single-arm
    behaviour for the aggregated stamp. The 4 production aggregated
    arms accumulate via this pass."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    from genlab_core.scripts import backfill_bandit_from_history as mod

    rows = [_fake_row(pa_id="33333333-3333-3333-3333-333333333333", platform="facebook")]
    conn = _patch_psycopg(rows)
    with (
        patch("psycopg.connect", return_value=conn),
        patch(
            "genlab_core.learning.source_performance.record_source_outcome",
            return_value=True,
        ) as mock_record,
    ):
        mod.main(["--apply", "--skip-per-platform"])

    assert mock_record.call_count == 1
    # Aggregated pass: platform kwarg is None even though row.platform=facebook
    assert mock_record.call_args.kwargs.get("platform") is None


# ── input validation invariants survive the platform extension ─────


def test_record_outcome_clamps_reward_in_per_platform_path(monkeypatch, caplog):
    """The Beta-posterior clamp at [0, 1] survives the platform
    extension — caller bugs can't poison per-platform arms either."""
    import logging

    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        with caplog.at_level(logging.WARNING):
            source_performance.record_source_outcome(
                "gaming", "youtube_trending", 1.5, platform="instagram"
            )
    bound = fake_conn.execute.call_args.args[1]
    # Reward clamped to 1.0; arm_id is per-platform shape
    assert bound[1] == "source:youtube_trending__instagram"
    assert bound[2] == 1.0
    assert bound[3] == 0.0


def test_source_performance_dataclass_still_frozen_with_platform():
    """The platform field addition MUST NOT break the dataclass
    immutability contract (PR #571 frozen=True). Consumers (dashboard,
    proposer) assume the snapshot can't be mutated mid-evaluation."""
    import dataclasses

    from genlab_core.learning.source_performance import SourcePerformance

    sp = SourcePerformance(
        niche_id="gaming",
        source="youtube_trending",
        platform="instagram",
        alpha=10.0,
        beta=5.0,
        n_plays=15,
        reward_mean=10.0 / 15.0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        sp.platform = "youtube"  # type: ignore[misc]
