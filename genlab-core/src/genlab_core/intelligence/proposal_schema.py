"""Pydantic schemas for Strategist outputs.

These define the structured contract between the Strategist LLM call and the
downstream consumers (dashboard, PhaseConfig, writer, arm_loader). The LLM is
prompted to emit JSON conforming to these schemas; Pydantic validates on read.

See docs/STRATEGIST-SPEC.md §4 for the full design.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class StrategicPhase(StrEnum):
    """The 5 strategic phases a niche can be in.

    Phases are detected from channel state and drive PhaseConfig (which in turn
    drives reward weights, gate thresholds, novelty rates). See §1 of the spec.
    """

    BOOTSTRAP = "BOOTSTRAP"  # 0-100 followers — novelty + reach win
    GROWTH = "GROWTH"  # 100-1K — shares + new_followers dominate
    OPTIMIZE = "OPTIMIZE"  # 1K-10K — engagement + retention
    MONETIZE = "MONETIZE"  # 10K+ — affiliate clicks + conversion
    DEFEND = "DEFEND"  # plateaued — diversify formats + platforms


class ProposalType(StrEnum):
    """The 7 proposal types the Strategist may emit.

    Type-whitelist is a hard safety boundary: the Strategist cannot propose
    arbitrary actions, only one of these well-defined classes that downstream
    handlers know how to apply (or refuse).
    """

    PHASE_SHIFT = "phase_shift"
    ARM_ADD = "arm_add"
    GATE_THRESHOLD = "gate_threshold"
    REWARD_WEIGHT = "reward_weight"
    NOVELTY_RATE = "novelty_rate"
    PLAYBOOK_UPDATE = "playbook_update"
    MANUAL_ACTION = "manual_action"  # requires operator do something the system can't


class ProposalRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProposalUrgency(StrEnum):
    SHIP_NOW = "ship_now"
    THIS_WEEK = "this_week"
    NEXT_SPRINT = "next_sprint"


class HypothesisConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Proposal(BaseModel):
    """A single Strategist proposal that requires operator approval.

    Each proposal is one atomic change with reasoning, expected impact, and
    risk. The dashboard surfaces proposals as accept/reject buttons; integration
    code (PR Strategist-3) translates accepted proposals into config changes.
    """

    model_config = ConfigDict(extra="forbid")

    type: ProposalType
    target: str = Field(
        description="Dotted path of what's being changed. "
        "e.g. 'ai_creators.phase', 'gaming.novelty_rate', 'anime.arms'."
    )
    current: Any = Field(description="Current value, or None for additions.")
    proposed: Any = Field(description="Proposed new value, or arm definition for arm_add.")
    reasoning: str = Field(min_length=20, description="Why this change is proposed.")
    expected_impact: str = Field(
        min_length=20,
        description="What the operator should observe if the change has its intended effect.",
    )
    risk: ProposalRisk
    urgency: ProposalUrgency


class CausalHypothesis(BaseModel):
    """A hypothesis about WHY a pattern in the data exists.

    The LLM observes patterns (bandit posterior shifts, engagement asymmetries,
    publish-time effects) and proposes causal explanations. Each hypothesis
    includes specific evidence (with sample sizes) and a testable prediction
    so it can be confirmed or refuted in future weeks.
    """

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(min_length=10, description="The observed pattern.")
    hypothesis: str = Field(min_length=20, description="The proposed causal explanation.")
    confidence: HypothesisConfidence
    evidence: list[str] = Field(
        min_length=1,
        description="Specific data points supporting the hypothesis. "
        "Must include sample sizes and effect magnitudes.",
    )
    testable_prediction: str = Field(
        min_length=10,
        description="A prediction that would confirm or refute the hypothesis "
        "in future data. Used for accountability over time.",
    )


class PlaybookProposal(BaseModel):
    """A cross-niche pattern observation proposed for the universal playbook.

    Patterns that hold across multiple niches earn a playbook entry, which
    seeds priors for new niches and informs writer prompts globally.
    """

    model_config = ConfigDict(extra="forbid")

    pattern_text: str = Field(min_length=20)
    evidence_niches: list[str] = Field(min_length=2, description="Niches where observed.")
    confidence: HypothesisConfidence


class StrategistReport(BaseModel):
    """The complete output of one Strategist run for one niche.

    Persisted to `strategist_reports` table. Operator reviews via dashboard,
    accepts/rejects individual proposals. Accepted proposals are applied by
    integration code (PR Strategist-3).
    """

    model_config = ConfigDict(extra="forbid")

    niche_id: str
    week_of: date = Field(description="Monday of the week analyzed.")
    run_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    detected_phase: StrategicPhase
    phase_evidence: str = Field(min_length=20)

    proposals: list[Proposal] = Field(default_factory=list)
    causal_hypotheses: list[CausalHypothesis] = Field(default_factory=list)
    universal_playbook_proposals: list[PlaybookProposal] = Field(default_factory=list)

    weekly_summary: str = Field(
        min_length=50,
        description="Human-readable summary suitable for Slack/email digest.",
    )

    # Set when persisting; not part of LLM output
    id: UUID = Field(default_factory=uuid4)


# Schema version — bump when changing the JSON contract the LLM must conform to.
# PR Strategist-2 will pass this in the prompt as "schema_version: X"; if the
# LLM emits a report tagged with an old version we know to reject/regenerate.
SCHEMA_VERSION = 1
