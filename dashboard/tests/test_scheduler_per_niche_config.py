"""Regression pin: ``_next_available_slot`` resolves the per-niche
``publishing.yaml`` before falling back to the shared genlab-core
config.

PR #509 (2026-06-24). Pre-PR, the function always loaded
``$BACKLOG_CONFIG_PATH/../publishing.yaml`` — on prod this resolved to
``/opt/genlab/genlab-core/config/publishing.yaml`` (ai_creators) for
every niche, regardless of which niche was being scheduled. The bug
stayed silent because every per-niche publishing.yaml currently
declares the same ``schedule_slots: ["12:00"]``, but became a real
bug the first time any niche diverged.

The fix mirrors the canonical pattern in
``publish_all_platforms.py:175-195`` — same import, same fallback
ladder (nested gaming layout → flat per-niche layout → shared
genlab-core).

If this test fails: the per-niche resolution path regressed and all
niches go back to sharing one config. The symptom is subtle and
silent until a niche diverges, so the pin matters even when slots
happen to match.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def per_niche_env(monkeypatch, tmp_path):
    """Build a fake workspace with DIVERGENT per-niche publishing.yaml
    slots, so the test can prove the right per-niche file was loaded.

    Layout mirrors prod:

        <tmp>/
            CriticalRush/niches/gaming/config/publishing.yaml  → 11:30 only
            SpliceReel/config/publishing.yaml                   → 14:00 only
            genlab-core/config/publishing.yaml                  → 09:00 only (fallback)
            genlab-core/config/lists_config.yaml                → BACKLOG_CONFIG_PATH

    If the function reads the per-niche file: gaming → 11:30,
    movies → 14:00. If it reads the shared genlab-core fallback for
    BOTH: 09:00 for both — that's the pre-PR-#509 silent-broken state.
    """
    # Per-niche files
    cr_cfg = tmp_path / "CriticalRush" / "niches" / "gaming" / "config"
    cr_cfg.mkdir(parents=True)
    (cr_cfg / "publishing.yaml").write_text(
        'instagram:\n  schedule_slots: ["11:30"]\n  timezone: "Asia/Kolkata"\n'
    )
    sr_cfg = tmp_path / "SpliceReel" / "config"
    sr_cfg.mkdir(parents=True)
    (sr_cfg / "publishing.yaml").write_text(
        'instagram:\n  schedule_slots: ["14:00"]\n  timezone: "Asia/Kolkata"\n'
    )
    # Shared genlab-core fallback (intentionally different from both)
    gc_cfg = tmp_path / "genlab-core" / "config"
    gc_cfg.mkdir(parents=True)
    (gc_cfg / "publishing.yaml").write_text(
        'instagram:\n  schedule_slots: ["09:00"]\n  timezone: "Asia/Kolkata"\n'
    )
    (gc_cfg / "lists_config.yaml").write_text("# noop\n")
    monkeypatch.setenv("BACKLOG_CONFIG_PATH", str(gc_cfg / "lists_config.yaml"))

    # Point the canonical resolver at the temp workspace
    monkeypatch.setenv("GENLAB_ROOT", str(tmp_path))

    yield tmp_path


@pytest.fixture(autouse=True)
def _stub_optimal_time_learner(monkeypatch):
    """Disable the learner — its DB read would fail in CI and we only
    want to test which yaml file the slot-picker loaded.
    """
    from genlab_core.scheduling import optimal_time_learner as otl

    monkeypatch.setattr(otl, "optimal_slots_hhmm", lambda *a, **kw: [])
    yield


@pytest.fixture(autouse=True)
def _stub_backlog_client(monkeypatch):
    """Avoid hitting the real BacklogClient — slot-picker only needs
    'no existing scheduled blueprints' to walk slots freely.

    The module uses ``_get_client()`` to obtain a client (lazy import
    from ``graph_sync``), so patch that helper rather than chasing the
    underlying class through layered modules.
    """
    from server.core import publishing_queue as mod

    class _StubBlueprints:
        def all(self, *a, **kw):
            return []

    class _StubClient:
        blueprints = _StubBlueprints()

    monkeypatch.setattr(mod, "_get_client", lambda: _StubClient())
    yield


@pytest.fixture(autouse=True)
def frozen_ist_now(monkeypatch):
    """Freeze the scheduler clock at 06:00 IST so every candidate slot
    is ahead of the +1h earliest threshold. Earliest = 07:00 IST →
    11:30 / 14:00 / 09:00 all qualify on today's date."""
    from datetime import datetime as real_datetime

    ist = ZoneInfo("Asia/Kolkata")
    fixed = real_datetime(2026, 6, 24, 6, 0, 0, tzinfo=ist)

    class _FixedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr("server.core.publishing_queue.datetime", _FixedDateTime)
    yield


