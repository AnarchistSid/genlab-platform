"""PR #566 (2026-06-25) — policy_gate aggregation + observation-only.

Pins:

  * Empty registered checks → 'allow' (PR #566 default — no checks
    are registered yet; the gate is a callable chokepoint).
  * Aggregation rule: most-restrictive wins (block > warn > allow).
  * Reasons tagged with source-check name for forensic clarity.
  * register_check accepts CheckFn; idempotent override.
  * Bad return from a check → 'warn' (defensive — buggy check
    shouldn't crash the publish path).
  * Check that raises → 'warn' with source-check name in reasons.
  * Aggregate decision logged to compliance_events (observability).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_registered_checks():
    """Each test gets a fresh registry — module-level state mustn't
    leak between tests."""
    from genlab_core.compliance import policy_gate

    saved = dict(policy_gate._REGISTERED_CHECKS)
    policy_gate._REGISTERED_CHECKS.clear()
    yield
    policy_gate._REGISTERED_CHECKS.clear()
    policy_gate._REGISTERED_CHECKS.update(saved)


# ── PR #566 default state: no checks registered → allow ───────────


def test_empty_registry_returns_allow():
    """The PR #566 ship-shape: no checks registered → ComplianceDecision
    ('allow', [], {}). Critical so upstream callers can wire through
    the gate TODAY without false-positive blocks before PR #567+
    plug in real checks."""
    from genlab_core.compliance.policy_gate import check_publish_policy

    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy({}, "instagram", "gaming")
    assert result.decision == "allow"
    assert result.reasons == []
    assert result.metadata == {}


# ── register_check + registered_check_names ───────────────────────


def test_register_and_inspect_check():
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.compliance.policy_gate import (
        register_check,
        registered_check_names,
    )

    def dummy(_bp, _platform, _niche):
        return ComplianceDecision(decision="allow")

    register_check("my_check", dummy)
    assert "my_check" in registered_check_names()


def test_register_check_empty_name_raises():
    from genlab_core.compliance.policy_gate import register_check

    with pytest.raises(ValueError, match="name"):
        register_check("", lambda *a: None)


def test_register_check_is_idempotent_override():
    """Re-registering the same name overrides — lets tests substitute
    mocks without un-registering. Critical for the upcoming
    PR #567-#570 checks that may want test-local replacements."""
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.compliance.policy_gate import _REGISTERED_CHECKS, register_check

    def first(_bp, _platform, _niche):
        return ComplianceDecision(decision="allow")

    def second(_bp, _platform, _niche):
        return ComplianceDecision(decision="warn")

    register_check("same_name", first)
    register_check("same_name", second)
    assert _REGISTERED_CHECKS["same_name"] is second


# ── aggregation rule: most-restrictive wins ───────────────────────


def test_aggregate_block_beats_allow_and_warn():
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.compliance.policy_gate import check_publish_policy, register_check

    register_check("a", lambda *a: ComplianceDecision(decision="allow"))
    register_check("b", lambda *a: ComplianceDecision(decision="warn", reasons=["w"]))
    register_check("c", lambda *a: ComplianceDecision(decision="block", reasons=["b"]))

    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy({}, "instagram", "gaming")
    assert result.decision == "block"


def test_aggregate_warn_beats_allow():
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.compliance.policy_gate import check_publish_policy, register_check

    register_check("a", lambda *a: ComplianceDecision(decision="allow"))
    register_check("b", lambda *a: ComplianceDecision(decision="warn", reasons=["w"]))

    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy({}, "instagram", "gaming")
    assert result.decision == "warn"


def test_aggregate_all_allow_returns_allow():
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.compliance.policy_gate import check_publish_policy, register_check

    register_check("a", lambda *a: ComplianceDecision(decision="allow"))
    register_check("b", lambda *a: ComplianceDecision(decision="allow"))

    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy({}, "instagram", "gaming")
    assert result.decision == "allow"


def test_aggregate_tags_reasons_with_source_check_name():
    """Forensic clarity: each reason in the aggregate is prefixed
    with the check name that produced it. Lets operators trace
    'why was this blocked' from the reasons list."""
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.compliance.policy_gate import check_publish_policy, register_check

    register_check(
        "spam_check",
        lambda *a: ComplianceDecision(decision="warn", reasons=["repeated_hashtags"]),
    )
    register_check(
        "copyright_check",
        lambda *a: ComplianceDecision(decision="warn", reasons=["unverified_clip"]),
    )

    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy({}, "instagram", "gaming")
    assert "spam_check:repeated_hashtags" in result.reasons
    assert "copyright_check:unverified_clip" in result.reasons


