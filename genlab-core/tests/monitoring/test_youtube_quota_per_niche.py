"""PR #536 (2026-06-24): per-niche dimension in YouTubeQuotaTracker
(SR-E foundation).

SYSTEM-RESEARCH.md §9 SR-E: shared global YouTube key = cross-tenant
DoS. This PR is the foundational step — add per-niche tracking to the
quota tracker so a future PR can enforce per-niche budgets without a
disruptive caller-rewrite. Same staged shape as SR-C/SR-A:

  * PR #536 (this) — tracker accepts ``niche_id=`` on record/can_afford/
    status. No enforcement yet. Backward compat preserved.
  * PR #537 (follow-up) — wire callers to pass niche_id.
  * PR #538 (follow-up) — add per-niche budget config + enforce.

If these pins regress, the SR-E sprint's foundation breaks and the
follow-up wire-up PRs can't safely land:
  * record() losing niche_id forward → per-niche counters stay at 0
    forever even after caller wire-up
  * load/save losing the per_niche key → state doesn't survive a
    process restart; tracker resets to global-only after each run
  * _maybe_reset() forgetting per_niche → day-1 spend leaks into
    day-2's budget once enforcement turns on
  * status() returning the wrong shape when niche_id is given →
    dashboard reads would break
"""

from __future__ import annotations

import json
from pathlib import Path

from genlab_core.monitoring.youtube_quota import YouTubeQuotaTracker

# ── backward compat (niche_id omitted) ─────────────────────────────


def test_record_without_niche_id_preserves_global_only_behavior(tmp_path):
    """The pre-PR call shape: ``record('search', 1)`` must still work
    and update ONLY the global counter — the per_niche dict stays
    empty so the state file shape is unchanged."""
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    tracker.record("search", 1)

    status = tracker.status()
    assert status["used"] == 100
    # No niche-aware kwarg passed → no per-niche entry created.
    assert tracker._per_niche == {}


def test_status_without_niche_id_returns_global_shape(tmp_path):
    """``status()`` with no kwarg returns the pre-PR-536 shape exactly
    — the top-level keys + their values. No surprise ``niche`` key
    leaks into the dashboard reads."""
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    tracker.record("search", 5, niche_id="gaming")

    out = tracker.status()  # no niche_id
    assert "niche" not in out, (
        "status() without niche_id must NOT include a 'niche' key — "
        "would break existing dashboard parsers."
    )
    # Existing keys still present
    for key in ("used", "remaining", "pct_used", "hard_stop", "upload_count", "reset_date"):
        assert key in out


def test_can_afford_without_niche_id_unchanged(tmp_path):
    """can_afford() with no kwarg is bit-for-bit the pre-PR behavior.
    Per-niche enforcement is explicitly DEFERRED to PR #538 — pinning
    this so a premature enforcement landing doesn't silently regress."""
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    # Fill global to just under hard_stop
    tracker.record("search", 90)  # 9000 units
    # 100 more would be 9100, hard_stop is 9800 → still affordable
    assert tracker.can_afford("search", 1) is True


# ── per-niche record + status ──────────────────────────────────────


def test_record_with_niche_id_increments_both_counters(tmp_path):
    """Per-niche record updates BOTH the global counter (backward
    compat — global enforcement still applies) AND the per-niche
    sub-counter (new SR-E dimension)."""
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    tracker.record("search", 1, niche_id="gaming")

    assert tracker._used == 100  # global still tracks
    assert tracker._per_niche["gaming"]["used"] == 100  # per-niche too
    assert tracker._per_niche["gaming"]["upload_count"] == 0


def test_record_upload_with_niche_id_tracks_upload_count(tmp_path):
    """upload counter is incremented per-niche too — useful for
    'how many uploads has THIS tenant burnt today?' queries."""
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    tracker.record("upload", 1, niche_id="sports")

    assert tracker._per_niche["sports"]["used"] == 1600
    assert tracker._per_niche["sports"]["upload_count"] == 1


def test_record_multiple_niches_keeps_them_separate(tmp_path):
    """Each niche has its own counter — burning gaming's quota
    doesn't show up under sports."""
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    tracker.record("search", 5, niche_id="gaming")  # 500 units
    tracker.record("search", 2, niche_id="sports")  # 200 units

    assert tracker._per_niche["gaming"]["used"] == 500
    assert tracker._per_niche["sports"]["used"] == 200
    # Global is the sum (no double-counting)
    assert tracker._used == 700


def test_status_with_niche_id_includes_per_niche_block(tmp_path):
    """status(niche_id=X) returns the global block PLUS a 'niche'
    sub-block with X's local usage. Top-level keys unchanged for
    backward compat."""
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    tracker.record("search", 3, niche_id="movies")

    out = tracker.status(niche_id="movies")
    assert out["used"] == 300  # global
    assert isinstance(out["niche"], dict)
    assert out["niche"]["niche_id"] == "movies"
    assert out["niche"]["used"] == 300


def test_status_with_unknown_niche_returns_zero_block(tmp_path):
    """status() for a niche that hasn't spent anything returns a
    zeroed 'niche' sub-block (NOT a missing key). Dashboard code can
    assume the shape is always present when niche_id is given."""
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    tracker.record("search", 1, niche_id="gaming")

    out = tracker.status(niche_id="anime")  # anime hasn't spent
    assert out["niche"] == {"niche_id": "anime", "used": 0, "upload_count": 0}


# ── persistence (load + save) ──────────────────────────────────────


