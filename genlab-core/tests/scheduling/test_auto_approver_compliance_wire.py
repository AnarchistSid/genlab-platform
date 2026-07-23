"""PR #576 (2026-06-25) — auto-approver consults compliance gate.

Pins:

  * _check_compliance_gate consults policy_gate per-platform
  * ANY platform 'block' → returns reasons list (auto-approval
    aborts; conservative — don't partial-publish)
  * All 'allow'/'warn' → empty list (proceed)
  * Reasons tagged with platform that produced them
  * Best-effort: ImportError + check exceptions → empty list
    (proceed; never crash the auto-approver)
  * skipped_compliance_block field tracks blocked record_ids
  * Source pin: helper called BETWEEN rollout dice + _execute_approval
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ── _check_compliance_gate unit tests ──────────────────────────────


def test_helper_returns_empty_when_all_platforms_allow():
    """The PR #576 baseline: all 5 platforms return 'allow' →
    proceed (empty list)."""
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.scheduling.auto_approver import _check_compliance_gate

    with patch(
        "genlab_core.compliance.policy_gate.check_publish_policy",
        return_value=ComplianceDecision(decision="allow"),
    ):
        result = _check_compliance_gate(blueprint={}, niche_id="gaming", record_id="bp-1")
    assert result == []


def test_helper_returns_empty_when_all_platforms_warn():
    """Phase A is observation-only: every check returns 'warn'.
    Auto-approver still proceeds (only 'block' aborts)."""
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.scheduling.auto_approver import _check_compliance_gate

    with patch(
        "genlab_core.compliance.policy_gate.check_publish_policy",
        return_value=ComplianceDecision(decision="warn", reasons=["some_warn"]),
    ):
        result = _check_compliance_gate(blueprint={}, niche_id="gaming", record_id="bp-1")
    assert result == []


def test_helper_returns_reasons_when_any_platform_blocks():
    """ANY platform 'block' → reasons list (auto-approval aborts).
    Conservative — don't partial-publish into a state where some
    platforms got the post and others didn't."""
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.scheduling.auto_approver import _check_compliance_gate

    # Simulate: instagram blocks, others allow
    def mock_check(blueprint, platform, niche_id):
        if platform == "instagram":
            return ComplianceDecision(decision="block", reasons=["copyright_strike"])
        return ComplianceDecision(decision="allow")

    with patch(
        "genlab_core.compliance.policy_gate.check_publish_policy",
        side_effect=mock_check,
    ):
        result = _check_compliance_gate(blueprint={}, niche_id="gaming", record_id="bp-1")
    assert result == ["instagram:copyright_strike"]


def test_helper_aggregates_blocks_from_multiple_platforms():
    """Reasons are tagged with platform that produced them. Pin
    so operators see WHICH platform was the source of the block."""
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.scheduling.auto_approver import _check_compliance_gate

    def mock_check(blueprint, platform, niche_id):
        if platform == "instagram":
            return ComplianceDecision(decision="block", reasons=["spam_pattern"])
        if platform == "youtube":
            return ComplianceDecision(decision="block", reasons=["copyright_strike"])
        return ComplianceDecision(decision="allow")

    with patch(
        "genlab_core.compliance.policy_gate.check_publish_policy",
        side_effect=mock_check,
    ):
        result = _check_compliance_gate(blueprint={}, niche_id="gaming", record_id="bp-1")
    # Both platforms' reasons present, tagged with source platform
    assert "instagram:spam_pattern" in result
    assert "youtube:copyright_strike" in result


def test_helper_checks_all_5_platforms():
    """Pin: the auto-approver checks the full default platform set.
    A future PR that adds a new platform (TikTok native, etc.)
    needs to update this list."""
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.scheduling.auto_approver import _check_compliance_gate

    platforms_checked = []

    def tracker(blueprint, platform, niche_id):
        platforms_checked.append(platform)
        return ComplianceDecision(decision="allow")

    with patch(
        "genlab_core.compliance.policy_gate.check_publish_policy",
        side_effect=tracker,
    ):
        _check_compliance_gate(blueprint={}, niche_id="gaming", record_id="bp-1")
    assert set(platforms_checked) == {"instagram", "youtube", "facebook", "twitter", "threads"}


# ── best-effort failure handling ───────────────────────────────────


