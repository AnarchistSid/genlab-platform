"""PR #570 (2026-06-25) — account_health real implementation + check.

Pins:

  * get_signal computes baseline/recent/ratio from analytics table
  * Insufficient data → None (fail-OPEN)
  * CRITICAL when ratio < 0.1, WARNING when < 0.5, HEALTHY when >= 0.5
  * check_account_health registered into policy_gate at import
  * Phase A observation-only — both warning + critical → 'warn'
  * Tunable thresholds via env vars
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ── registration ───────────────────────────────────────────────────


def test_check_registered_at_import():
    import genlab_core.compliance  # noqa: F401 — triggers registration
    from genlab_core.compliance.policy_gate import registered_check_names

    assert "account_health_check" in registered_check_names()


# ── HealthSignal dataclass ─────────────────────────────────────────


def test_health_signal_frozen():
    """Same safety rationale as Tenant / User / ComplianceDecision DTOs."""
    import dataclasses

    from genlab_core.compliance.account_health import HealthSignal

    s = HealthSignal(state="healthy", message="ok")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.state = "critical"  # type: ignore[misc]


def test_health_signal_evidence_defaults_empty():
    from genlab_core.compliance.account_health import HealthSignal

    s = HealthSignal(state="healthy", message="ok")
    assert s.evidence == {}


# ── get_signal fail-OPEN ──────────────────────────────────────────


def test_get_signal_no_dsn_returns_none(monkeypatch):
    from genlab_core.compliance import account_health

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert account_health.get_signal("instagram", "gaming") is None


def test_get_signal_empty_inputs_return_none():
    from genlab_core.compliance.account_health import get_signal

    assert get_signal("", "gaming") is None
    assert get_signal("instagram", "") is None


def test_get_signal_db_exception_returns_none(monkeypatch):
    """fail-OPEN: DB query raise → None, never propagates."""
    from genlab_core.compliance import account_health

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated DB blowup")

    with patch.object(account_health, "_connect", return_value=fake_conn):
        assert account_health.get_signal("instagram", "gaming") is None


# ── insufficient data → None ──────────────────────────────────────


def _stub_stats(monkeypatch, stats_dict):
    """Helper: stub _compute_reach_stats to return a controlled dict."""
    from genlab_core.compliance import account_health

    monkeypatch.setattr(account_health, "_compute_reach_stats", lambda p, n: stats_dict)


def test_insufficient_baseline_returns_none(monkeypatch):
    """< MIN_BASELINE_SAMPLES (default 5) → None. Fail-OPEN for new
    niches with no history."""
    from genlab_core.compliance.account_health import get_signal

    _stub_stats(
        monkeypatch,
        {
            "baseline_avg": 100.0,
            "baseline_n": 3,  # < 5
            "recent_avg": 50.0,
            "recent_n": 2,
        },
    )
    assert get_signal("instagram", "gaming") is None


def test_insufficient_recent_returns_none(monkeypatch):
    """< MIN_RECENT_SAMPLES (default 2) → None. Avoids false alarms
    on a single bad post."""
    from genlab_core.compliance.account_health import get_signal

    _stub_stats(
        monkeypatch,
        {
            "baseline_avg": 100.0,
            "baseline_n": 14,
            "recent_avg": 50.0,
            "recent_n": 1,  # < 2
        },
    )
    assert get_signal("instagram", "gaming") is None


def test_zero_baseline_returns_none(monkeypatch):
    """baseline=0 makes ratio undefined. None — proceed as healthy
    (YouTube without real metrics yet, etc.)"""
    from genlab_core.compliance.account_health import get_signal

    _stub_stats(
        monkeypatch,
        {
            "baseline_avg": 0,
            "baseline_n": 10,
            "recent_avg": 0,
            "recent_n": 5,
        },
    )
    assert get_signal("instagram", "gaming") is None


# ── cliff detection (the core logic) ──────────────────────────────


def test_critical_when_ratio_below_critical_threshold(monkeypatch):
    """ratio < 0.1 (10x drop) → CRITICAL signal with shadowban msg."""
    from genlab_core.compliance.account_health import get_signal

    _stub_stats(
        monkeypatch,
        {
            "baseline_avg": 1000.0,
            "baseline_n": 30,
            "recent_avg": 50.0,  # ratio = 0.05
            "recent_n": 5,
        },
    )
    sig = get_signal("instagram", "gaming")
    assert sig is not None
    assert sig.state == "critical"
    assert "shadowban" in sig.message.lower()
    assert sig.evidence["ratio"] == 0.05


def test_warning_when_ratio_between_thresholds(monkeypatch):
    """0.1 <= ratio < 0.5 → WARNING. Not yet shadowban but the
    operator should look."""
    from genlab_core.compliance.account_health import get_signal

    _stub_stats(
        monkeypatch,
        {
            "baseline_avg": 1000.0,
            "baseline_n": 30,
            "recent_avg": 300.0,  # ratio = 0.3
            "recent_n": 5,
        },
    )
    sig = get_signal("instagram", "gaming")
    assert sig is not None
    assert sig.state == "warning"
    assert sig.evidence["ratio"] == 0.3


def test_healthy_when_ratio_at_or_above_warning(monkeypatch):
    """ratio >= 0.5 → HEALTHY. Pin the boundary so a future refactor
    doesn't accidentally make 0.5 trigger 'warning'."""
    from genlab_core.compliance.account_health import get_signal

    _stub_stats(
        monkeypatch,
        {
            "baseline_avg": 1000.0,
            "baseline_n": 30,
            "recent_avg": 500.0,  # ratio = 0.5 — boundary
            "recent_n": 5,
        },
    )
    sig = get_signal("instagram", "gaming")
    assert sig is not None
    assert sig.state == "healthy"