def test_per_niche_state_persists_across_tracker_instances(tmp_path):
    """The per-niche state must survive process restart — that's the
    point of using a file-backed store. A fresh tracker reading the
    same JSON sees the same per-niche counters."""
    state = tmp_path / "q.json"

    t1 = YouTubeQuotaTracker(state_path=state)
    t1.record("search", 5, niche_id="gaming")
    t1.record("upload", 1, niche_id="gaming")

    # Fresh instance — must see the same state
    t2 = YouTubeQuotaTracker(state_path=state)
    assert t2._per_niche == {
        "gaming": {"used": 500 + 1600, "upload_count": 1},
    }


def test_state_file_omits_per_niche_when_empty(tmp_path):
    """Operators tailing the JSON shouldn't see an empty ``per_niche: {}``
    key for the pre-migration case — keeps the file minimal + visually
    indistinguishable from pre-PR-536 state files."""
    state = tmp_path / "q.json"
    tracker = YouTubeQuotaTracker(state_path=state)
    tracker.record("search", 1)  # no niche_id

    payload = json.loads(state.read_text())
    assert "per_niche" not in payload, (
        "When no niche-aware writes have happened, per_niche key must "
        "be absent from the JSON — preserves the pre-PR shape exactly."
    )


def test_state_file_includes_per_niche_when_populated(tmp_path):
    """As soon as one niche-aware record() lands, the per_niche key
    appears in the JSON. Operators can grep for it to see migration
    progress."""
    state = tmp_path / "q.json"
    tracker = YouTubeQuotaTracker(state_path=state)
    tracker.record("search", 1, niche_id="gaming")

    payload = json.loads(state.read_text())
    assert payload.get("per_niche") == {
        "gaming": {"used": 100, "upload_count": 0},
    }


def test_load_tolerates_pre_pr_state_file(tmp_path):
    """A state file written by a pre-PR-536 binary has no per_niche
    key. Loading it must NOT crash + must initialize per_niche to {}
    so subsequent record() calls populate it from scratch."""
    state = tmp_path / "q.json"
    # Simulate a pre-PR state file
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(
            {
                "used": 500,
                "upload_count": 0,
                "reset_date": "2099-12-31",  # far future to avoid reset
            }
        )
    )

    tracker = YouTubeQuotaTracker(state_path=state)
    assert tracker._used == 500
    assert tracker._per_niche == {}  # empty, not raising


def test_load_tolerates_corrupt_per_niche_entries(tmp_path):
    """A future field-rename or partial-write could put garbage into
    per_niche. The loader must coerce + drop invalid entries instead
    of crashing the tracker (which would silently disable the whole
    quota system)."""
    state = tmp_path / "q.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(
            {
                "used": 500,
                "upload_count": 0,
                "reset_date": "2099-12-31",
                "per_niche": {
                    "gaming": {"used": 100, "upload_count": 0},  # OK
                    "sports": "garbage-not-a-dict",  # bad — dropped
                    "movies": {"used": "string-not-int", "upload_count": None},
                },
            }
        )
    )

    tracker = YouTubeQuotaTracker(state_path=state)
    # Good entry preserved
    assert tracker._per_niche["gaming"] == {"used": 100, "upload_count": 0}
    # String-instead-of-dict dropped entirely
    assert "sports" not in tracker._per_niche
    # Coerced: 'string-not-int' → 0, None → 0
    assert tracker._per_niche["movies"] == {"used": 0, "upload_count": 0}


# ── reset behavior ─────────────────────────────────────────────────


def test_maybe_reset_clears_per_niche_alongside_global(tmp_path):
    """When the Pacific date rolls over, per_niche must reset too.
    Without this, yesterday's per-tenant spend would count against
    today's budget once PR #538's enforcement turns on."""
    state = tmp_path / "q.json"
    tracker = YouTubeQuotaTracker(state_path=state)
    tracker.record("search", 5, niche_id="gaming")

    # Simulate the date rollover by stuffing yesterday's date in
    tracker._reset_date = "1999-01-01"
    tracker._maybe_reset(persist=False)

    assert tracker._used == 0
    assert tracker._per_niche == {}, (
        "Per-niche dict must clear on Pacific-date rollover — "
        "otherwise PR #538's enforcement would count yesterday's "
        "spend against today's budget."
    )


# ── can_afford signature (foundation only — not yet enforced) ─────


def test_can_afford_accepts_niche_id_kwarg_without_breaking(tmp_path):
    """can_afford(..., niche_id='X') must NOT raise TypeError —
    callers that wire it in PR #537 land before PR #538's enforcement
    must not crash. The answer remains identical to the no-kwarg call
    (no per-niche enforcement until PR #538)."""
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    tracker.record("search", 5, niche_id="gaming")

    # Both calls should return the same answer (no enforcement yet)
    without = tracker.can_afford("search", 1)
    with_niche = tracker.can_afford("search", 1, niche_id="gaming")
    assert without == with_niche


# ── source pins (foundation-mode markers) ──────────────────────────


def test_source_pins_record_signature_includes_niche_id():
    """Source pin so a refactor removing the kwarg trips."""
    import inspect

    sig = inspect.signature(YouTubeQuotaTracker.record)
    assert "niche_id" in sig.parameters


def test_source_pins_can_afford_signature_includes_niche_id():
    import inspect

    sig = inspect.signature(YouTubeQuotaTracker.can_afford)
    assert "niche_id" in sig.parameters


def test_source_pins_status_signature_includes_niche_id():
    import inspect

    sig = inspect.signature(YouTubeQuotaTracker.status)
    assert "niche_id" in sig.parameters


def test_source_pins_pr_anchor_present():
    """Future archaeologist anchor — grep PR #536 lands at the
    foundation comments."""
    import genlab_core.monitoring.youtube_quota as mod

    src = Path(mod.__file__).read_text()
    assert "PR #536" in src
    assert "SR-E foundation" in src