"""
IST/UTC note for assertions below:

The scheduler returns UTC ISO 8601 (suffix 'Z'). The fixture's
``schedule_slots`` are declared in Asia/Kolkata (IST = UTC+5:30):

    11:30 IST → 06:00 UTC   (gaming fixture)
    14:00 IST → 08:30 UTC   (movies fixture)
    09:00 IST → 03:30 UTC   (genlab-core fallback fixture)

Assert against the UTC representation; comparing IST strings against
a UTC return value silently fails for the right reason but the WRONG
diagnostic.
"""


def test_gaming_loads_critical_rush_nested_publishing_yaml(per_niche_env):
    """gaming → CriticalRush/niches/gaming/config/publishing.yaml.

    The slot value '11:30 IST' (06:00 UTC) lives ONLY in that file.
    If the function instead loaded the shared genlab-core fallback,
    the result would be at 03:30 UTC (09:00 IST).
    """
    from server.core.publishing_queue import _next_available_slot

    iso = _next_available_slot(niche_id="gaming")
    assert iso, "scheduler must return a slot"
    assert "T06:00:00" in iso, (
        f"gaming must pick slot 11:30 IST (06:00 UTC) from CriticalRush/"
        f"niches/gaming/config/publishing.yaml, got {iso!r} — likely fell "
        "back to shared genlab-core config (regression of PR #509)."
    )


def test_movies_loads_splicereel_flat_publishing_yaml(per_niche_env):
    """movies → SpliceReel/config/publishing.yaml (flat layout, no
    ``niches/`` subdirectory). Slot '14:00 IST' (08:30 UTC) lives ONLY
    there."""
    from server.core.publishing_queue import _next_available_slot

    iso = _next_available_slot(niche_id="movies")
    assert iso, "scheduler must return a slot"
    assert "T08:30:00" in iso, (
        f"movies must pick slot 14:00 IST (08:30 UTC) from SpliceReel/"
        f"config/publishing.yaml, got {iso!r} — likely fell back to "
        "shared genlab-core config (regression of PR #509)."
    )


def test_no_niche_id_falls_back_to_legacy_path(per_niche_env):
    """Backward compat: when ``niche_id`` is empty, the legacy
    ``BACKLOG_CONFIG_PATH``-parent / shared-genlab-core lookup must
    still work. Slot '09:00 IST' (03:30 UTC) is in the shared fallback
    file."""
    from server.core.publishing_queue import _next_available_slot

    iso = _next_available_slot(niche_id="")
    assert iso, "scheduler must return a slot even without niche_id"
    assert "T03:30:00" in iso, (
        f"empty niche_id must fall back to the legacy genlab-core "
        f"shared publishing.yaml (slot 09:00 IST / 03:30 UTC), got {iso!r}."
    )


def test_unknown_niche_falls_back_to_legacy_path(per_niche_env):
    """Defensive: a niche_id NOT in ``NICHE_DIR_NAMES`` must not crash;
    it falls through to the legacy path. Pins ``except`` handling."""
    from server.core.publishing_queue import _next_available_slot

    iso = _next_available_slot(niche_id="nonexistent_niche")
    assert iso, "scheduler must return a slot even for unknown niche"
    # Should land on the legacy 03:30 UTC slot from shared genlab-core.
    assert "T03:30:00" in iso


def test_per_niche_resolution_uses_canonical_NICHE_DIR_NAMES(per_niche_env):
    """Source-level pin: the function must import the canonical
    NICHE_DIR_NAMES mapping rather than re-define one locally.

    A local re-definition would drift from the source of truth in
    ``genlab_core.pipeline.cli``. The shared map already has 5
    callers (cross_post_teaser, publish_all_platforms, pipeline/
    __init__, this PR); a 6th re-definition would compound the drift
    surface.
    """
    from pathlib import Path

    import server.core.publishing_queue as mod

    src = Path(mod.__file__).read_text()
    # Pin both the import and the per-niche resolution shape
    assert "from genlab_core.pipeline.cli import" in src
    assert "NICHE_DIR_NAMES" in src
    assert "_resolve_genlab_root" in src
    # Pin the candidate-ladder shape so a refactor that drops the
    # gaming-nested-first ordering trips here (gaming uses
    # ``CriticalRush/niches/gaming/config/`` — different from the
    # flat layout used by every other niche).
    assert '"niches" / niche_id / "config" / "publishing.yaml"' in src


# Verify the patched fixture didn't accidentally short-circuit the
# slot-picker — sanity check on the test harness itself.
def test_fixture_smoke_at_least_one_slot_resolves(per_niche_env):
    from server.core.publishing_queue import _next_available_slot

    # Any of the three configs should give SOMETHING — if not, the
    # harness itself broke before any of the bug-pins ran.
    result = _next_available_slot(niche_id="anime")
    assert result is not None, (
        "scheduler returned None for niche=anime under test fixture — "
        "harness setup regressed; the bug-pins above can't be trusted."
    )
