"""NARR-01 narration enablement gate.

Two-key gate for enabling voice-over commentary in published reels:

  1. ``GENLAB_NARRATION_ENABLED`` env flag — master kill switch. When
     off, no narration attempts across any niche.
  2. ``narration.enabled: true`` in that niche's ``niche.yaml`` —
     canary allowlist. When absent or false, that niche keeps the
     legacy 2-input (source + music) audio mix.

Both must be truthy for narration to fire on a given blueprint.

Rationale — 2026-08-18 (NARR-01 plan section 1):
  * Env master is the operator's emergency stop knob (nuclear kill
    without editing every niche's YAML).
  * Per-niche YAML is the canary allowlist (opt-in per niche,
    matching how ``cross_channel_footer`` + ``persona_hint`` shipped).
  * Narration config (voice, vo_bed_duck_db, target_lufs) is
    niche-specific and belongs in ``niche.yaml`` next to
    ``music_mood`` config — a bare comma-list env var can't carry
    the tuning knobs.

Rule #19 pattern applies: any unexpected exception in the gate
returns False (fail-open to legacy behavior) with a WARN log
naming the reason — not a silent DEBUG swallow.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Final

logger = logging.getLogger(__name__)


_ROLLOUT_ENV: Final[str] = "GENLAB_NARRATION_ENABLED"
_ON_TOKENS: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def _env_flag_enabled() -> bool:
    """Master env flag check. True when the operator has flipped
    ``GENLAB_NARRATION_ENABLED`` to a truthy value."""
    val = (os.environ.get(_ROLLOUT_ENV) or "").strip().lower()
    return val in _ON_TOKENS


def _niche_yaml_enabled(niche_config: dict[str, Any] | None) -> bool:
    """Per-niche YAML check. Reads ``narration.enabled`` from the
    niche config dict. Missing key or non-dict shape → False.

    This is a canary allowlist: even with env flag on, a niche whose
    ``niche.yaml`` doesn't declare narration stays on the legacy
    2-input mix path.
    """
    if not isinstance(niche_config, dict):
        return False
    narration_cfg = niche_config.get("narration")
    if not isinstance(narration_cfg, dict):
        return False
    return bool(narration_cfg.get("enabled", False))


def is_narration_enabled_for(
    niche_id: str,
    niche_config: dict[str, Any] | None,
) -> bool:
    """True iff BOTH the env master flag AND the niche's YAML gate
    are on. Fail-open (return False) on any exception.

    Args:
        niche_id: canonical niche identifier, used in logs.
        niche_config: the niche's parsed config dict (from
            ``niche_loader.load_niche_config``).

    Returns False when either gate is off or on any error.
    """
    try:
        env_on = _env_flag_enabled()
        yaml_on = _niche_yaml_enabled(niche_config)
        return env_on and yaml_on
    except Exception as exc:  # noqa: BLE001 — fail-open with WARN
        logger.warning(
            "[narration_gate] is_narration_enabled_for(%s) raised: %s "
            "— defaulting to disabled (fail-open to legacy audio path)",
            niche_id, exc,
        )
        return False


def get_tts_rate_wpm(niche_config: dict[str, Any] | None) -> int:
    """Speaking rate (wpm) for the tier expected to synthesise this reel.

    NARR-11 (2026-08-20). The writer must project duration BEFORE the TTS
    cascade runs, so the tier is not yet known. Resolution:

    * When ``GENLAB_INFSH_TTS_ENABLED`` is on, Inworld TTS-2 is the primary
      tier and its measured rate is used.
    * Otherwise the ``default`` rate applies.

    Deliberately biased slow: under-predicting the rate produces a script that
    is too long and gets rejected late, after paying for synthesis. Over-
    predicting merely produces a slightly short script, which costs nothing.

    Never raises; falls back to 150 on any malformed config.
    """
    try:
        rates = get_narration_config(niche_config).get("tts_rates") or {}
        if not isinstance(rates, dict):
            return 150
        infsh_on = (os.environ.get("GENLAB_INFSH_TTS_ENABLED") or "").strip().lower() in _ON_TOKENS
        tier = "inworld" if infsh_on else "default"
        value = rates.get(tier, rates.get("default", 150))
        return int(value) if int(value) > 0 else 150
    except Exception:  # noqa: BLE001 — fail to the safe baseline
        return 150


def get_narration_config(niche_config: dict[str, Any] | None) -> dict[str, Any]:
    """Return the narration sub-config with sensible defaults for any
    missing key. Never raises; returns defaults on any error.

    Defaults match the plan section 3.2:
      * ``vo_bed_duck_db`` = -8  (additional bed duck under VO segments)
      * ``target_lufs``   = -14.0  (EBU R128 short-form spec)
      * ``wpm``           = 150  (baseline TTS speech rate)
      * ``tail_buffer_seconds`` = 2.0  (music-carries-out margin)
      * ``narration_vo_db``  = 0  (VO amplitude, unducked)
    """
    defaults: dict[str, Any] = {
        "enabled": False,
        "vo_bed_duck_db": -8,
        "target_lufs": -14.0,
        "wpm": 150,
        "tail_buffer_seconds": 2.0,
        "narration_vo_db": 0,
        # NARR-11 (2026-08-20): per-TTS-tier speaking rates. A blanket wpm
        # under-predicts for slower providers and wastes synthesis on scripts
        # the mix then rejects.
        #
        # inworld=141 measured 2026-08-20 from two full-chain runs on story_0:
        # predicted 16.0s at wpm=150, actual 16.98s and 16.88s -> ~6% slower.
        # Other tiers stay at 150 until measured; do not guess them.
        #
        # Follow-up task (not NARR-11): auto-fit these from the logged A4
        # (predicted, actual) triples instead of hand-measuring.
        "tts_rates": {"inworld": 141, "default": 150},
        # One regeneration at this fraction of the original budget when the
        # script overruns, before degrading (NARR-11 ruling).
        "retry_budget_factor": 0.85,
        # NARR-12 (2026-08-20): safety margin on the fit check. A script whose
        # PROJECTED duration lands within this fraction of the budget is sent
        # to the retry path rather than passed.
        #
        # Why: projection is a model, not a measurement. Inworld ran 6.6%
        # slower than predicted on the round-3 script (13.62s projected,
        # 14.52s actual). A script projecting at 99% of budget is therefore
        # odds-on to overrun in reality, and the failure lands late — after
        # synthesis is paid for, at the mix-time guard, costing the reel its
        # narration. Retrying at 85% costs one LLM call.
        #
        # NOTE: this margin has TWO implementers — the validator's check and
        # the prompt's HARD word cap. Both read this value. Changing one alone
        # is the NARR-11 defect.
        "fit_margin": 0.05,
    }
    if not isinstance(niche_config, dict):
        return defaults
    narration_cfg = niche_config.get("narration")
    if not isinstance(narration_cfg, dict):
        return defaults
    merged = dict(defaults)
    for key, val in narration_cfg.items():
        if key in defaults:
            merged[key] = val
    return merged


__all__ = [
    "is_narration_enabled_for",
    "get_narration_config",
    "_ROLLOUT_ENV",  # exported for flag_audit pin test
]
