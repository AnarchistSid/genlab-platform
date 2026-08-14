"""Prompt templates for Strategist LLM calls.

Templates are pure functions of state — no side effects, no LLM calls. Tests
snapshot the rendered output to catch unintended drift in what we send to
the model.

See docs/STRATEGIST-SPEC.md §4 for the design.
"""

from __future__ import annotations

import json
from typing import Any

from genlab_core.intelligence.proposal_schema import SCHEMA_VERSION

# The system prompt is versioned. Bump SYSTEM_PROMPT_VERSION when changing
# the role/responsibilities/output rules — never silently mutate. Tests
# snapshot this so unintended edits are caught at review.
SYSTEM_PROMPT_VERSION = 1

SYSTEM_PROMPT = """You are the Strategist for Gen Lab, a video-first content automation system that runs 5 social media channels (ai_creators, gaming, sports, movies, anime) across 6 platforms (Instagram, YouTube, Facebook, X/Twitter, Threads, TikTok).

Your role is the MANAGER above a tactical optimization stack:
- Bandits (LinUCB + Thompson) pick content arms per blueprint
- Gates (auto-approval with 5 checks + LLM judge for borderlines) filter publishes
- Classifiers (XGBoost hook quality, conformal router) provide confidence signals

Your job each week:
1. Detect the strategic phase per niche (BOOTSTRAP → GROWTH → OPTIMIZE → MONETIZE → DEFEND)
2. Review last week's publishes + engagement + bandit shifts
3. Generate causal hypotheses for observed patterns
4. Propose concrete adaptations with reasoning + risk assessment
5. Surface cross-niche patterns as universal playbook entries

You are NOT the operator. Every proposal requires operator approval. You write proposals; the operator decides. Be specific, calibrated, and admit uncertainty.

Strategic phases:
- BOOTSTRAP (0-100 followers): novelty + reach > engagement. Ship aggressively.
- GROWTH (100-1K): shares + new_followers > engagement_rate. Optimize for spread.
- OPTIMIZE (1K-10K): engagement_rate + watch_time. Refine within learned patterns.
- MONETIZE (10K+): affiliate_clicks + conversion. Reward weights shift to revenue.
- DEFEND (plateaued): diversify formats + platforms to escape local optimum.

Output your analysis as JSON conforming exactly to the schema in the user prompt. Avoid generalities. Cite specific arm_ids, blueprint_ids, reward values, sample sizes. If you don't have enough evidence for a hypothesis, say "insufficient_evidence" explicitly rather than guessing.

OUTPUT RULES (non-negotiable):
- Respond with valid JSON ONLY. No prose preamble, no markdown fences.
- Conform exactly to the schema; unknown fields will be rejected.
- Every proposal must include reasoning ≥20 chars and expected_impact ≥20 chars.
- Every causal hypothesis must include ≥1 evidence item with sample size.
- Universal playbook entries require ≥2 evidence_niches.
"""


