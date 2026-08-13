"""LLM-based per-proposal reviewer.

**PHASE 2 SCAFFOLD** — prompt + client wrapper + tests. Integration
into the auto-accept pipeline (as the "abstain" fallback layer)
is deferred to a follow-up session.

## Motivation

The heuristic classifiers in ``proposal_auto_accept.py`` cover the
common shapes: arm_add / reward_weight / gate_threshold /
novelty_rate. Anything outside those falls to "operator_gate:
unknown_shape" and sits in the review queue.

Tonight's (2026-08-13) manual review found ~50 proposals worth
accepting that fell to unknown_shape, plus ~60 worth rejecting on
state-drift grounds. Extending the shape-classifiers helped, but
some proposals need genuine judgment ("does this recommendation
make sense given the current system state?").

An LLM reviewer with system-state context fills that gap. It runs
AFTER the heuristic classifiers abstain — cheap Haiku call, ~$0.001
per proposal, ~200ms latency. Cost bounded because it only fires
on shapes the classifiers punt on.

## Prompt design

The reviewer sees:
  1. Proposal text (type/target/current/proposed/reasoning)
  2. Current system state snapshot (relevant metrics for the
     proposal's niche)
  3. Recent history — did similar proposals accept/reject well?
  4. Rule #23 constraints reminder (TikTok/X out of scope)

Output is a strict JSON verdict:
  {
    "decision": "accept" | "reject" | "abstain",
    "confidence": 0.0-1.0,
    "reason": "<one-line justification>",
    "risk_flags": ["<optional risk flags>"]
  }

## Integration flow (deferred)

```
strategist_apply worker loop:
  for proposal in unreviewed:
      heuristic = classify_arm_add(proposal, ...)
      if heuristic.should_auto_accept:
          accept_and_record()
          continue

      llm_verdict = reviewer.review(proposal, state)
      if llm_verdict.decision == "accept" and llm_verdict.confidence >= 0.7:
          accept_and_record(source="llm_reviewer")
      elif llm_verdict.decision == "reject" and llm_verdict.confidence >= 0.7:
          reject_and_record(source="llm_reviewer")
      else:
          leave_for_operator()  # abstain or low-confidence
```

## Safety

Guard rails identical to heuristic path:
  * Rate limit reuses ``get_max_auto_accepts_per_week()``
  * All LLM-driven decisions tagged in ``action_taken_source``
    so operator can filter by "auto vs human vs llm" for audit
  * If Anthropic call fails → abstain, don't crash
  * If output isn't valid JSON → abstain
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)

_ENABLE_ENV_VAR = "GENLAB_LLM_REVIEWER_ENABLED"

# Model choice: Haiku for cost. A cheap review call (~800 input +
# 300 output tokens) is ~$0.001 with Claude Haiku vs $0.02 with
# Sonnet. Reviewer decisions are gated by confidence threshold and
# rate-limited, so we accept slightly noisier Haiku output.
_REVIEWER_MODEL = "claude-haiku-4-5-20251001"
_REVIEWER_MAX_TOKENS = 500
_REVIEWER_TIMEOUT_S = 30

# Confidence threshold for auto-action. Below this, the reviewer
# abstains and the proposal goes to operator review.
CONFIDENCE_THRESHOLD_ACCEPT: float = 0.70
CONFIDENCE_THRESHOLD_REJECT: float = 0.70


@dataclass(frozen=True)
class ReviewVerdict:
    decision: Literal["accept", "reject", "abstain"]
    confidence: float
    reason: str
    risk_flags: tuple[str, ...] = ()


class LLMClient(Protocol):
    """Injectable LLM client for testability. Prod uses
    genlab_core.intelligence.anthropic_client wrappers; tests use
    mock clients that return deterministic verdicts."""

    def review(self, system_prompt: str, user_prompt: str) -> str:
        """Return raw response text. Callers parse the JSON."""
        ...


def is_enabled() -> bool:
    return os.environ.get(_ENABLE_ENV_VAR, "").strip().lower() in (
        "1", "true", "yes", "on",
    )


SYSTEM_PROMPT = """\
You are a proposal reviewer for Gen Lab's autonomous content system.
Your job: decide whether a strategist-generated proposal should be
auto-applied to production config, auto-rejected, or escalated to
operator review.

## Context

Gen Lab publishes short-form video reels for 5 niches (ai_creators,
gaming, sports, movies, anime) across 4 in-scope platforms (Facebook,
Instagram, YouTube, Threads).

## Rules