def test_healthy_when_reach_grew(monkeypatch):
    """Recent > baseline → ratio > 1 → HEALTHY. Sanity."""
    from genlab_core.compliance.account_health import get_signal

    _stub_stats(
        monkeypatch,
        {
            "baseline_avg": 100.0,
            "baseline_n": 30,
            "recent_avg": 500.0,  # ratio = 5.0
            "recent_n": 5,
        },
    )
    sig = get_signal("instagram", "gaming")
    assert sig.state == "healthy"


def test_evidence_includes_diagnostic_fields(monkeypatch):
    """The evidence dict is what the dashboard renders + what the
    operator inspects when investigating a flag."""
    from genlab_core.compliance.account_health import get_signal

    _stub_stats(
        monkeypatch,
        {
            "baseline_avg": 1000.0,
            "baseline_n": 30,
            "recent_avg": 50.0,
            "recent_n": 5,
        },
    )
    sig = get_signal("instagram", "gaming")
    assert "ratio" in sig.evidence
    assert "baseline_avg_reach" in sig.evidence
    assert "recent_avg_reach" in sig.evidence
    assert "baseline_samples" in sig.evidence
    assert "recent_samples" in sig.evidence
    assert "critical_threshold" in sig.evidence
    assert "warning_threshold" in sig.evidence


# ── check_account_health behavior ─────────────────────────────────


def test_check_returns_allow_when_no_signal(monkeypatch):
    """get_signal=None (fail-OPEN) → check returns 'allow'."""
    from genlab_core.compliance import account_health

    monkeypatch.setattr(account_health, "get_signal", lambda *a: None)
    result = account_health.check_account_health({}, "instagram", "gaming")
    assert result.decision == "allow"


def test_check_returns_allow_when_healthy(monkeypatch):
    from genlab_core.compliance import account_health
    from genlab_core.compliance.account_health import HealthSignal

    monkeypatch.setattr(
        account_health,
        "get_signal",
        lambda *a: HealthSignal(state="healthy", message="ok"),
    )
    result = account_health.check_account_health({}, "instagram", "gaming")
    assert result.decision == "allow"


def test_check_returns_warn_when_warning(monkeypatch):
    """Phase A is observation-only — warning state → 'warn'."""
    from genlab_core.compliance import account_health
    from genlab_core.compliance.account_health import HealthSignal

    monkeypatch.setattr(
        account_health,
        "get_signal",
        lambda *a: HealthSignal(
            state="warning",
            message="Reach dropped 2x",
            evidence={"ratio": 0.3},
        ),
    )
    result = account_health.check_account_health({}, "instagram", "gaming")
    assert result.decision == "warn"
    assert "account_health_warning" in result.reasons


def test_check_returns_warn_when_critical(monkeypatch):
    """Phase A is observation-only — even critical state → 'warn'
    (not 'block' yet). PR after enforcement-toggle can upgrade
    critical → block per-niche."""
    from genlab_core.compliance import account_health
    from genlab_core.compliance.account_health import HealthSignal

    monkeypatch.setattr(
        account_health,
        "get_signal",
        lambda *a: HealthSignal(
            state="critical",
            message="Reach dropped 20x: shadowban",
            evidence={"ratio": 0.05},
        ),
    )
    result = account_health.check_account_health({}, "instagram", "gaming")
    assert result.decision == "warn"
    assert "account_health_critical" in result.reasons


def test_check_metadata_includes_evidence(monkeypatch):
    """Operators need the evidence to investigate. Pin that the
    evidence dict is propagated into the ComplianceDecision metadata."""
    from genlab_core.compliance import account_health
    from genlab_core.compliance.account_health import HealthSignal

    monkeypatch.setattr(
        account_health,
        "get_signal",
        lambda *a: HealthSignal(
            state="critical",
            message="Reach dropped 20x",
            evidence={"ratio": 0.05, "baseline_avg_reach": 1000.0},
        ),
    )
    result = account_health.check_account_health({}, "instagram", "gaming")
    assert result.metadata["ratio"] == 0.05
    assert result.metadata["baseline_avg_reach"] == 1000.0
    assert "Reach dropped" in result.metadata["message"]


# ── policy_gate aggregate integration ─────────────────────────────


def test_policy_gate_aggregate_uses_health_check(monkeypatch):
    """The 4 Phase A checks (copyright + spam + account_health +
    PR #566's empty default) all aggregate via policy_gate."""
    from genlab_core.compliance import account_health
    from genlab_core.compliance.account_health import HealthSignal
    from genlab_core.compliance.policy_gate import check_publish_policy

    monkeypatch.setattr(
        account_health,
        "get_signal",
        lambda *a: HealthSignal(state="critical", message="bad"),
    )

    bp = {"video_id": "abc", "source": "youtube_trending"}
    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy(bp, "instagram", "gaming")
    assert result.decision == "warn"
    assert any("account_health_check:account_health_critical" in r for r in result.reasons)
