"""Platform compliance — Phase A foundation (PR #566, 2026-06-25).

The compliance moat that lets autonomy expansion happen without
ban risk. Every pre-publish decision, AI-content disclosure, copyright
flag, spam pattern detection, and account-health warning flows through
this package.

## Phase A roadmap (PRs #566-#570)

  #566 — THIS PR. compliance_events table + policy_gate skeleton +
         disclosure + account_health stubs. Observation-only mode.
  #567 — AI-content disclosure wire (YouTube/Meta/TikTok 2024
         disclosure policies). Consumer of disclosure.py.
  #568 — Copyright safety — attribution overlay + content fingerprint
         check before publish.
  #569 — Spam-pattern detection (caption/hashtag diversity, posting
         cadence jitter, affiliate-link density).
  #570 — Account health per-platform (engagement-rate cliff detection
         = shadowban early warning).

## Phase B roadmap (PRs #571-#578)

Autonomy expansion — only activates AFTER Phase A enforces. The
gate primitives in this package are designed to be the chokepoint
that Phase B respects.

## Public surface

  log_compliance_event(...) — durable audit log
  policy_gate.check_publish_policy(blueprint, platform) -> Decision
  disclosure.generate_ai_disclosure(platform) -> str
  account_health.get_signal(platform, niche_id) -> Signal | None

  ComplianceDecision dataclass — (decision, reasons, metadata)
  HealthSignal dataclass — (state, message, evidence)

All helpers FAIL-CLOSED on compliance check errors but FAIL-OPEN
on event logging (logging is observability, not enforcement —
losing one log row beats blocking a publish).
"""

# Import side-effects: register checks into the policy_gate at
# module-import time. Each module's `register_check(...)` call
# fires when this import runs, so any caller that pulls the
# compliance package (or anything transitive) gets ALL checks live.
#
#   copyright_safety (PR #568) — copyright_attribution_check
#   spam_detection   (PR #569) — spam_pattern_check
from genlab_core.compliance import (
    copyright_safety,  # noqa: F401
    spam_detection,  # noqa: F401
)
from genlab_core.compliance.events import ComplianceDecision, log_compliance_event

__all__ = ["ComplianceDecision", "log_compliance_event"]