1. **Scope (rule #23 - strict)**: TikTok and X/Twitter are OUT of scope.
   Any proposal advocating publishing to, distributing on, or optimizing
   for those platforms MUST be rejected.

2. **Organic-only**: Never accept proposals suggesting paid boosts,
   audience seeding services, or engagement pods.

3. **State-drift skepticism**: If the proposal's reasoning cites
   "Spearman=0.0", "reward signal broken", "engagement metric ingestion",
   check whether the concern is stale (current reward loop is populated
   across all 20 niche×platform slots as of 2026-08-13). If stale,
   reject.

4. **Config changes only**: You can accept structural config changes
   (arm additions, weight tunings, threshold adjustments). You must
   ABSTAIN on manual_action proposals — those need human judgment.

5. **Bias toward abstain when uncertain**: Missing acceptance costs
   the queue one more item; wrong acceptance costs the system.

## Output format

Return valid JSON only, no prose before/after:
{
  "decision": "accept" | "reject" | "abstain",
  "confidence": <float 0.0 to 1.0>,
  "reason": "<one sentence>",
  "risk_flags": ["<optional>"]
}
"""


def build_user_prompt(
    proposal: dict[str, Any], niche_id: str, state_snapshot: dict[str, Any],
) -> str:
    """Format the per-review prompt. Kept short — Haiku responds faster
    to concise input and JSON contract is easier to enforce."""
    lines = [
        f"NICHE: {niche_id}",
        f"PROPOSAL TYPE: {proposal.get('type', '?')}",
        f"TARGET: {str(proposal.get('target', ''))[:200]}",
        f"URGENCY: {proposal.get('urgency', '?')}",
        f"RISK: {proposal.get('risk', '?')}",
        "",
        "CURRENT STATE (from strategist):",
        f"  {str(proposal.get('current', ''))[:400]}",
        "",
        "PROPOSED:",
        f"  {json.dumps(proposal.get('proposed', ''), default=str)[:600]}",
        "",
        "STRATEGIST REASONING:",
        f"  {str(proposal.get('reasoning', ''))[:400]}",
        "",
        "SYSTEM STATE SNAPSHOT:",
        f"  {json.dumps(state_snapshot, default=str)[:800]}",
        "",
        "Decide: accept, reject, or abstain. Output JSON only.",
    ]
    return "\n".join(lines)


def parse_verdict(raw_text: str) -> ReviewVerdict:
    """Parse the LLM's JSON response into a ReviewVerdict. On any
    parse failure, return an abstain verdict — never raise."""
    text = raw_text.strip()
    # Strip common LLM boilerplate: markdown code fences
    if text.startswith("```"):
        # Remove opening fence (optionally with `json`)
        lines = text.splitlines()
        # Drop first line + last if it's a closing fence
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(
            "[llm_reviewer] JSON decode failed: %s — abstaining. head=%r",
            exc, text[:120],
        )
        return ReviewVerdict(
            decision="abstain", confidence=0.0,
            reason=f"json_decode_failed: {exc}",
        )
    decision = str(obj.get("decision", "abstain")).lower().strip()
    if decision not in ("accept", "reject", "abstain"):
        return ReviewVerdict(
            decision="abstain", confidence=0.0,
            reason=f"unknown_decision: {decision!r}",
        )
    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reason = str(obj.get("reason", ""))[:300]
    risk_flags_raw = obj.get("risk_flags") or ()
    if isinstance(risk_flags_raw, list):
        risk_flags = tuple(str(r)[:80] for r in risk_flags_raw[:10])
    else:
        risk_flags = ()
    return ReviewVerdict(
        decision=decision, confidence=confidence,
        reason=reason, risk_flags=risk_flags,
    )


class Reviewer:
    """Per-proposal LLM reviewer.

    Fires only when heuristic classifiers abstain. Bounded via
    is_enabled() env flag + confidence thresholds for auto-action.
    """

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def review(
        self, proposal: dict[str, Any], niche_id: str,
        state_snapshot: dict[str, Any] | None = None,
    ) -> ReviewVerdict:
        """Return a ReviewVerdict. Never raises."""
        user_prompt = build_user_prompt(
            proposal, niche_id, state_snapshot or {},
        )
        try:
            raw = self._client.review(SYSTEM_PROMPT, user_prompt)
        except Exception as exc:
            logger.warning(
                "[llm_reviewer] LLM call failed: %s — abstaining", exc,
            )
            return ReviewVerdict(
                decision="abstain", confidence=0.0,
                reason=f"llm_call_failed: {exc}",
            )
        return parse_verdict(raw)


__all__ = [
    "CONFIDENCE_THRESHOLD_ACCEPT",
    "CONFIDENCE_THRESHOLD_REJECT",
    "LLMClient",
    "ReviewVerdict",
    "Reviewer",
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "is_enabled",
    "parse_verdict",
]
