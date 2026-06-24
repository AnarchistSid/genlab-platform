"""PR #538 (2026-06-24): SR-E enforcement — per-niche budget cap in
``YouTubeQuotaTracker.can_afford``.

Final stage of the SR-E sprint. Combined with PR #536 (foundation,
tracker accepts niche_id) and PR #537 (wire, callers pass niche_id),
this PR turns on per-niche enforcement: when a niche's local usage
+ cost would exceed the configured cap, ``can_afford`` returns False.

Rollout shape (same as SR-D's observability → opt-in strict pattern):
  * Default state: enforcement OFF (no env var, no kwarg) → behaves
    identically to PR #537 (per-niche counters populate but no cap).
  * Operators opt in via ``GENLAB_YOUTUBE_PER_NICHE_QUOTA=2000`` env
    var to enable enforcement once they've seen one full day of
    per-niche data and confirmed the cap is safe.
  * Explicit kwarg ``per_niche_quota=N`` on the tracker overrides
    the env (mostly useful for tests + per-instance customization).

If these pins regress, the SR-E sprint stalls at "tracking only" —
data accumulates but no enforcement → effectively SR-E remains
open. The pins cover BOTH directions:
  * Enforcement-off must NOT block any caller (backward compat)
  * Enforcement-on must block when the per-niche cap is exceeded
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from genlab_core.monitoring.youtube_quota import YouTubeQuotaTracker

# ── enforcement OFF (default) ──────────────────────────────────────


def test_enforcement_off_by_default(tmp_path, monkeypatch):
    """No env var, no kwarg → per_niche_quota=None → no enforcement."""
    monkeypatch.delenv("GENLAB_YOUTUBE_PER_NICHE_QUOTA", raising=False)
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    assert tracker._per_niche_quota is None


def test_enforcement_off_can_afford_ignores_niche_usage(tmp_path, monkeypatch):
    """With enforcement off, a niche burning lots of units still
    passes can_afford as long as the global hard-stop allows. Pins
    backward compat for PR #537-shipped wire-up callers."""
    monkeypatch.delenv("GENLAB_YOUTUBE_PER_NICHE_QUOTA", raising=False)
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")

    # Burn 5000 units on gaming (well over any reasonable per-niche cap)
    tracker.record("search", 50, niche_id="gaming")  # 5000 units

    # Still passes — enforcement is off → no per-niche check
    assert tracker.can_afford("search", 1, niche_id="gaming") is True


# ── enforcement ON via constructor kwarg ───────────────────────────


def test_explicit_kwarg_enables_enforcement(tmp_path, monkeypatch):
    monkeypatch.delenv("GENLAB_YOUTUBE_PER_NICHE_QUOTA", raising=False)
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json", per_niche_quota=2000)
    assert tracker._per_niche_quota == 2000


def test_enforcement_blocks_when_niche_cap_exceeded(tmp_path, monkeypatch):
    """Niche at 1900 + 200-unit op = 2100 > 2000 cap → blocked.
    The global hard-stop is 9800, so global isn't the constraint —
    purely the per-niche check that fires."""
    monkeypatch.delenv("GENLAB_YOUTUBE_PER_NICHE_QUOTA", raising=False)
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json", per_niche_quota=2000)

    tracker.record("search", 19, niche_id="gaming")  # 1900 units

    assert tracker.can_afford("search", 2, niche_id="gaming") is False, (
        "Per-niche cap exceeded → can_afford must return False"
    )


def test_enforcement_allows_under_niche_cap(tmp_path, monkeypatch):
    """At 1900 + 100-unit op = 2000 == cap → passes (≤ check)."""
    monkeypatch.delenv("GENLAB_YOUTUBE_PER_NICHE_QUOTA", raising=False)
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json", per_niche_quota=2000)

    tracker.record("search", 19, niche_id="gaming")  # 1900 units

    assert tracker.can_afford("search", 1, niche_id="gaming") is True


def test_enforcement_one_niche_doesnt_affect_another(tmp_path):
    """The whole point of SR-E. Gaming burns its 2000 cap; sports
    can still afford its full 2000 (isolated counters)."""
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json", per_niche_quota=2000)

    # Burn gaming's cap entirely
    tracker.record("search", 20, niche_id="gaming")  # 2000 units
    assert tracker.can_afford("search", 1, niche_id="gaming") is False

    # Sports unaffected — still has its full cap
    assert tracker.can_afford("search", 20, niche_id="sports") is True


def test_no_niche_id_skips_per_niche_check_even_with_enforcement(tmp_path):
    """A caller that didn't pass niche_id (PR #537 not migrated yet)
    only hits the global hard-stop check. Per-niche enforcement
    requires BOTH the env/kwarg AND the kwarg at the call site."""
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json", per_niche_quota=2000)

    # Burn 5000 globally (over the per-niche cap but under global hard-stop)
    tracker.record("search", 50)

    # No niche_id → no per-niche check → passes
    assert tracker.can_afford("search", 1) is True


def test_global_hard_stop_still_wins(tmp_path):
    """When global hard-stop is the binding constraint, the per-niche
    cap doesn't bypass it. Defense-in-depth — a misconfigured per-
    niche cap can't overrun the daily 10k limit."""
    tracker = YouTubeQuotaTracker(
        state_path=tmp_path / "q.json",
        daily_quota=100,  # tiny global for the test
        per_niche_quota=1000,  # cap higher than global (intentionally wrong)
    )

    # 100 units globally → hard_stop=98 → 100 > 98 → blocked
    # even though per-niche cap (1000) would allow it.
    tracker.record("search", 1)  # 100 units
    assert tracker.can_afford("search", 1, niche_id="gaming") is False