def build_user_prompt(state: dict[str, Any], schema_version: int = SCHEMA_VERSION) -> str:
    """Render a per-niche user prompt from collected state.

    `state` is the dict produced by StateCollector.collect(). Missing keys
    are tolerated — we substitute the literal string 'unknown' so the LLM
    has explicit signal about what's missing rather than guessing.

    Returns a multi-section prompt string ready for the LLM message body.
    """

    def _get(key: str, default: Any = "unknown") -> Any:
        v = state.get(key)
        return v if v is not None else default

    return f"""NICHE: {_get("niche_id")}
WEEK OF: {_get("week_of")}
TIMESTAMP: {_get("run_at")}

CHANNEL STATE
-------------
Follower count: {_get("follower_count")} ({_get("follower_delta_4w", "no_baseline")})
Engagement rate 7d: {_get("engagement_rate_7d")}%
Watch time avg 7d: {_get("watch_time_avg_7d")}s
Total publishes last 7d: {_get("n_publishes_7d")}
Top performing blueprint: {_get("top_blueprint_id")} ({_get("top_blueprint_metrics", "no_data")})
Bottom performing blueprint: {_get("bot_blueprint_id")} ({_get("bot_blueprint_metrics", "no_data")})

BANDIT POSTERIORS (top + bottom arms)
-------------------------------------
{_format_bandit_state(_get("bandit_state", []))}

VALIDATION HARNESS
------------------
Spearman last 7d: {_get("spearman_7d")} (interpretation: {_get("validation_interpretation")})
Calibration agreement: {_get("calibration_agreement_pct")}% ({_get("calibration_n_rows")} rows)

CONFORMAL ROUTER STATE
----------------------
Coverage achieved: {_get("conformal_coverage_pct")}%
Abstain rate: {_get("conformal_abstain_pct")}%

COST EFFICIENCY
---------------
Cost per blueprint: ${_get("cost_per_blueprint")}
Cost per published post: ${_get("cost_per_published")}

RECENT PUBLISHES SAMPLE
-----------------------
{_format_publishes(_get("recent_publishes", []))}

CROSS-NICHE COMPARISON (brief)
------------------------------
{_format_cross_niche(_get("other_niches_summary", {}))}

ACTIVE LEARNINGS (existing findings for this niche)
---------------------------------------------------
{_format_findings(_get("active_findings", []))}

LAST WEEK'S PROPOSALS + OUTCOMES
--------------------------------
{_format_last_week(_get("last_week_outcomes", []))}

COUNTERFACTUAL REPLAY (top DR arms — offline policy eval)
---------------------------------------------------------
{_format_counterfactual_replay(_get("counterfactual_replay", None))}

COMPETITOR CONTEXT (top-tier creators outperforming our reach)
--------------------------------------------------------------
{_format_competitor_context(_get("competitor_context", []))}

ACTIVE + RECENT EXPERIMENTS (don't re-propose what's running)
-------------------------------------------------------------
{_format_active_experiments(_get("active_experiments", {}))}

---

Generate your weekly report as JSON conforming to this schema (schema_version={schema_version}):

{_SCHEMA_HINT}
{_PROPOSED_FIELD_RULES}
Respond with JSON only. No preamble, no markdown.
"""


def _format_bandit_state(arms: list[dict[str, Any]]) -> str:
    """Format the top + bottom 5 arms by reward as a readable table."""
    if not arms:
        return "  (no bandit state available)"
    sorted_arms = sorted(arms, key=lambda a: a.get("reward", 0), reverse=True)
    top = sorted_arms[:5]
    bottom = sorted_arms[-5:] if len(sorted_arms) > 5 else []
    lines = ["  Top arms:"]
    for a in top:
        lines.append(
            f"    {a.get('arm_id', '?'):<45} n={a.get('n_plays', '?'):<4} reward={a.get('reward', '?')}"
        )
    if bottom:
        lines.append("  Bottom arms:")
        for a in bottom:
            lines.append(
                f"    {a.get('arm_id', '?'):<45} n={a.get('n_plays', '?'):<4} reward={a.get('reward', '?')}"
            )
    return "\n".join(lines)


def _format_publishes(publishes: list[dict[str, Any]]) -> str:
    if not publishes:
        return "  (no recent publishes)"
    return "\n".join(
        f"  {p.get('blueprint_id', '?')[:8]} | {p.get('platform', '?'):<10} | "
        f"reward={p.get('reward', '?')} | hook={(p.get('hook_text') or '?')[:50]}"
        for p in publishes
    )


def _format_cross_niche(summary: dict[str, Any]) -> str:
    if not summary:
        return "  (no cross-niche summary)"
    return "\n".join(f"  {niche}: {info}" for niche, info in summary.items())