def test_aggregate_metadata_keyed_by_check_name():
    """Per-check metadata buckets — prevents collisions when two
    checks both report a 'count' field."""
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.compliance.policy_gate import check_publish_policy, register_check

    register_check(
        "a",
        lambda *_: ComplianceDecision(decision="warn", metadata={"count": 5}),
    )
    register_check(
        "b",
        lambda *_: ComplianceDecision(decision="warn", metadata={"count": 99}),
    )

    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy({}, "instagram", "gaming")
    assert result.metadata["a"]["count"] == 5
    assert result.metadata["b"]["count"] == 99


# ── defensive: bad check return / raise → warn ────────────────────


def test_check_that_raises_converts_to_warn():
    """A buggy check WITHOUT default_mode='block' shouldn't crash the
    publish path. The exception is caught + converted to 'warn'
    (the default fallback_mode) with the source check name in
    reasons. WARNING is logged for the operator to find + fix.
    """
    from genlab_core.compliance.policy_gate import check_publish_policy, register_check

    def broken(_bp, _platform, _niche):
        raise RuntimeError("simulated bug")

    register_check("broken_check", broken)  # default_mode="warn"

    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy({}, "instagram", "gaming")
    assert result.decision == "warn"
    assert "check_raised:broken_check" in result.reasons


def test_check_registered_default_mode_block_fails_closed():
    """2026-07-14 (audit F88) pin: a check registered with
    ``default_mode='block'`` that RAISES falls back to BLOCK, not the
    historic downgrade-to-warn. Critical checks (attribution,
    account_health) should fail-closed on their own bugs, not silently
    downgrade enforcement.
    """
    from genlab_core.compliance.policy_gate import check_publish_policy, register_check

    def broken_critical(_bp, _platform, _niche):
        raise RuntimeError("simulated critical-check bug")

    register_check("broken_critical_check", broken_critical, default_mode="block")

    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy({}, "instagram", "gaming")
    assert result.decision == "block", (
        "A default_mode='block' check that raises must fall back to "
        "BLOCK, not warn — critical checks fail-closed on bug."
    )
    assert "check_raised:broken_critical_check" in result.reasons


def test_register_check_invalid_default_mode_raises():
    """2026-07-14 (audit F88): register_check rejects invalid
    default_mode values at registration time (fail-fast, not at
    check_publish_policy call time).
    """
    import pytest as _pytest
    from genlab_core.compliance.policy_gate import register_check

    with _pytest.raises(ValueError, match="default_mode must be one of"):
        register_check("bad", lambda *_: None, default_mode="skip")


def test_check_returning_non_decision_converts_to_warn():
    """Defensive — a check returning the wrong type → 'warn' +
    check name in reasons. Prevents downstream code from crashing
    on .decision attribute access."""
    from genlab_core.compliance.policy_gate import check_publish_policy, register_check

    register_check("bad_return", lambda *_: "allow")  # str, not ComplianceDecision

    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy({}, "instagram", "gaming")
    assert result.decision == "warn"
    assert "check_bad_return:bad_return" in result.reasons


# ── empty niche_id defensive ─────────────────────────────────────


def test_empty_niche_id_returns_warn_without_running_checks():
    """Caller-bug defensive: empty niche_id → warn with 'empty_niche_id'
    reason. Checks NOT invoked (they can't make a meaningful per-niche
    decision without scope)."""
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.compliance.policy_gate import check_publish_policy, register_check

    called = []

    def tracker(*_args):
        called.append(True)
        return ComplianceDecision(decision="allow")

    register_check("a", tracker)
    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy({}, "instagram", "")
    assert result.decision == "warn"
    assert "empty_niche_id" in result.reasons
    assert called == []  # checks not invoked


# ── logging integration ───────────────────────────────────────────


def test_aggregate_decision_logged_to_compliance_events():
    """Every policy_gate aggregate decision logs one row with
    event_type='pre_publish_check'. Observability for ops."""
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.compliance.policy_gate import check_publish_policy, register_check

    register_check("a", lambda *_: ComplianceDecision(decision="warn", reasons=["x"]))

    bp = {"id": "00000000-0000-0000-0000-000000000001"}
    with patch("genlab_core.compliance.policy_gate.log_compliance_event") as mock_log:
        check_publish_policy(bp, "instagram", "gaming")
    assert mock_log.call_count == 1
    kwargs = mock_log.call_args.kwargs
    assert kwargs["niche_id"] == "gaming"
    assert kwargs["event_type"] == "pre_publish_check"
    assert kwargs["decision"] == "warn"
    assert kwargs["platform"] == "instagram"
    assert "a:x" in kwargs["reasons"]