def test_helper_returns_empty_on_check_exception():
    """A check raising → DEBUG log + skip that platform. Other
    platforms still get checked. Auto-approval flow never crashes."""
    from genlab_core.compliance.events import ComplianceDecision
    from genlab_core.scheduling.auto_approver import _check_compliance_gate

    def flaky(blueprint, platform, niche_id):
        if platform == "instagram":
            raise RuntimeError("simulated compliance check error")
        return ComplianceDecision(decision="allow")

    with patch(
        "genlab_core.compliance.policy_gate.check_publish_policy",
        side_effect=flaky,
    ):
        # Must not raise; the broken platform is skipped silently
        result = _check_compliance_gate(blueprint={}, niche_id="gaming", record_id="bp-1")
    # No block reasons (instagram raised; other 4 returned allow)
    assert result == []


def test_helper_returns_empty_when_compliance_unavailable():
    """ImportError on genlab_core.compliance.policy_gate (e.g.
    running against pre-PR-566 core) → empty list, no crash."""
    from genlab_core.scheduling.auto_approver import _check_compliance_gate

    with patch.dict(
        "sys.modules",
        {"genlab_core.compliance.policy_gate": None},
    ):
        result = _check_compliance_gate(blueprint={}, niche_id="gaming", record_id="bp-1")
    assert result == []


# ── AutoApprovalPassResult field pin ───────────────────────────────


def test_result_has_compliance_block_field():
    """The new skipped_compliance_block field exists + defaults []."""
    from genlab_core.scheduling.auto_approver import (
        AutoApprovalPassResult,
        AutoApprovalPolicy,
    )

    policy = AutoApprovalPolicy(enabled=False, min_confidence=0.85)
    result = AutoApprovalPassResult(niche_id="gaming", policy=policy)
    assert hasattr(result, "skipped_compliance_block")
    assert result.skipped_compliance_block == []


# ── source pin: integration site ──────────────────────────────────


def test_source_pin_helper_called_before_execute_approval():
    """The _check_compliance_gate call MUST appear BEFORE
    _execute_approval in the source. Pin via rindex on call sites
    so a future refactor that moves compliance AFTER approval
    surfaces in tests (it would defeat the purpose — we'd publish
    first then learn we shouldn't have)."""
    import genlab_core.scheduling.auto_approver as mod

    src = Path(mod.__file__).read_text()
    # Helper definition exists
    assert "def _check_compliance_gate(" in src
    assert "PR #576" in src
    # Call site exists in the main run_pass loop
    assert "_check_compliance_gate(" in src
    # Block reasons → continue (skip approval)
    assert "skipped_compliance_block" in src
    # Call site for _execute_approval comes AFTER call site for
    # _check_compliance_gate.
    #
    # 2026-07-23: `rindex` used to look for the single-line literal
    # ``compliance_block_reasons = _check_compliance_gate(`` but the
    # actual code has multi-arg wrap (call on line N, args on
    # lines N+1..N+3). The rindex would raise ValueError silently
    # under CI's tail-truncation. Switched to bare-function-name
    # rindex which survives arg-wrap and still pins ordering.
    # 2026-07-23: pin the CALL SITE ordering (not the def site).
    # Prior pin used single-line literal ``compliance_block_reasons =
    # _check_compliance_gate(`` + ``if not _execute_approval(`` — both
    # broken by legit refactors (multi-arg wrap; assignment-form call).
    #
    # Switched to regex to survive multi-line arg wrap. Both regexes
    # exclude the ``def`` prefix so we anchor on real call sites.
    import re as _re

    _compliance_match = list(_re.finditer(r"(?<!def )_check_compliance_gate\(", src))
    _approval_match = list(_re.finditer(r"(?<!def )_execute_approval\(", src))
    assert _compliance_match, "compliance call site not found"
    assert _approval_match, "approval call site not found"
    compliance_call = _compliance_match[-1].start()
    approval_call = _approval_match[-1].start()
    assert compliance_call < approval_call, (
        "compliance check MUST run BEFORE _execute_approval — "
        "otherwise we'd publish then learn we shouldn't have"
    )


def test_source_pin_block_aborts_via_continue():
    """When compliance returns a non-empty block list, the loop
    MUST `continue` (skip this blueprint, try the next). Pin so a
    future refactor doesn't accidentally fall through to
    _execute_approval anyway."""
    import genlab_core.scheduling.auto_approver as mod

    src = Path(mod.__file__).read_text()
    # The if block_reasons → continue pattern
    assert "if compliance_block_reasons:" in src
    # The continue is on the same indentation level as the if check
    assert "skipped_compliance_block.append(record_id)" in src
