"""Niche-pause wire into auto_approver — PR #575 consumer.

Pins:

  * _niche_is_paused calls genlab_core.scheduling.niche_pause.is_paused
  * Pause check runs AFTER kill switch + policy.enabled guards but
    BEFORE candidate query (no DB roundtrip when paused)
  * Paused niche → returns result with niche_paused=True and zero
    approvals
  * Not paused → proceeds normally
  * ImportError on niche_pause module → False (pre-PR-577 graceful
    degrade)
  * Exception in is_paused → False (fail-OPEN; auto-approver never
    halts on a transient DB hiccup)
  * AutoApprovalPassResult.niche_paused field exists, defaults False
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ── _niche_is_paused unit tests ────────────────────────────────────


def test_niche_is_paused_delegates_to_helper():
    """The wire forwards the niche_id to genlab_core.scheduling.niche_pause."""
    from genlab_core.scheduling.auto_approver import _niche_is_paused

    with patch(
        "genlab_core.scheduling.niche_pause.is_paused",
        return_value=True,
    ) as mock_paused:
        result = _niche_is_paused("gaming")
    assert result is True
    mock_paused.assert_called_once_with("gaming")


def test_niche_is_paused_returns_false_when_not_paused():
    from genlab_core.scheduling.auto_approver import _niche_is_paused

    with patch("genlab_core.scheduling.niche_pause.is_paused", return_value=False):
        assert _niche_is_paused("gaming") is False


def test_niche_is_paused_handles_import_error_gracefully():
    """Pre-PR-577 core → no niche_pause module → False. The wire
    must be safe to deploy against an older core version without
    crashing the auto-approver."""
    from genlab_core.scheduling.auto_approver import _niche_is_paused

    with patch.dict(
        "sys.modules",
        {"genlab_core.scheduling.niche_pause": None},
    ):
        assert _niche_is_paused("gaming") is False


def test_niche_is_paused_handles_exception_fail_open():
    """is_paused exception → False (fail-OPEN). Auto-approver MUST
    NOT halt on a transient DB hiccup — that would defeat autonomy
    based on a non-pause-related failure."""
    from genlab_core.scheduling.auto_approver import _niche_is_paused

    with patch(
        "genlab_core.scheduling.niche_pause.is_paused",
        side_effect=RuntimeError("simulated DB error"),
    ):
        assert _niche_is_paused("gaming") is False


# ── AutoApprovalPassResult.niche_paused field ──────────────────────


def test_result_has_niche_paused_field():
    """The new niche_paused field exists, defaults to False, and is
    distinct from kill_switch_active + policy_disabled (operators
    can see WHICH guard halted the pass)."""
    from genlab_core.scheduling.auto_approver import (
        AutoApprovalPassResult,
        AutoApprovalPolicy,
    )

    policy = AutoApprovalPolicy(enabled=False, min_confidence=0.85)
    result = AutoApprovalPassResult(niche_id="gaming", policy=policy)
    assert hasattr(result, "niche_paused")
    assert result.niche_paused is False
    # Distinct from other guard fields
    assert result.kill_switch_active is False
    assert result.policy_disabled is False


# ── run_pass integration ───────────────────────────────────────────


def test_run_pass_aborts_when_niche_paused(monkeypatch):
    """When _niche_is_paused returns True, run_pass:
    * sets niche_paused=True on the result
    * returns immediately (no candidate query, no _execute_approval)
    * still records 0 auto-approvals + 0 candidates examined"""
    from genlab_core.scheduling import auto_approver
    from genlab_core.scheduling.auto_approver import AutoApprovalPolicy, run_pass

    # Mock load_policy to return enabled=True (so we hit the pause
    # guard, not the policy.enabled guard above it)
    enabled_policy = AutoApprovalPolicy(enabled=True, min_confidence=0.85)
    monkeypatch.setattr(auto_approver, "load_policy", lambda *a, **kw: enabled_policy)
    monkeypatch.setattr(auto_approver, "_kill_switch_active", lambda: (False, ""))
    monkeypatch.setattr(auto_approver, "_niche_is_paused", lambda nid: True)

    # backlog_client / gate_evaluate should never be called when paused
    fake_client = patch("genlab_core.http.backlog_client.BacklogClient").start()
    try:
        result = run_pass("gaming")
    finally:
        patch.stopall()

    assert result.niche_paused is True
    assert result.candidates_examined == 0
    assert result.auto_approved == []
    assert fake_client.call_count == 0


def test_run_pass_proceeds_when_not_paused(monkeypatch):
    """When _niche_is_paused returns False, run_pass continues to
    the candidate query branch (which we stub to return empty so
    we don't exercise the full approval path)."""
    from genlab_core.scheduling import auto_approver
    from genlab_core.scheduling.auto_approver import AutoApprovalPolicy, run_pass

    enabled_policy = AutoApprovalPolicy(enabled=True, min_confidence=0.85)
    monkeypatch.setattr(auto_approver, "load_policy", lambda *a, **kw: enabled_policy)
    monkeypatch.setattr(auto_approver, "_kill_switch_active", lambda: (False, ""))
    monkeypatch.setattr(auto_approver, "_niche_is_paused", lambda nid: False)

    # Stub the backlog client to return empty candidates → loop exits
    # cleanly without exercising _execute_approval
    from unittest.mock import MagicMock

    fake_client = MagicMock()
    fake_client.blueprints.all.return_value = []
    result = run_pass("gaming", backlog_client=fake_client)

    assert result.niche_paused is False
    # No candidates → 0 examined, 0 approved
    assert result.candidates_examined == 0


def test_pause_guard_runs_after_kill_switch_and_policy_guards(monkeypatch):
    """Pin order: kill switch (global) → policy.enabled (per-niche
    YAML) → niche_paused (DB row). Without this order, the pause
    check would hit DB even when global kill switch was active —
    wasted DB query."""
    from genlab_core.scheduling import auto_approver
    from genlab_core.scheduling.auto_approver import AutoApprovalPolicy, run_pass

    # Kill switch is active → pause check should NEVER run
    monkeypatch.setattr(
        auto_approver,
        "load_policy",
        lambda *a, **kw: AutoApprovalPolicy(enabled=True, min_confidence=0.85),
    )
    monkeypatch.setattr(auto_approver, "_kill_switch_active", lambda: (True, "env_var"))

    pause_was_called = []

    def tracker(nid):
        pause_was_called.append(nid)
        return False

    monkeypatch.setattr(auto_approver, "_niche_is_paused", tracker)

    result = run_pass("gaming")

    assert result.kill_switch_active is True
    assert pause_was_called == [], (
        "_niche_is_paused must NOT be called when kill_switch_active (wasted DB query)"
    )


def test_pause_guard_skipped_when_policy_disabled(monkeypatch):
    """When policy.enabled=false (per-niche YAML), the pause check
    should also be skipped — the niche isn't auto-approving anyway,
    no need to consult the pause row."""
    from genlab_core.scheduling import auto_approver
    from genlab_core.scheduling.auto_approver import AutoApprovalPolicy, run_pass

    # Policy disabled → pause check should NEVER run
    disabled_policy = AutoApprovalPolicy(enabled=False, min_confidence=0.85)
    monkeypatch.setattr(auto_approver, "load_policy", lambda *a, **kw: disabled_policy)
    monkeypatch.setattr(auto_approver, "_kill_switch_active", lambda: (False, ""))

    pause_was_called = []

    def tracker(nid):
        pause_was_called.append(nid)
        return False

    monkeypatch.setattr(auto_approver, "_niche_is_paused", tracker)

    result = run_pass("gaming")

    assert result.policy_disabled is True
    assert pause_was_called == [], "_niche_is_paused must NOT be called when policy.enabled=false"


# ── source pins ────────────────────────────────────────────────────


def test_source_pin_helper_present():
    import genlab_core.scheduling.auto_approver as mod

    src = Path(mod.__file__).read_text()
    assert "def _niche_is_paused(niche_id: str) -> bool:" in src
    assert "PR #577 consumer wire" in src


def test_source_pin_guard_3_called_between_policy_and_query():
    """Pin guard ordering in run_pass: Guard 1 kill switch → Guard 2
    policy.enabled → Guard 3 niche_paused → candidate query.

    Verified via source positions: each guard's marker comment +
    the niche_paused check appear in the expected order."""
    import genlab_core.scheduling.auto_approver as mod

    src = Path(mod.__file__).read_text()
    g1 = src.index("Guard 1: global kill switch")
    g2 = src.index("Guard 2: per-niche policy")
    g3 = src.index("Guard 3: per-niche pause")
    candidates_query = src.index("Query candidate blueprints")
    assert g1 < g2 < g3 < candidates_query
