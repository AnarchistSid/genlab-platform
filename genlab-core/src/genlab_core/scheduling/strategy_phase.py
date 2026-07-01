"""PhaseConfig loader — reads operator-accepted Strategist proposals and
materialises per-niche configuration overrides for the reward shaper,
auto-approval gate, and writer.

This is the "action-side" half of the Strategist loop:

  Strategist runs weekly → proposes phase_shift/reward_weight/novelty_rate/
  gate_threshold changes → operator reviews via dashboard → operator's
  accepted proposals get read by THIS MODULE, which surfaces them as
  PhaseConfig(...) objects the consumer modules already know how to use.

Design principles:

1. **Fail-closed to defaults.** Any error reading the DB, parsing a
   proposal, or applying an override returns the built-in default. A
   corrupt Strategist report NEVER breaks reward computation or
   auto-approval — the worst case is "operator's proposal ignored."

2. **Feature-flagged OFF by default.** ``GENLAB_STRATEGIST_INTEGRATION_ENABLED=1``
   activates reading from strategist_reports. Without the flag,
   ``get_phase_config()`` always returns the default. This is
   defense-in-depth: even if a proposal writes something silly, prod
   only sees the change when the operator explicitly opts in.

3. **Cached per-process for ~5 min.** The reward shaper is called
   inside pipeline stages that fire hundreds of times per run — we
   can't hit the DB for every reward computation. TTL-cache surfaces
   proposal changes within 5 min without hammering the DB.

4. **Read-only.** This module NEVER writes to strategist_reports.
   Operator's accept/reject decisions live at the dashboard layer
   (``/api/v1/strategist/reports/<id>/review``); we only read the
   result.

See ``docs/STRATEGIST-SPEC.md`` §5 for the integration architecture.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from genlab_core.intelligence.proposal_schema import StrategicPhase

logger = logging.getLogger(__name__)

# Cache TTL — 5 minutes. Balances "operator proposal takes effect quickly"
# against "we don't hammer the DB on every reward compute."
_CACHE_TTL_SEC = 300.0
_cache: dict[str, tuple[float, PhaseConfig]] = {}


def _integration_enabled() -> bool:
    """Feature-flag check. Default OFF so ONLY explicit opt-in reads
    strategist_reports. Safe rollout path: operator watches Strategist
    reports for 2-3 weeks, THEN sets the flag to activate integration."""
    return os.environ.get("GENLAB_STRATEGIST_INTEGRATION_ENABLED", "").lower() == "true"


@dataclass
class PhaseConfig:
    """Per-niche configuration derived from the operator-accepted Strategist proposals.

    All fields are OPTIONAL — a niche with no accepted proposals gets
    default field values, and consumer modules fall back to their
    hardcoded defaults when a field is None.

    The consumer contract:

    - ``reward_weight_overrides`` — reward shaper multiplies BASE_WEIGHTS
      by these per (platform, metric). None = use BASE_WEIGHTS unchanged.
    - ``gate_threshold_override`` — auto_approval_gate uses this in place
      of composite_score >= 0.3. None = 0.3 default.
    - ``novelty_rate_override`` — push_to_backlog force-explore rate;
      None = 0.25 default.
    - ``detected_phase`` — informational only; consumers can log it but
      no consumer today branches on it directly. (Reserved for
      future phase-specific defaults if we skip the per-field overrides.)
    """

    niche_id: str
    detected_phase: StrategicPhase = StrategicPhase.BOOTSTRAP
    reward_weight_overrides: dict[str, float] = field(default_factory=dict)
    gate_threshold_override: float | None = None
    novelty_rate_override: float | None = None
    active_findings: list[str] = field(default_factory=list)
    # Provenance — set when config is derived from a real report.
    # Empty when returning the default (no accepted proposals yet).
    source_report_id: str = ""
    source_report_run_at: str = ""


_DEFAULT_CONFIG = PhaseConfig(niche_id="__default__")


def get_phase_config(niche_id: str) -> PhaseConfig:
    """Return the current PhaseConfig for a niche.

    Cached for _CACHE_TTL_SEC. Fail-closed to _DEFAULT_CONFIG on any error
    (DB unreachable, malformed proposal, feature flag off).

    Contract: NEVER raises. Callers can rely on always getting a valid
    PhaseConfig even when the underlying reads fail.
    """
    if not _integration_enabled():
        return _DEFAULT_CONFIG

    now = time.monotonic()
    cached = _cache.get(niche_id)
    if cached is not None:
        cached_at, cached_config = cached
        if now - cached_at < _CACHE_TTL_SEC:
            return cached_config

    try:
        config = _load_phase_config(niche_id)
    except Exception as exc:
        # Fail-closed: any DB error, malformed JSON, missing table → default.
        # We log so the operator can investigate, but never let it break
        # reward computation or the gate.
        logger.warning(
            "strategy_phase.load_failed niche=%s err=%s — using default",
            niche_id,
            exc,
        )
        config = _DEFAULT_CONFIG

    _cache[niche_id] = (now, config)
    return config


def _load_phase_config(niche_id: str) -> PhaseConfig:
    """Query strategist_reports for the latest operator-accepted proposals
    and materialise them into a PhaseConfig.

    Split out of get_phase_config() so tests can exercise the DB path
    without going through the cache + feature flag.
    """
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return PhaseConfig(niche_id=niche_id)

    # Fetch the most recent REVIEWED report for this niche, with its
    # accepted proposals. Only reviewed reports contribute — a pending
    # report means the operator hasn't decided yet.
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute(
            """
            SELECT id, run_at, detected_phase, proposals, proposals_accepted
            FROM strategist_reports
            WHERE niche_id = %s
              AND reviewed_at IS NOT NULL
              AND proposals_accepted IS NOT NULL
            ORDER BY run_at DESC
            LIMIT 1
            """,
            (niche_id,),
        ).fetchone()

        # Also fetch active learning_findings — these feed the writer prompt.
        findings_rows = conn.execute(
            """
            SELECT finding_text FROM learning_findings
            WHERE niche_id = %s AND active = true
            ORDER BY evidence_count DESC LIMIT 5
            """,
            (niche_id,),
        ).fetchall()
        findings = [r["finding_text"] for r in findings_rows] if findings_rows else []

    if not row:
        # No reviewed reports yet — return default with findings if any
        return PhaseConfig(niche_id=niche_id, active_findings=findings)

    detected_phase = _parse_phase(row.get("detected_phase"))
    proposals = row.get("proposals") or []
    accepted_indices = row.get("proposals_accepted") or []
    # proposals_accepted is JSONB; can be [], [0, 2], or historical string.
    if isinstance(accepted_indices, str):
        import json

        try:
            accepted_indices = json.loads(accepted_indices)
        except (json.JSONDecodeError, ValueError):
            accepted_indices = []

    # Materialise the accepted proposals into PhaseConfig fields
    weight_overrides: dict[str, float] = {}
    gate_threshold: float | None = None
    novelty_rate: float | None = None

    if isinstance(proposals, list) and isinstance(accepted_indices, list):
        for idx in accepted_indices:
            try:
                p = proposals[int(idx)]
            except (IndexError, TypeError, ValueError):
                continue
            if not isinstance(p, dict):
                continue
            proposal_type = p.get("type")
            if proposal_type == "reward_weight":
                # target format: "ai_creators.reward_weight.instagram.saves"
                # or "ai_creators.reward_weight.instagram" as bag-of-weights
                # For safety, ignore malformed targets rather than crash.
                target = p.get("target", "")
                proposed = p.get("proposed")
                if isinstance(target, str) and isinstance(proposed, int | float):
                    weight_overrides[target] = float(proposed)
            elif proposal_type == "gate_threshold":
                proposed = p.get("proposed")
                try:
                    gate_threshold = float(proposed)
                    # Clamp to sane range — refuse to accept 0.99 or -1.0
                    if not 0.05 <= gate_threshold <= 0.85:
                        gate_threshold = None
                except (TypeError, ValueError):
                    pass
            elif proposal_type == "novelty_rate":
                proposed = p.get("proposed")
                try:
                    novelty_rate = float(proposed)
                    if not 0.0 <= novelty_rate <= 0.50:
                        novelty_rate = None
                except (TypeError, ValueError):
                    pass

    return PhaseConfig(
        niche_id=niche_id,
        detected_phase=detected_phase,
        reward_weight_overrides=weight_overrides,
        gate_threshold_override=gate_threshold,
        novelty_rate_override=novelty_rate,
        active_findings=findings,
        source_report_id=str(row.get("id", "")),
        source_report_run_at=str(row.get("run_at", "")),
    )


def _parse_phase(raw: Any) -> StrategicPhase:
    """Convert a stored phase string to the StrategicPhase enum. Fail-closed
    to BOOTSTRAP (the safest / most conservative default) on any error.

    The DB stores phase as TEXT (see migration y6t7u8v9w0x1) so we can't
    rely on the type — could be "GROWTH", "growth", NULL, etc.
    """
    if not isinstance(raw, str):
        return StrategicPhase.BOOTSTRAP
    try:
        return StrategicPhase(raw.upper())
    except ValueError:
        return StrategicPhase.BOOTSTRAP


def clear_cache() -> None:
    """Force a cache refresh — useful for tests + operator-triggered
    'apply my latest proposal NOW' actions."""
    _cache.clear()
