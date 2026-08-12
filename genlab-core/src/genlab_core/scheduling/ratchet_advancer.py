"""AUTO #2 Phase 3 — auto-advance rollout_pct along the ladder.

## Status: SHIPPED SCAFFOLDING, FLAG-GATED OFF

Ships the DECISION LOGIC + PERSISTENCE LAYER + AUTO_APPROVER
CONSUMER WIRE for auto-advancing `rollout_pct` per niche when the
ratchet advancement signal (`ratchet_advancement.check_...`) says
combined_ready.

  * Phase 1 (already live, `39320e56`): log ratchet signal per pass
  * Phase 2 (flag flip only): outcome-readiness contributes to
    combined_ready via `GENLAB_OUTCOME_READINESS_RATCHET_ENABLED`
  * Phase 3 (THIS COMMIT, flag off by default): auto-advance
    rollout_pct along the ladder when combined_ready holds AND
    cooldown has elapsed AND current_pct < 1.0

## Why flag off

The blast radius of a bad auto-advance is catastrophic:

  * All 5 niches could ramp from 0.1 → 1.0 based on stale reward data
  * Auto-approving 100% of blueprints for a niche where the gate
    is misaligned floods feeds with bad content in a single day
  * Reversing takes an operator YAML edit + deploy + wait for cache
    to expire

So this ship is INFRASTRUCTURE ONLY. Operator flips
`GENLAB_AUTO_ADVANCE_ROLLOUT_ENABLED=1` after Phase 2 accumulates
1-2 weeks of clean signal.

## The ladder

Standard AUTO #2 rollout ladder from CLAUDE.md:

    0.1 -> 0.25 -> 0.5 -> 1.0

Never skips steps. If current_pct is 0.1 and the signal is strong,
one advance takes it to 0.25 — not 1.0. Each step needs its own
cooldown period + signal re-verification.

## Cooldown

Default 7 days between advances per niche. Rationale: reward_48h
takes 48h to materialize + 24h for outcome_readiness to reflect
new data + a buffer. Faster ratcheting risks compound errors.

## State persistence

`/opt/genlab/.runtime/ratchet_state.json` — same directory pattern
as the retro-credit state file (rule #15 pins ownership to
genlab:genlab, not root). Shape:

    {
      "version": 1,
      "niches": {
        "ai_creators": {
          "current_pct": 0.25,
          "last_advanced_at": "2026-08-19T10:00:00Z",
          "history": [
            {
              "at": "2026-08-12T10:00:00Z",
              "from": 0.1,
              "to": 0.25,
              "reason": "outcome_ready samples=45 rate=0.85"
            }
          ]
        }
      }
    }

## Load_policy override integration

`auto_approver.load_policy` reads the YAML `rollout_pct` as
baseline. Then checks `ratchet_state.json` for a MONOTONICALLY-
HIGHER override:

  * State pct >= YAML pct -> use state pct (advanced value)
  * State pct <  YAML pct -> use YAML pct (operator lowered it,
    respect that — never let auto-advance override a manual pause)
  * State file missing -> use YAML pct
  * State file corrupt -> use YAML pct + WARN log

Rule: auto-advance can only bump UP, never down. Operator manual
YAML edit is the authoritative kill switch.

## Fail-open

Every failure path returns the YAML value + no advance decision:

  * State file corrupt / missing / permission denied
  * Ratchet signal query error
  * Time parse error on last_advanced_at
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

_STATE_FILE: Final[Path] = Path("/opt/genlab/.runtime/ratchet_state.json")
_STATE_VERSION: Final[int] = 1
_LADDER: Final[tuple[float, ...]] = (0.1, 0.25, 0.5, 1.0)
_COOLDOWN_DAYS: Final[int] = 7


@dataclass(frozen=True)
class AdvancementDecision:
    """Result of `check_and_advance` for one niche."""

    niche_id: str
    current_pct: float
    target_pct: float
    advanced: bool
    reason: str


def _is_enabled() -> bool:
    from genlab_core.settings import env_true

    return env_true("GENLAB_AUTO_ADVANCE_ROLLOUT_ENABLED")


def _state_path() -> Path:
    """Allow test override via `GENLAB_RATCHET_STATE_PATH`."""
    override = os.environ.get("GENLAB_RATCHET_STATE_PATH")
    if override:
        return Path(override)
    return _STATE_FILE


def _load_state() -> dict:
    """Read the state file. Returns empty {niches: {}} on any error."""
    path = _state_path()
    if not path.exists():
        return {"version": _STATE_VERSION, "niches": {}}
    try:
        raw = path.read_text()
        data = json.loads(raw)
        if not isinstance(data, dict) or "niches" not in data:
            logger.warning(
                "[ratchet_advancer] state file malformed shape (missing niches key): %s",
                path,
            )
            return {"version": _STATE_VERSION, "niches": {}}
        return data
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "[ratchet_advancer] state read failed (%s) — treating as empty",
            exc,
        )
        return {"version": _STATE_VERSION, "niches": {}}


def _save_state(data: dict) -> bool:
    """Write the state file atomically. Returns True on success."""
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(path)
        return True
    except OSError as exc:
        logger.warning(
            "[ratchet_advancer] state write failed (%s) — advance NOT persisted",
            exc,
        )
        return False


def get_state_override_for_niche(niche_id: str, *, yaml_pct: float) -> float:
    """Consumer API for `auto_approver.load_policy`.

    Returns the effective rollout_pct after considering both the YAML
    baseline and any auto-advance state override. Monotone-up
    semantics: state can only INCREASE, never decrease.

    Fail-open: any error returns yaml_pct unchanged.
    """
    try:
        state = _load_state()
        niche_state = state.get("niches", {}).get(niche_id, {})
        state_pct_raw = niche_state.get("current_pct")
        if state_pct_raw is None:
            return yaml_pct
        state_pct = float(state_pct_raw)
        # Never let state DEMOTE — operator YAML edit is authoritative
        # for pauses / lowers
        return max(state_pct, yaml_pct)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[ratchet_advancer] state override read failed for %s (%s) — using YAML",
            niche_id, exc,
        )
        return yaml_pct


def _next_ladder_step(current: float) -> float | None:
    """Return the next ladder value above current, or None if at cap."""
    for step in _LADDER:
        if step > current + 1e-9:  # float epsilon
            return step
    return None


def _cooldown_elapsed(niche_state: dict) -> tuple[bool, str]:
    """Check whether _COOLDOWN_DAYS have passed since last_advanced_at.

    Returns (elapsed, human_reason). Empty last_advanced_at means
    never-advanced -> elapsed=True.
    """
    last_at_raw = niche_state.get("last_advanced_at")
    if not last_at_raw:
        return True, "never_advanced"
    try:
        last_at = datetime.fromisoformat(str(last_at_raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        # Corrupt timestamp — treat as never-advanced so ratchet
        # doesn't stall on bad state
        return True, "corrupt_last_advanced_at"
    now = datetime.now(UTC)
    elapsed = now - last_at
    threshold = timedelta(days=_COOLDOWN_DAYS)
    if elapsed >= threshold:
        return True, f"cooldown_elapsed_{elapsed.days}d"
    remaining = (threshold - elapsed).days
    return False, f"cooldown_remaining_{remaining}d"


def check_and_advance(niche_id: str) -> AdvancementDecision:
    """Evaluate + optionally advance rollout_pct for one niche.

    Reads:
      * Current pct from state file (falls back to conservative 0.1
        when no state exists — safer than assuming 1.0)
      * Ratchet signal via `check_ratchet_advancement_signal`
      * Cooldown from state's last_advanced_at

    Advances if:
      * Flag `GENLAB_AUTO_ADVANCE_ROLLOUT_ENABLED` is on
      * Ratchet signal `combined_ready` is True
      * Cooldown elapsed
      * Current pct < 1.0 (not at cap)

    Never raises. Persists state on advance.
    """
    if not _is_enabled():
        return AdvancementDecision(
            niche_id=niche_id,
            current_pct=0.0,
            target_pct=0.0,
            advanced=False,
            reason="flag_off",
        )

    state = _load_state()
    niche_state = state.get("niches", {}).get(niche_id, {})
    current = float(niche_state.get("current_pct", 0.1))

    target = _next_ladder_step(current)
    if target is None:
        return AdvancementDecision(
            niche_id=niche_id,
            current_pct=current,
            target_pct=current,
            advanced=False,
            reason="at_ladder_cap",
        )

    elapsed, cooldown_reason = _cooldown_elapsed(niche_state)
    if not elapsed:
        return AdvancementDecision(
            niche_id=niche_id,
            current_pct=current,
            target_pct=target,
            advanced=False,
            reason=cooldown_reason,
        )

    try:
        from genlab_core.scheduling.ratchet_advancement import (
            check_ratchet_advancement_signal,
        )
        signal = check_ratchet_advancement_signal(niche_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[ratchet_advancer] signal query failed for %s (%s) — no advance",
            niche_id, exc,
        )
        return AdvancementDecision(
            niche_id=niche_id,
            current_pct=current,
            target_pct=target,
            advanced=False,
            reason="signal_query_failed",
        )

    if not signal.combined_ready:
        return AdvancementDecision(
            niche_id=niche_id,
            current_pct=current,
            target_pct=target,
            advanced=False,
            reason=(
                f"signal_not_ready cal={signal.calibration_agreement_count}/"
                f"{signal.calibration_samples} outcome={signal.outcome_good_count}/"
                f"{signal.outcome_samples}"
            ),
        )

    # Advance: persist + return
    now = datetime.now(UTC).isoformat()
    reason = (
        f"outcome={signal.outcome_good_count}/{signal.outcome_samples} "
        f"rate={signal.outcome_good_rate:.2f}"
    )
    niches = state.setdefault("niches", {})
    niche_state = niches.setdefault(niche_id, {})
    niche_state["current_pct"] = target
    niche_state["last_advanced_at"] = now
    history = niche_state.setdefault("history", [])
    history.append({"at": now, "from": current, "to": target, "reason": reason})
    state["version"] = _STATE_VERSION

    if not _save_state(state):
        return AdvancementDecision(
            niche_id=niche_id,
            current_pct=current,
            target_pct=target,
            advanced=False,
            reason="state_write_failed",
        )

    logger.warning(
        "[ratchet_advancer] ADVANCED niche=%s from=%.2f to=%.2f reason=%s",
        niche_id, current, target, reason,
    )
    return AdvancementDecision(
        niche_id=niche_id,
        current_pct=target,
        target_pct=target,
        advanced=True,
        reason=reason,
    )


__all__ = [
    "AdvancementDecision",
    "check_and_advance",
    "get_state_override_for_niche",
]
