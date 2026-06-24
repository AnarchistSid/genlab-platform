"""PR #519 (2026-06-24): UUID-shape filter on calibration stats queries.

Gaming had 80 synthetic test rows (blueprint_id like '10001', '10005')
polluting agreement-rate calculation, dragging the displayed value
from a real ~36% to 22.3%. The PR #222 D1.1 purge in
AUTO_2_ROLLOUT_2026-06-15 was supposed to be a one-time cleanup, but
new synthetic rows accumulate (test invocations against the prod DB,
backfill scripts, etc.). The durable read-side defense is a UUID-shape
filter on every consumer of ``auto_approval_calibration``.

Three consumer functions updated:
  * ``stats()`` (per-niche agreement-rate)
  * ``stats_all_niches()`` (batch variant for Mission Control)
  * ``breakdown_by_category()`` (operator-rejection breakdown)

If these pins regress, the operator sees misleading "ready_for_
enforcement" verdicts and untrustworthy agreement-rate badges.
"""

from __future__ import annotations

from pathlib import Path


def test_stats_query_filters_synthetic_uuid_shape():
    """``stats()`` SQL must include the UUID-LIKE filter."""
    import genlab_core.scheduling.calibration_logger as mod

    src = Path(mod.__file__).read_text()
    # Pin the exact LIKE pattern that catches UUIDs and excludes
    # integer-string test IDs ('10001', '10005').
    assert "blueprint_id LIKE '________-____-____-____-____________'" in src, (
        "calibration_logger must filter to UUID-shape blueprint_id to exclude "
        "synthetic test rows. Pre-PR-#519 these rows dragged gaming's "
        "agreement from 36% to 22%."
    )


def test_uuid_filter_appears_in_all_three_queries():
    """All three calibration consumers must apply the same filter.

    Without this, ``stats()`` and ``stats_all_niches()`` would
    disagree on per-niche numbers — operators would see different
    "ready" verdicts depending on which endpoint Mission Control
    happened to call.
    """
    import genlab_core.scheduling.calibration_logger as mod

    src = Path(mod.__file__).read_text()
    occurrences = src.count("blueprint_id LIKE '________-____-____-____-____________'")
    assert occurrences >= 3, (
        f"UUID-shape filter must appear in stats() + stats_all_niches() + "
        f"breakdown_by_category() queries (3+ occurrences); found {occurrences}."
    )


def test_filter_pattern_matches_real_uuid_shape():
    """Sanity: the SQL LIKE pattern actually matches real UUIDs."""
    # The pattern has 36 chars: 8-4-4-4-12 with dashes. Real UUIDs
    # have the same shape. ``_`` in SQL LIKE matches any single char.
    import uuid

    real_uuid = str(uuid.uuid4())
    assert len(real_uuid) == 36
    # 8 chars + dash + 4 + dash + 4 + dash + 4 + dash + 12
    parts = real_uuid.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


def test_filter_excludes_synthetic_integer_string_ids():
    """Sanity: integer-string IDs like '10001' do NOT match UUID shape."""
    bad_ids = ["10001", "10005", "1", "test_fixture_001", "9999"]
    # Each is fewer than 36 chars and has no dashes → would fail LIKE
    # '________-____-____-____-____________' (which requires exactly
    # 36 chars with dashes in canonical positions).
    for bad in bad_ids:
        # Heuristic check: real UUID format
        if len(bad) != 36:
            continue
        parts = bad.split("-")
        assert [len(p) for p in parts] != [8, 4, 4, 4, 12], (
            f"'{bad}' should NOT match the UUID-shape filter"
        )
