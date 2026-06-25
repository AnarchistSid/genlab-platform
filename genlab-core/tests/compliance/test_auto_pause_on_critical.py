"""Auto-pause on critical account health — PR after #578.

Pins:

  * _maybe_auto_pause_on_critical is a no-op when env flag unset
    (opt-in by GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL=1)
  * When flag set + signal critical → pause() called with
    paused_by='system:account_health_check' + 4h paused_until
  * Reason embeds the signal message for audit forensics
  * pause() failure (returns False) → WARNING log + ComplianceDecision
    return value UNCHANGED (auto-pause is best-effort)
  * Exception in pause() → swallowed; never propagates
  * check_account_health 'critical' triggers the helper; 'warning'
    does NOT (only critical = 10x reach drop = shadowban)
  * 'healthy' / None signal → helper not called (no false-positive pause)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

# ── _maybe_auto_pause_on_critical ─────────────────────────────────


def _critical_signal():
    from genlab_core.compliance.account_health import HealthSignal

    return HealthSignal(
        state="critical",
        message="Reach dropped 20x: 48h avg 50 vs 14d baseline 1000. Probable shadowban.",
        evidence={"ratio": 0.05, "baseline_avg_reach": 1000.0, "recent_avg_reach": 50.0},
    )


def test_no_op_when_env_flag_unset(monkeypatch, caplog):
    """Opt-in by env flag — default-off lets the feature ship
    without changing behavior on existing deployments."""
    import logging

    from genlab_core.compliance.account_health import _maybe_auto_pause_on_critical

    monkeypatch.delenv("GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL", raising=False)

    with patch("genlab_core.scheduling.niche_pause.pause") as mock_pause:
        with caplog.at_level(logging.DEBUG):
            _maybe_auto_pause_on_critical("gaming", _critical_signal())
    mock_pause.assert_not_called()
    # DEBUG log surfaces "would have paused" for forensic context
    assert any("auto-pause disabled" in r.getMessage() for r in caplog.records)


def test_fires_pause_when_env_flag_set(monkeypatch):
    """Flag on → pause() called with system: paused_by + 4h window
    + message-embedded reason."""
    from genlab_core.compliance.account_health import _maybe_auto_pause_on_critical

    monkeypatch.setenv("GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL", "1")

    with patch("genlab_core.scheduling.niche_pause.pause", return_value=True) as mock_pause:
        _maybe_auto_pause_on_critical("gaming", _critical_signal())

    assert mock_pause.call_count == 1
    kwargs = mock_pause.call_args.kwargs
    assert kwargs["niche_id"] == "gaming"
    assert "auto_paused_health_critical" in kwargs["reason"]
    # Reason embeds the signal message for forensic clarity
    assert "shadowban" in kwargs["reason"].lower()
    assert kwargs["paused_by"] == "system:account_health_check"
    # paused_until ~4 hours from now (allow 5s slop for test timing)
    expected = datetime.now(tz=UTC) + timedelta(hours=4)
    actual = kwargs["paused_until"]
    assert abs((actual - expected).total_seconds()) < 5


def test_no_pause_when_flag_set_to_anything_other_than_1(monkeypatch):
    """Strict opt-in: only '1' enables. Empty / 'true' / '0' all
    treated as disabled (avoids ambiguity)."""
    from genlab_core.compliance.account_health import _maybe_auto_pause_on_critical

    # Note: code does .strip() so '1 ' / ' 1' both enable (handles
    # operator copy-paste). True non-1 values:
    for value in ["", "0", "true", "yes", "TRUE", "enabled", "on"]:
        monkeypatch.setenv("GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL", value)
        with patch("genlab_core.scheduling.niche_pause.pause") as mock_pause:
            _maybe_auto_pause_on_critical("gaming", _critical_signal())
        assert mock_pause.call_count == 0, f"flag value {value!r} should not enable"


def test_pause_failure_logs_warning_no_propagation(monkeypatch, caplog):
    """When pause() returns False (write failure), the helper logs
    WARNING but does NOT raise. The check_account_health caller
    still returns its normal 'warn' ComplianceDecision."""
    import logging

    from genlab_core.compliance.account_health import _maybe_auto_pause_on_critical

    monkeypatch.setenv("GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL", "1")

    with patch("genlab_core.scheduling.niche_pause.pause", return_value=False):
        with caplog.at_level(logging.WARNING):
            # Must not raise
            _maybe_auto_pause_on_critical("gaming", _critical_signal())
    assert any("auto-pause attempt" in r.getMessage() for r in caplog.records)


def test_pause_exception_swallowed(monkeypatch, caplog):
    """pause() raising → swallowed + WARNING log. Best-effort —
    auto-pause failure must NEVER propagate to check_account_health."""
    import logging

    from genlab_core.compliance.account_health import _maybe_auto_pause_on_critical

    monkeypatch.setenv("GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL", "1")

    with patch(
        "genlab_core.scheduling.niche_pause.pause",
        side_effect=RuntimeError("simulated DB error"),
    ):
        with caplog.at_level(logging.WARNING):
            # Must not raise
            _maybe_auto_pause_on_critical("gaming", _critical_signal())
    assert any("auto-pause for" in r.getMessage() for r in caplog.records)


def test_successful_pause_logs_warning(monkeypatch, caplog):
    """Successful auto-pause logs WARNING (not INFO) so it's
    visible in operator alerts — the system just took an
    autonomous publishing-halt action."""
    import logging

    from genlab_core.compliance.account_health import _maybe_auto_pause_on_critical

    monkeypatch.setenv("GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL", "1")

    with patch("genlab_core.scheduling.niche_pause.pause", return_value=True):
        with caplog.at_level(logging.WARNING):
            _maybe_auto_pause_on_critical("gaming", _critical_signal())
    assert any("AUTO-PAUSED" in r.getMessage() for r in caplog.records)


# ── check_account_health integration ──────────────────────────────


def test_check_critical_signal_triggers_auto_pause(monkeypatch):
    """When the signal is CRITICAL, check_account_health calls
    the auto-pause helper. Pin via mock spy on the helper."""
    from genlab_core.compliance import account_health
    from genlab_core.compliance.account_health import HealthSignal, check_account_health

    monkeypatch.setattr(
        account_health,
        "get_signal",
        lambda *a: HealthSignal(state="critical", message="shadowban"),
    )

    with patch.object(account_health, "_maybe_auto_pause_on_critical") as mock_helper:
        result = check_account_health({}, "instagram", "gaming")
    mock_helper.assert_called_once()
    # ComplianceDecision return value still 'warn' (Phase A observation-only)
    assert result.decision == "warn"
    assert "account_health_critical" in result.reasons


def test_check_warning_signal_does_NOT_trigger_auto_pause(monkeypatch):
    """A 'warning' signal (2x reach drop) is NOT severe enough for
    auto-pause. Only 'critical' (10x drop = shadowban) triggers.
    Pin so a future refactor doesn't accidentally widen the trigger."""
    from genlab_core.compliance import account_health
    from genlab_core.compliance.account_health import HealthSignal, check_account_health

    monkeypatch.setattr(
        account_health,
        "get_signal",
        lambda *a: HealthSignal(state="warning", message="reach dropped 2x"),
    )

    with patch.object(account_health, "_maybe_auto_pause_on_critical") as mock_helper:
        check_account_health({}, "instagram", "gaming")
    mock_helper.assert_not_called()


def test_check_healthy_signal_does_NOT_trigger_auto_pause(monkeypatch):
    from genlab_core.compliance import account_health
    from genlab_core.compliance.account_health import HealthSignal, check_account_health

    monkeypatch.setattr(
        account_health,
        "get_signal",
        lambda *a: HealthSignal(state="healthy", message="within baseline"),
    )

    with patch.object(account_health, "_maybe_auto_pause_on_critical") as mock_helper:
        check_account_health({}, "instagram", "gaming")
    mock_helper.assert_not_called()


def test_check_none_signal_does_NOT_trigger_auto_pause(monkeypatch):
    """get_signal returns None on insufficient data → no pause
    (we shouldn't pause based on absence of data)."""
    from genlab_core.compliance import account_health
    from genlab_core.compliance.account_health import check_account_health

    monkeypatch.setattr(account_health, "get_signal", lambda *a: None)

    with patch.object(account_health, "_maybe_auto_pause_on_critical") as mock_helper:
        check_account_health({}, "instagram", "gaming")
    mock_helper.assert_not_called()
