"""Persona voice drift detector (Phase 4.D).

LLM-scores whether a published hook matches its niche's
persona.yaml voice / style / topic constraints. Sampled 1-of-N
recent publishes rather than every publish — keeps LLM cost
minimal (roadmap: "every 20th publish").

## Flow

  1. Load niche persona.yaml (voice, style_examples, topics
     to engage / avoid, reply_constraints).
  2. Compact into a short prompt block.
  3. Call Anthropic Haiku via ``AnthropicStrategistClient`` with
     caller_type='optional' so Phase 2.D budget gate can throttle.
  4. Parse LLM response: {drift_score: 0-1, reasons: [...]}.

## Fail-open

Every layer:
  * persona.yaml missing → returns None (skip, caller doesn't persist).
  * LLM budget gate active → skip.
  * LLM call fails / non-JSON → returns None.

Drift alerts fire ONLY when drift_score < threshold, so a
scoring failure never produces a false-positive alert.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)


ALERT_THRESHOLD: Final[float] = 0.6


@dataclass(frozen=True)
class DriftResult:
    """Outcome of one drift scoring pass. Fail-open: ok=False when
    no persona or LLM failure."""
    ok: bool
    drift_score: float | None = None
    reasons: list[str] = field(default_factory=list)
    persona_hash: str = ""
    llm_cost_usd: float = 0.0
    reason_code: str = ""  # only set when ok=False


def _persona_path(niche_id: str) -> Path | None:
    """Resolve the niche's persona.yaml. Uses the niche → dir map
    from pipeline.cli so the same lookup as sponsorship + writer
    modules. Fail-open to None if the file doesn't exist."""
    try:
        from genlab_core.pipeline.cli import (
            NICHE_DIR_NAMES,
            _resolve_genlab_root,
        )
    except ImportError:
        return None
    root = _resolve_genlab_root()
    dir_name = NICHE_DIR_NAMES.get(niche_id)
    if not dir_name:
        return None
    candidates = [
        Path(root) / dir_name / "config" / "persona.yaml",
        Path(root) / dir_name / "niches" / niche_id / "config" / "persona.yaml",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def load_persona(niche_id: str) -> dict[str, Any] | None:
    """Read persona.yaml as a dict. None on any failure."""
    path = _persona_path(niche_id)
    if path is None:
        return None
    try:
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        logger.warning("[persona_drift] load persona failed niche=%s: %s",
                       niche_id, exc)
        return None


def _persona_hash(persona: dict) -> str:
    """Stable hash of persona so old scores don't blur trend lines
    when the operator edits persona.yaml."""
    canonical = json.dumps(persona, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _build_prompt(persona: dict, hook: str) -> tuple[str, str]:
    """(system, user) prompt strings. Kept compact — the persona
    itself is small; the LLM's job is a single judgment."""
    voice = persona.get("voice") or {}
    style_examples = persona.get("style_examples") or []
    topics_engage = persona.get("topics_to_engage") or []
    topics_avoid = persona.get("topics_to_avoid") or []
    name = persona.get("name") or "the brand"

    system = (
        "You are a brand-voice auditor. Given a channel persona + a "
        "generated hook, judge how well the hook matches the persona's "
        "voice, style, and topic preferences.\n\n"
        "Return STRICT JSON:\n"
        "{\n"
        '  "drift_score": FLOAT in [0.0, 1.0],  # 1.0 = perfect match, 0.0 = severe drift\n'
        '  "reasons": ["short reason 1", "short reason 2", ...]  # 1-3 items\n'
        "}\n\n"
        "Rules:\n"
        "- Only judge voice/style/topic FIT — not factual accuracy.\n"
        "- Reasons must be specific (\"emoji_density too high for 'low' persona\"), "
        "not vague (\"seems off-brand\").\n"
        "- If the hook is in a topic listed in `topics_to_avoid`, drift_score <= 0.4.\n"
        "- If tone matches all voice knobs, drift_score >= 0.8.\n"
        "- Return only JSON, no preamble."
    )
    user = (
        f"CHANNEL PERSONA — {name}\n\n"
        f"voice:\n"
        f"  formality: {voice.get('formality', '?')}\n"
        f"  enthusiasm: {voice.get('enthusiasm', '?')}\n"
        f"  emoji_density: {voice.get('emoji_density', '?')}\n"
        f"  vocabulary: {voice.get('vocabulary', '?')}\n\n"
        f"style_examples:\n"
        + "\n".join(f"  - {s}" for s in style_examples[:5]) + "\n\n"
        f"topics_to_engage: {topics_engage[:6]}\n"
        f"topics_to_avoid: {topics_avoid}\n\n"
        f"HOOK TO JUDGE:\n{hook}\n"
    )
    return system, user


def _parse_response(raw: str) -> tuple[float | None, list[str]]:
    """Parse LLM output. Returns (score, reasons) or (None, []) on
    any parse failure."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("[persona_drift] JSON decode failed: %s", exc)
        return None, []
    try:
        score = float(obj.get("drift_score"))
    except (TypeError, ValueError):
        return None, []
    if not (0.0 <= score <= 1.0):
        return None, []
    reasons = obj.get("reasons") or []
    if not isinstance(reasons, list):
        reasons = []
    reasons = [str(r)[:200] for r in reasons if isinstance(r, (str, int, float))]
    return score, reasons[:5]


def compute_drift(
    hook: str, niche_id: str,
    *,
    _client=None,  # test seam
) -> DriftResult:
    """Score one hook against its niche's persona.

    Uses the shared Anthropic client with caller_type='optional' —
    Phase 2.D budget gate can throttle without breaking the runner.
    """
    if not hook:
        return DriftResult(ok=False, reason_code="empty_hook")
    persona = load_persona(niche_id)
    if not persona:
        return DriftResult(ok=False, reason_code="no_persona")

    system, user = _build_prompt(persona, hook)
    ph = _persona_hash(persona)

    try:
        if _client is not None:
            client = _client
        else:
            from genlab_core.intelligence.anthropic_client import (
                AnthropicStrategistClient,
            )
            client = AnthropicStrategistClient()
        result = client.generate_report(system, user, caller_type="optional")
    except Exception as exc:
        logger.warning(
            "[persona_drift] LLM call failed niche=%s: %s", niche_id, exc,
        )
        return DriftResult(
            ok=False, persona_hash=ph, reason_code=f"llm_failed:{type(exc).__name__}",
        )
    text = getattr(result, "text", "") or ""
    cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
    if not text:
        # Budget gate blocked or empty response
        return DriftResult(
            ok=False, persona_hash=ph, llm_cost_usd=cost,
            reason_code="llm_empty",
        )
    score, reasons = _parse_response(text)
    if score is None:
        return DriftResult(
            ok=False, persona_hash=ph, llm_cost_usd=cost,
            reason_code="llm_unparseable",
        )
    return DriftResult(
        ok=True, drift_score=score, reasons=reasons,
        persona_hash=ph, llm_cost_usd=cost,
    )