# ── env-var path ───────────────────────────────────────────────────


def test_env_var_enables_enforcement(tmp_path, monkeypatch):
    monkeypatch.setenv("GENLAB_YOUTUBE_PER_NICHE_QUOTA", "2000")
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    assert tracker._per_niche_quota == 2000


def test_explicit_kwarg_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GENLAB_YOUTUBE_PER_NICHE_QUOTA", "2000")
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json", per_niche_quota=500)
    assert tracker._per_niche_quota == 500


@pytest.mark.parametrize("bad_value", ["abc", "0", "-5", ""])
def test_invalid_env_var_falls_back_to_none(tmp_path, monkeypatch, bad_value):
    """Defense-in-depth: a typo in the env var shouldn't crash the
    quota gate. Falls back to None (enforcement disabled) — the
    global hard-stop still protects."""
    monkeypatch.setenv("GENLAB_YOUTUBE_PER_NICHE_QUOTA", bad_value)
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    assert tracker._per_niche_quota is None


def test_invalid_env_logs_warning_for_typo(tmp_path, monkeypatch, caplog):
    """A non-int env value triggers a WARNING so the operator notices
    their typo. Empty / zero / negative values don't log (they're
    valid "disable" signals)."""
    monkeypatch.setenv("GENLAB_YOUTUBE_PER_NICHE_QUOTA", "two-thousand")
    with caplog.at_level(logging.WARNING):
        YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    assert any("not a valid int" in r.message for r in caplog.records)


# ── status() exposes cap details when enforcement on ──────────────


def test_status_includes_cap_when_enforcement_on(tmp_path):
    """status(niche_id=X) returns cap + remaining + pct_of_cap when
    per-niche enforcement is enabled. Dashboards use this to render
    per-tenant quota bars."""
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json", per_niche_quota=2000)
    tracker.record("search", 5, niche_id="gaming")  # 500 units

    out = tracker.status(niche_id="gaming")
    assert out["niche"]["used"] == 500
    assert out["niche"]["cap"] == 2000
    assert out["niche"]["remaining"] == 1500
    assert out["niche"]["pct_of_cap"] == 25.0


def test_status_omits_cap_when_enforcement_off(tmp_path, monkeypatch):
    """With enforcement off, the cap keys are absent — consumers
    can detect "foundation only" vs "enforcement on" by their
    presence. Important for dashboards that should render "cap: —"
    (not "cap: 0") when no cap exists."""
    monkeypatch.delenv("GENLAB_YOUTUBE_PER_NICHE_QUOTA", raising=False)
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json")
    tracker.record("search", 1, niche_id="gaming")

    out = tracker.status(niche_id="gaming")
    for key in ("cap", "remaining", "pct_of_cap"):
        assert key not in out["niche"], (
            f"status() must omit per-niche '{key}' when enforcement is off "
            f"— dashboards detect foundation-only state via absence."
        )


# ── WARNING logging when cap is hit ────────────────────────────────


def test_cap_exceeded_logs_warning_with_grep_friendly_shape(tmp_path, caplog):
    """When the per-niche cap blocks an operation, log a WARNING that
    includes the niche_id, used/cap counts, the operation, and the
    global state — operators tailing logs can immediately see WHO
    hit the cap, on WHAT, and how much global headroom remains."""
    tracker = YouTubeQuotaTracker(state_path=tmp_path / "q.json", per_niche_quota=2000)
    tracker.record("search", 19, niche_id="gaming")  # 1900 units

    with caplog.at_level(logging.WARNING):
        tracker.can_afford("search", 2, niche_id="gaming")  # 1900+200=2100 > 2000

    matches = [r.message for r in caplog.records if "per-niche cap reached" in r.message]
    assert len(matches) == 1
    msg = matches[0]
    assert "gaming" in msg
    assert "1900" in msg
    assert "2000" in msg
    assert "search" in msg


# ── source pins ────────────────────────────────────────────────────


def test_source_pins_enforcement_branch_present():
    """A future refactor that drops the per-niche cap check in
    can_afford would silently re-disable enforcement. Pin the
    structural shape."""
    import genlab_core.monitoring.youtube_quota as mod

    src = Path(mod.__file__).read_text()
    # The enforcement branch in can_afford
    assert "if niche_id and self._per_niche_quota is not None:" in src
    # The check itself
    assert "(niche_used + cost) > self._per_niche_quota" in src
    # The WARNING message anchor — split across two literal strings in
    # source (`"per-niche " "cap reached)"`) so the joined form isn't a
    # substring. Check both halves separately.
    assert "per-niche " in src
    assert "cap reached" in src


def test_source_pins_pr_538_anchor():
    """Grep PR #538 lands at the enforcement comments."""
    import genlab_core.monitoring.youtube_quota as mod

    src = Path(mod.__file__).read_text()
    assert "PR #538" in src
    assert "SR-E enforcement" in src
    # Env var name pinned — a typo would silently make operators
    # unable to enable enforcement
    assert "GENLAB_YOUTUBE_PER_NICHE_QUOTA" in src


def test_source_pins_constructor_accepts_per_niche_quota_kwarg():
    """The constructor signature must declare the kwarg so tests +
    operators can construct trackers with custom caps."""
    import inspect

    sig = inspect.signature(YouTubeQuotaTracker.__init__)
    assert "per_niche_quota" in sig.parameters