def _format_findings(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "  (no active findings)"
    return "\n".join(
        f"  - {f.get('finding_text', '?')} (evidence n={f.get('evidence_count', '?')})"
        for f in findings
    )


def _format_last_week(outcomes: list[dict[str, Any]]) -> str:
    if not outcomes:
        return "  (first run — no prior proposals)"
    lines = []
    for o in outcomes:
        action = o.get("operator_action", "unreviewed")
        lines.append(f"  - {o.get('proposal_summary', '?')} → {action}")
    return "\n".join(lines)


def _format_counterfactual_replay(replay: dict[str, Any] | None) -> str:
    """Intervention 7 consumer wire (2026-07-02) — DR replay artifact.

    Renders the top-5 arms by DR reward (falling back to IPS when DR
    null) as evidence the Strategist can cite in its proposals.
    Returns an ``(no artifact yet)`` cold-start line when the monthly
    runner hasn't fired or the flag is off — LLM sees explicit
    missing-signal rather than silence.

    The ``dr_enabled`` flag in the artifact is surfaced so the LLM
    knows whether the ``dr_reward`` field is Ridge-model output or
    a null stub (in which case IPS is the meaningful signal).
    """
    if not replay or not isinstance(replay, dict):
        return "  (no artifact yet — monthly runner or DR flag disabled)"
    per_arm = replay.get("per_arm") or []
    if not per_arm:
        return "  (empty replay — no arms with reward+context in window)"
    dr_enabled = bool(replay.get("dr_enabled"))
    signal_label = "DR" if dr_enabled else "IPS (DR stub — flag off)"

    def _score(arm: dict[str, Any]) -> float:
        dr = arm.get("dr_reward")
        if dr is not None:
            return float(dr)
        ips = arm.get("ips_reward")
        return float(ips) if ips is not None else float("-inf")

    scored = [a for a in per_arm if a.get("n_with_reward", 0) >= 3]
    scored.sort(key=_score, reverse=True)
    top = scored[:5]
    if not top:
        return "  (no arm cleared n>=3 threshold — sample too thin for offline eval)"

    lines = [f"  Signal: {signal_label}, window={replay.get('window_days', '?')}d"]
    for a in top:
        arm_id = str(a.get("arm_id", "?"))
        n = a.get("n_with_reward", "?")
        ips = a.get("ips_reward")
        dr = a.get("dr_reward")
        ips_str = f"{ips:.3f}" if isinstance(ips, (int, float)) else "-"
        dr_str = f"{dr:.3f}" if isinstance(dr, (int, float)) else "-"
        lines.append(f"    {arm_id[:45]:<45} n={n:<4} ips={ips_str:<6} dr={dr_str}")
    return "\n".join(lines)


def _format_active_experiments(summary: dict[str, Any]) -> str:
    """Phase 3.D session 3 (2026-08-14): render running experiments +
    recent verdicts so the strategist doesn't propose the same
    experiment twice AND can cite recent verdicts in its reasoning.

    Empty (cold-start or DB failure) renders explicit line so LLM
    sees the missing-signal state (same pattern as counterfactual
    replay + competitor context)."""
    if not summary or not isinstance(summary, dict):
        return "  (no experiment data available)"
    running = summary.get("running") or []
    recent = summary.get("recent_verdicts") or []
    if not running and not recent:
        return "  (no active or recent experiments)"

    lines = []
    if running:
        lines.append(f"  Currently running ({len(running)}):")
        for exp in running[:5]:
            arms = exp.get("arms") or []
            arms_str = " vs ".join(str(a)[:25] for a in arms[:2]) or "?"
            lines.append(
                f"    {arms_str} · {exp.get('age_days', 0):.1f}d / "
                f"{exp.get('duration_days', 7)}d"
            )
    else:
        lines.append("  (no experiments currently running)")

    if recent:
        lines.append(f"  Recent verdicts ({len(recent)}):")
        for v in recent[:5]:
            arms = v.get("arms") or []
            arms_str = " vs ".join(str(a)[:25] for a in arms[:2]) or "?"
            verdict = v.get("verdict") or v.get("status") or "?"
            p = v.get("prob_b_beats_a")
            p_str = f"p_b={p:.2f}" if isinstance(p, (int, float)) else "p_b=?"
            lines.append(f"    {arms_str} → {verdict} ({p_str})")
    return "\n".join(lines)


def _format_competitor_context(rows: list[dict[str, Any]]) -> str:
    """Phase 3.A session 3 (2026-08-14): render top competitor deltas.

    Cold-start / flag-off returns an explicit missing-signal line so
    the LLM knows this signal is unavailable (mirrors
    ``_format_counterfactual_replay``). Rows already excluded the
    thin-baseline cases in the collector, so any output here is a
    reasonable comparison the strategist can cite in proposals.
    """
    import os

    flag = os.environ.get(
        "GENLAB_COMPETITOR_CONTEXT_ENABLED", "0",
    ).strip().lower()
    if flag not in {"1", "true", "yes"}:
        return "  (flag disabled — GENLAB_COMPETITOR_CONTEXT_ENABLED=0)"
    if not rows:
        return "  (no competitor rows yet — daily runner may not have fired)"
    lines = [
        "  Top competitor uploads outperforming our niche-median (last 48h):",
    ]
    for r in rows:
        label = (r.get("competitor_label") or "?")[:20]
        title = (r.get("title") or "?")[:55]
        views = r.get("view_count") or 0
        ratio = r.get("delta_ratio")
        ratio_str = f"{ratio:.1f}x" if isinstance(ratio, (int, float)) else "?"
        lines.append(
            f"    {label:<20} views={views:>10,} delta={ratio_str:<8} — {title}"
        )
    return "\n".join(lines)


# Hint shown to the LLM so it knows the expected JSON shape. Kept aligned
# with proposal_schema.py — when adding fields to Pydantic models update
# this hint in the same commit. Pin tests verify the alignment.
#
# 2026-08-11: `proposed` was documented as free-text, so the LLM emitted
# prose ("Set novelty_rate to 0.30 during BOOTSTRAP") for numeric types.
# Downstream auto-accept classifiers (proposal_auto_accept.py) require
# structured values (numbers for reward_weight / gate_threshold /
# novelty_rate; dict for arm_add). Prose broke every auto-accept for
# these 3 types — proposals sat unreviewed forever. Now the hint
# includes TYPE-SPECIFIC examples for each proposal type + explicit
# PROPOSED_FIELD_RULES section clarifying WHAT `proposed` must contain
# per type + WHERE to put the justification prose (in `reasoning` and
# `expected_impact`, NOT in `proposed`).
_SCHEMA_HINT = json.dumps(
    {
        "detected_phase": "BOOTSTRAP | GROWTH | OPTIMIZE | MONETIZE | DEFEND",
        "phase_evidence": "20+ char justification citing concrete numbers",
        "weekly_summary": "50+ char human-readable summary",
        "proposals": [
            {
                "_comment": "See PROPOSED_FIELD_RULES below for the exact shape of `proposed` per type. The examples below show ONE proposal per type.",
                "type": "arm_add",
                "target": "gaming.arms",
                "current": None,
                "proposed": {
                    "arm_id": "style:gaming:tier_list_reaction",
                    "prior_alpha": 1.0,
                    "prior_beta": 1.0,
                },
                "reasoning": "20+ char explanation citing evidence",
                "expected_impact": "20+ char observable outcome",
                "risk": "low",
                "urgency": "this_week",
            },
            {
                "type": "reward_weight",
                "target": "ai_creators.reward_weight.instagram.saves",
                "current": 0.25,
                "proposed": 0.35,
                "reasoning": "Saves correlate 0.42 with follower growth in ai_creators. Current weight underweights this signal.",
                "expected_impact": "Bandit shifts allocation toward save-generating hooks; 168h reward increases ~10%.",
                "risk": "low",
                "urgency": "this_week",
            },
            {
                "type": "gate_threshold",
                "target": "sports.auto_approval",
                "current": 0.30,
                "proposed": 0.35,
                "reasoning": "20+ char rationale",
                "expected_impact": "20+ char observable outcome",
                "risk": "low",
                "urgency": "next_sprint",
            },
            {
                "type": "novelty_rate",
                "target": "anime.bandit.novelty_rate",
                "current": 0.25,
                "proposed": 0.30,
                "reasoning": "20+ char rationale",
                "expected_impact": "20+ char observable outcome",
                "risk": "low",
                "urgency": "this_week",
            },
            {
                "type": "phase_shift",
                "target": "movies.phase",
                "current": "BOOTSTRAP",
                "proposed": "GROWTH",
                "reasoning": "20+ char rationale",
                "expected_impact": "20+ char observable outcome",
                "risk": "medium",
                "urgency": "next_sprint",
            },
            {
                "type": "manual_action",
                "target": "operator.attention",
                "current": None,
                "proposed": "Free-form description of the manual action the operator must take.",
                "reasoning": "20+ char rationale",
                "expected_impact": "20+ char observable outcome",
                "risk": "medium",
                "urgency": "ship_now",
            },
        ],
        "causal_hypotheses": [
            {
                "pattern": "observed pattern",
                "hypothesis": "20+ char explanation",
                "confidence": "high | medium | low",
                "evidence": ["evidence with n=X"],
                "testable_prediction": "what would confirm/refute",
            }
        ],
        "universal_playbook_proposals": [
            {
                "pattern_text": "20+ char pattern",
                "evidence_niches": ["min 2 niches"],
                "confidence": "high | medium | low",
            }
        ],
    },
    indent=2,
)

# Additional guidance printed AFTER the schema hint so the LLM sees the
# structural rules explicitly. Adding this as a separate string (rather
# than folding it into _SCHEMA_HINT above) means the LLM sees natural-
# language rules in addition to concrete examples.
_PROPOSED_FIELD_RULES = """
PROPOSED_FIELD_RULES — the `proposed` field is TYPE-SPECIFIC:

* arm_add       -> object: {"arm_id": "<dim>:<niche>:<variant>", "prior_alpha": 1.0, "prior_beta": 1.0}
                   Example arm_id shapes: "style:gaming:tier_list_reaction",
                   "transform__hook_framing__tactical_breakdown",
                   "hook_type:sports:comparison"
* reward_weight -> NUMBER (float) in [0.0, 5.0]. NO prose in this field.
                   Target format REQUIRED: "{niche}.reward_weight.{platform}.{metric}"
                   Example: target="ai_creators.reward_weight.instagram.saves", proposed=0.35
                   Anything else is silently ignored by the reward shaper.
* gate_threshold -> NUMBER (float) in [0.05, 0.85]. Default 0.30. Auto-accept
                    only for changes within 0.15 of default (i.e. [0.15, 0.45]).
* novelty_rate  -> NUMBER (float) in [0.0, 0.50]. Default 0.25. Auto-accept
                   only for changes within 0.15 of default (i.e. [0.10, 0.40]).
* phase_shift   -> STRING enum: "BOOTSTRAP" | "GROWTH" | "OPTIMIZE" | "MONETIZE" | "DEFEND"
* playbook_update -> STRING (prose describing the playbook change)
* manual_action -> STRING (prose describing what the operator must do)

CRITICAL: Put justification prose in `reasoning` and `expected_impact`,
NEVER in `proposed`. If a numeric type gets a string `proposed`, the
downstream classifier will reject the proposal as malformed and it
will sit unapplied indefinitely. The `reasoning` field has no length
cap — put all the "why" there.
"""


def render_messages(state: dict[str, Any]) -> list[dict[str, str]]:
    """Build the messages list for an Anthropic-compatible chat call.

    Returns a list with one user message; the system prompt is passed
    separately (Anthropic's convention) — see anthropic_client.generate_report.
    """
    return [{"role": "user", "content": build_user_prompt(state)}]


def get_system_prompt() -> str:
    """Return the system prompt verbatim. Pin tests snapshot this."""
    return SYSTEM_PROMPT
