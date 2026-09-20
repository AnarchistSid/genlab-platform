"""Shared Anthropic → OpenAI fallback machinery for direct-call sites.

The 2026-07-18 → 2026-07-21 outage was caused by Anthropic API credit
exhaustion. `AnthropicLLMClient` (writer path) got its own fallback in
commit `f1554193`, but 7 other Anthropic-direct call sites remained
vulnerable:

  * `scheduling/auto_approval_gate.py` — LLM judge (borderline decisions)
  * `engagement/persona_engine.py` — outbound + inbound reply generation
  * `writing/llm_hook_generator.py` — hook generation (6 sub-calls)
  * `writing/caption_segments.py` — caption writing
  * `learning/scratchpad.py` — weekly Opus reflection
  * `learning/post_rca.py` — post-mortem RCA
  * `learning/rationale_classifier.py` — rejection auto-classify

Each has slightly different shapes (different models, prompt caching,
temperatures, structured outputs). Rather than force a full refactor
to `AnthropicLLMClient`, this module provides SHARED helpers each
site can wrap around its existing Anthropic call in 3-5 lines:

    from genlab_core.llm.fallback import (
        should_fallback, call_openai_fallback, cb_record_exhaustion,
        cb_record_success, cb_is_open,
    )

    try:
        if cb_is_open():
            raise _CircuitOpen  # goes straight to fallback
        response = client.messages.create(...)
        cb_record_success()
        return response.content[0].text
    except Exception as e:
        if should_fallback(e):
            cb_record_exhaustion()
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if api_key:
                return call_openai_fallback(system, user, max_tokens, temp, api_key)
        raise

CRITICAL: circuit breaker state is MODULE-LEVEL so ALL 8 sites share
ONE breaker. Otherwise 8 sites × 3 exhaustions each = 24 total
exhaustion calls before any site stops trying. Shared state means
the first 3 exhaustions (across ANY sites) opens the breaker for
everyone.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Final

logger = logging.getLogger(__name__)


# ── Shared circuit breaker state (module-level, all sites benefit) ──

_ANTHROPIC_EXHAUSTION_COUNT: int = 0
_ANTHROPIC_CB_OPEN_UNTIL: float = 0.0


def _cb_threshold() -> int:
    """Consecutive exhaustions before the shared breaker opens.

    FIX-LLMFB §B says "once tripped", which reads as 1. Kept at 3 by default,
    deliberately and recorded rather than silently substituted: this breaker is
    MODULE-LEVEL and shared by eight Anthropic call sites, so a threshold of 1
    lets any single transient misclassification divert every site at once. The
    exhaustion classifier is now typed on ``error.type == "billing_error"``
    (ad6b4d18) and no longer fires on 429/5xx, so 3 consecutive genuine billing
    errors is a strong signal and costs at most two extra failed calls.
    Configurable for an operator who wants the spec's literal behaviour.
    """
    try:
        return max(1, int(os.environ.get("GENLAB_LLM_FALLBACK_CB_THRESHOLD", "3")))
    except ValueError:
        return 3


def _cb_cooldown_s() -> int:
    """How long to serve from the fallback chain before probing Anthropic.

    FIX-LLMFB §B: default 60 minutes, configurable. Previously a hardcoded 600s
    (10 min). Ten minutes against a genuinely exhausted key means six pointless
    primary attempts an hour, each one a failed request the writer waits on.
    """
    try:
        return max(
            60,
            int(os.environ.get("GENLAB_LLM_FALLBACK_COOLDOWN_MINUTES", "60")) * 60,
        )
    except ValueError:
        return 3600


class CircuitOpen(Exception):
    """Sentinel raised internally when caller checks `cb_is_open()`
    and wants to skip Anthropic. Never raised by the helpers below;
    it's here as a documented pattern for callers who want the "if
    breaker open, immediately fallback" flow."""

    pass


# ── Exhaustion classifier ──

_ANTHROPIC_EXHAUSTION_MARKERS = (
    "credit balance is too low",
    "credit balance too low",
    "insufficient credits",
    "insufficient_quota",
)


def _error_type_of(exc: Exception) -> str:
    """Anthropic's own ``error.type`` discriminator, or "" when absent.

    The SDK models this as ``anthropic.types.shared.ErrorType``, a Literal
    union that includes ``"billing_error"`` alongside ``"rate_limit_error"``,
    ``"overloaded_error"`` and the rest. It is the authoritative signal and it
    does not depend on prose.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("type") or "")
    return ""


def should_fallback(exc: Exception) -> bool:
    """True only when Anthropic is EXHAUSTED — not merely unhappy.

    Measured 2026-09-12 against real SDK error objects; the previous
    implementation got two of six cases wrong, both consequentially:

    * **A typed ``billing_error`` with a terse message returned False.**
      Detection was purely substring-based on "credit balance is too low", so
      the shape the SDK actually models — ``{"type": "billing_error",
      "message": "Billing error"}`` — did not match and NO fallback fired. The
      OpenAI fallback has never once fired in production (no OpenAI model
      appears in ``pipeline_run_costs.by_model`` since 2026-06-14), and during
      the 09-06→09-09 outage ``by_model`` carried only ``{"tts": …}`` — no
      Claude, no OpenAI. This is a sufficient explanation for that window.
    * **A 429 returned True.** Rate limiting is transient and retryable on the
      primary; failing over moves traffic off Anthropic for a blip, and on the
      day the primary is genuinely down that traffic stampedes the secondary.

    So: the typed discriminator first, prose markers as a compatibility net for
    older API shapes, and transient classes explicitly excluded.

    Fails over on:
      * ``error.type == "billing_error"`` — authoritative
      * ``credit balance is too low`` / ``insufficient credits`` /
        ``insufficient_quota`` — prose variants, kept for older responses

    Does NOT fail over on (retry on the primary instead):
      * 429 ``rate_limit_error`` · 529 ``overloaded_error`` · 5xx · timeouts
      * 401 ``authentication_error`` — an operator problem; see
        ``should_fallback_on_auth`` for the opt-in persistent-auth case
      * connection errors — the secondary shares the same network
    """
    if _error_type_of(exc) == "billing_error":
        return True
    msg = str(exc).lower()
    return any(marker in msg for marker in _ANTHROPIC_EXHAUSTION_MARKERS)


def should_fallback_on_auth(exc: Exception) -> bool:
    """True for an authentication failure, when the caller has already retried.

    A revoked or expired key is functionally exhaustion — the primary will not
    serve again until an operator acts. Separated from
    :func:`should_fallback` so the caller decides, because a FIRST auth failure
    can be a transient control-plane blip and failing over on it would hide a
    key rotation. Gated by ``GENLAB_LLM_FALLBACK_ON_AUTH`` (default on).
    """
    if os.environ.get("GENLAB_LLM_FALLBACK_ON_AUTH", "1").strip() == "0":
        return False
    return (
        _error_type_of(exc) == "authentication_error" or type(exc).__name__ == "AuthenticationError"
    )


# ── Circuit breaker API ──


def cb_is_open() -> bool:
    """True if the shared Anthropic circuit breaker is currently open.
    Callers should skip the Anthropic attempt and go straight to
    OpenAI when this returns True."""
    return time.time() < _ANTHROPIC_CB_OPEN_UNTIL


def cb_record_exhaustion() -> None:
    """Increment the shared exhaustion counter. Open the breaker if
    we hit the threshold. Idempotent within a single process."""
    global _ANTHROPIC_EXHAUSTION_COUNT, _ANTHROPIC_CB_OPEN_UNTIL
    _ANTHROPIC_EXHAUSTION_COUNT += 1
    if _ANTHROPIC_EXHAUSTION_COUNT >= _cb_threshold():
        cooldown = _cb_cooldown_s()
        _ANTHROPIC_CB_OPEN_UNTIL = time.time() + cooldown
        _notify_failover(
            "circuit-breaker",
            "ANTHROPIC CIRCUIT OPEN",
            f"{_ANTHROPIC_EXHAUSTION_COUNT} consecutive exhaustion errors — "
            f"serving from the fallback chain for {cooldown // 60} min, then "
            f"one probe call returns traffic if the primary recovers",
        )


def cb_record_success() -> None:
    """Reset the counter + close the breaker on any successful
    Anthropic call from any site."""
    global _ANTHROPIC_EXHAUSTION_COUNT, _ANTHROPIC_CB_OPEN_UNTIL
    if _ANTHROPIC_EXHAUSTION_COUNT > 0 or _ANTHROPIC_CB_OPEN_UNTIL > 0:
        # §C.6 probe-back: once the cooldown lapses, cb_is_open() returns False
        # and the NEXT call goes to Anthropic — that call IS the single probe.
        # Its success lands here and returns all traffic to the primary.
        _notify_failover(
            "circuit-breaker",
            "ANTHROPIC RECOVERED",
            f"probe call succeeded after {_ANTHROPIC_EXHAUSTION_COUNT} "
            f"exhaustion(s) — returning traffic to the primary",
        )
    _ANTHROPIC_EXHAUSTION_COUNT = 0
    _ANTHROPIC_CB_OPEN_UNTIL = 0.0


def fallback_enabled() -> bool:
    """Env-flag opt-in (default ON). Operator can set
    ``GENLAB_LLM_FALLBACK_ENABLED=0`` to disable when OpenAI budget
    is also dry, so we don't cascade a second outage."""
    return os.environ.get("GENLAB_LLM_FALLBACK_ENABLED", "1").strip() != "0"


# ── OpenAI call helper ──


def call_openai_fallback(
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    api_key: str,
    *,
    model: str = "gpt-4o-mini",
    json_mode: bool = False,
) -> str:
    """Fallback LLM call. Tries BELT (inference.sh Claude Haiku) first, then OpenAI.

    2026-09-17: the belt tier had been defined in this module since 2026-09-12 and
    had **zero consumers** -- all ten call sites imported `call_openai_fallback`
    and went straight to OpenAI. When the OpenAI balance hit zero the auto-approver
    threw `RateLimitError` on every borderline blueprint (137 tracebacks in 24h),
    approved nothing for days, so nothing got a `scheduled_for`, so the publisher
    found nothing due and all five channels stopped.

    Routing belt-first HERE rather than editing ten call sites is deliberate: the
    tier was missed once per site already, and a shared helper cannot be missed
    per site. The historical name is kept so no caller has to change; the
    docstring carries the truth.

    Belt is the same model as the primary at 0% markup on a separate credit pool,
    so failing over changes neither cost nor output distribution -- only which
    balance is consumed. OpenAI's gpt-4o-mini is a different model and a different
    voice, which is why it is tier 2.
    """
    if belt_fallback_enabled():
        try:
            return call_belt_haiku_fallback(
                system,
                user,
                max_tokens,
                temperature,
                json_mode=json_mode,
            )
        except Exception as exc:  # noqa: BLE001 — belt down must not lose the call
            # WARNING, not debug: a silently-dead tier is what produced this bug.
            logger.warning(
                "[llm-fallback] belt tier failed (%s: %s) — falling through to OpenAI",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
    if not api_key:
        raise RuntimeError("LLM fallback exhausted: belt unavailable and no OPENAI_API_KEY set")
    return _call_openai_direct(
        system, user, max_tokens, temperature, api_key, model=model, json_mode=json_mode
    )


def _call_openai_direct(
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    api_key: str,
    *,
    model: str = "gpt-4o-mini",
    json_mode: bool = False,
) -> str:
    """Tier 2. Call OpenAI directly. Returns the response text as a string.

    Mirrors shape of `genlab_core.llm.router._call_openai`. Reuses the
    cost accumulator so fallback spend is visible on the same ledger
    as primary calls.

    ``json_mode=True`` requests OpenAI's structured JSON output
    (matches `router.py:_call_openai`'s json_mode param) — use for
    sites that need parseable JSON (rationale_classifier, judge).
    """
    import openai  # noqa: PLC0415 — lazy import; tests without openai still import

    client = openai.OpenAI(api_key=api_key)
    # Skip system role when empty — some sites (e.g., caption_segments)
    # only send a user prompt. OpenAI accepts an empty system message
    # but including it as an empty string is wasteful/confusing.
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
    try:
        from genlab_core.intelligence.cost_accumulator import record_openai_usage

        record_openai_usage(model, response)
    except Exception:  # noqa: BLE001 — cost tracking never blocks
        pass
    return response.choices[0].message.content


# ── Belt (inference.sh) Claude Haiku fallback ──

_BELT_MODEL: Final[str] = "claude-haiku-4-5"
_BELT_APP: Final[str] = "anthropic/claude-haiku-4-5"


def belt_fallback_enabled() -> bool:
    """Belt is tier 1 of the fallback chain. ``GENLAB_LLM_FALLBACK_BELT=0``
    disables it and leaves OpenAI as the only tier."""
    return os.environ.get("GENLAB_LLM_FALLBACK_BELT", "1").strip() != "0"


def extract_json(text: str) -> str:
    """Return the first complete JSON value embedded in ``text``.

    OpenAI has a real structured-output mode (``response_format``) that
    guarantees the body is nothing but JSON. Anthropic has no equivalent, so the
    belt tier returns the object wrapped in whatever prose the model felt like
    ("Here is my verdict:\n{...}\nLet me know..."). Callers that did
    ``json.loads(raw)`` against OpenAI therefore started throwing the moment
    belt became tier 1:

      [gate] LLM judge failed (using rule-based) — reason=unknown:
      Extra data: line 5 column 1 (char 130)

    which is a SILENT quality regression, not an outage -- the gate caught it
    and fell back to rule-based scoring, so the pass still reported errors=0
    while discarding the judge's verdict on half the blueprints it examined.

    Scans for a balanced object/array while respecting string literals and
    escapes, so a brace inside a reason string cannot terminate it early.
    Returns the original text unchanged when nothing parses -- the caller's own
    error is more informative than one invented here.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    try:
        json.loads(stripped)
        return stripped
    except ValueError:
        pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        if start == -1:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(stripped)):
            ch = stripped[i]
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    candidate = stripped[start : i + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except ValueError:
                        break
    return text


def call_belt_haiku_fallback(
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    *,
    model: str = _BELT_MODEL,
    json_mode: bool = False,
    timeout_s: int = 180,
) -> str:
    """Call Claude Haiku through the inference.sh belt. Returns response text.

    Why this tier exists and sits ABOVE OpenAI: it is the SAME MODEL the primary
    uses, at 0% markup (measured 2026-09-12: belt lists $1.00/M input and
    $5.00/M output, identical to Anthropic), drawing on a SEPARATE credit pool.
    Failing over here changes neither cost nor output distribution — only which
    balance is consumed. OpenAI's gpt-4o-mini is a different model and a
    different voice, so it is the second tier, not the first.

    Concurrency measured 2026-09-12 by ramping 1→32 parallel calls: **87/87
    completed, no throttle at 32**, latency flat at ~2s median. The writer's
    burst is ~20 calls per fire and the five niches are staggered 02:30–06:00Z,
    so the cap is not a constraint even if every niche needed this at once.
    (Contrast ElevenLabs, capped at 2 — T-40.)

    T-38: a call is a success ONLY when ``status_text == completed``. The belt
    CLI exits 0 on a failed task, so the exit code proves nothing.
    T-39: cost is read only alongside that status.
    """
    import json as _json
    import subprocess  # noqa: PLC0415 — lazy; keeps import cost off module load

    # Use the DEFAULT `run` function, not `openai`. Measured 2026-09-12: the
    # `openai` function returned `{"output": {"response": ""}}` — status
    # "completed", zero content — on every shape tried, including one that had
    # returned a real `choices` payload two hours earlier. `run` returns content
    # reliably. Whatever changed on the belt side, a fallback tier cannot rest
    # on the surface that silently empties.
    # Anthropic requires at least one message; OpenAI does not. Two call sites
    # (hook_classifier:187 and :218) put the ENTIRE prompt in `system` and pass
    # user="" -- fine against OpenAI, but belt relayed it as an empty messages
    # array and got back
    #   400 invalid_request_error: messages: at least one message is required
    # on every call. Promote the system prompt to the user turn in that case so
    # the belt tier is usable from every call site rather than only the ones
    # that happen to split their prompt the way Anthropic expects.
    sys_text, user_text = system, user
    if not user_text.strip():
        if not sys_text.strip():
            raise ValueError("belt fallback needs a system or user prompt; both empty")
        sys_text, user_text = "", sys_text

    payload_in: dict[str, Any] = {
        "text": user_text,
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if sys_text:
        payload_in["system_prompt"] = sys_text
    # NOT sent: ``response_format: {"type": "json_object"}``. That is an
    # OpenAI-ism, and belt relays this app to Anthropic, which rejects it:
    #
    #   belt task status='failed' err=response_format json_object is not
    #   supported by Anthropic; use json_schema with a schema
    #
    # The request failed before producing any response, so every json_mode
    # caller fell straight through to OpenAI — and when the OpenAI balance
    # hit zero (2026-09-20) the strategist failed 100% of niches and exited
    # 1 every Sunday, with belt funded and working the whole time.
    #
    # The response-side half of the contract was already correct below
    # (``extract_json`` unwraps fenced or prose JSON), written by someone
    # who knew Anthropic has no structured-output mode. This line
    # contradicted it. json_schema is not a substitute: callers pass a
    # boolean, not a schema, and inventing one per call site is exactly
    # the per-site duplication ``call_openai_fallback`` exists to avoid.

    proc = subprocess.run(
        [
            "belt",
            "app",
            "run",
            _BELT_APP,
            "--no-input",
            "--json",
            "--input",
            _json.dumps(payload_in),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    try:
        payload = _json.loads(proc.stdout or "{}")
    except ValueError as exc:
        raise RuntimeError(
            f"belt returned unparseable output (rc={proc.returncode}): "
            f"{(proc.stdout or proc.stderr or '')[:200]}"
        ) from exc

    # T-38: never gate on proc.returncode — the belt CLI exits 0 on a failed task.
    status = payload.get("status_text") or ""
    if status != "completed":
        raise RuntimeError(f"belt task status={status!r} err={str(payload.get('error'))[:200]}")

    # T-57: "completed" is NOT success. It says the task finished, not that it
    # produced anything. Measured the same day: the `openai` function returned
    # completed-with-empty-response on 100% of calls, and a concurrency ramp
    # that checked only status scored 87/87 while very possibly serving nothing.
    # A fallback that returns "" would hand the writer an empty hook and look
    # like it worked.
    out = payload.get("output") or {}
    text = str(out.get("response") or "").strip()
    if json_mode and text:
        # Anthropic has no structured-output mode; unwrap the prose.
        text = extract_json(text)
    if not text:
        raise RuntimeError(
            f"belt returned status=completed with EMPTY content "
            f"(output keys: {sorted(out) if isinstance(out, dict) else type(out).__name__})"
        )

    # T-39: cost recorded only alongside a completed status AND real content.
    try:
        from genlab_core.intelligence.cost_accumulator import record_provider_usage

        record_provider_usage(provider="belt", model=model, payload=payload)
    except Exception:  # noqa: BLE001 — cost tracking never blocks a call
        pass
    return text


def _notify_failover(site_label: str, event: str, detail: str) -> None:
    """Announce a failover or a recovery. Best-effort, never blocks a call.

    This is the notice that did not exist for four days in September: the writer
    stopped calling the LLM entirely and nothing said so. A log line alone was
    not enough — the journal retains ~14h (T-44), so an outage older than that
    leaves no trace at all.
    """
    logger.warning("[llm-fallback][%s] %s: %s", site_label, event, detail)
    try:
        from genlab_core.monitoring.notify import send_outage_alert

        send_outage_alert(f"[llm-fallback] {event} ({site_label}): {detail}")
    except Exception:  # noqa: BLE001 — alerting never blocks
        logger.debug("[llm-fallback] alert sink unavailable", exc_info=True)


def call_fallback_chain(
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    *,
    json_mode: bool = False,
    site_label: str = "unknown",
    original_exc: Exception | None = None,
) -> str:
    """Try each fallback tier in order: belt Claude Haiku, then OpenAI.

    A chain rather than a swap, so there is never a window with no fallback.
    If EVERY tier fails, the ORIGINAL Anthropic exception is re-raised — the
    primary's failure is the diagnostic that matters, and surfacing the last
    tier's error instead would point the operator at the wrong system.
    """
    errors: list[str] = []
    last_exc: Exception | None = None

    if belt_fallback_enabled():
        try:
            text = call_belt_haiku_fallback(
                system, user, max_tokens, temperature, json_mode=json_mode
            )
            _notify_failover(
                site_label,
                "SERVED BY BELT",
                f"Anthropic unavailable; same model on the belt pool "
                f"({type(original_exc).__name__ if original_exc else 'n/a'})",
            )
            return text
        except Exception as exc:  # noqa: BLE001 — try the next tier
            last_exc = exc
            errors.append(f"belt: {type(exc).__name__}: {str(exc)[:150]}")
            logger.warning("[llm-fallback][%s] belt tier failed: %s", site_label, errors[-1])

    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_key:
        try:
            text = call_openai_fallback(
                system, user, max_tokens, temperature, openai_key, json_mode=json_mode
            )
            _notify_failover(site_label, "SERVED BY OPENAI", "belt tier unavailable or disabled")
            return text
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            errors.append(f"openai: {type(exc).__name__}: {str(exc)[:150]}")
            logger.warning("[llm-fallback][%s] openai tier failed: %s", site_label, errors[-1])
    else:
        errors.append("openai: OPENAI_API_KEY not set")

    _notify_failover(site_label, "ALL FALLBACK TIERS FAILED", "; ".join(errors))
    if original_exc is not None:
        # Surface the ORIGINAL Anthropic error — the primary's failure is the
        # diagnostic that matters — but chain the last tier's exception as
        # __cause__ so the trail is not lost. Raising the last tier's error
        # instead would point the operator at the wrong system entirely.
        if last_exc is not None:
            raise original_exc from last_exc
        raise original_exc
    raise RuntimeError("all fallback tiers failed: " + "; ".join(errors))


# ── Convenience wrapper for the common pattern ──


def with_openai_fallback(
    anthropic_call: Any,  # callable returning str
    *,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool = False,
    site_label: str = "unknown",
) -> str:
    """Run an Anthropic call and transparently fall back to OpenAI on
    exhaustion. Callers pass a zero-arg callable that performs the
    Anthropic call and returns a str. Handles the circuit breaker +
    logging boilerplate.

    Use this when you don't need site-specific Anthropic customisation
    (prompt caching, model routing, etc.). For sites with rich
    Anthropic-specific setup, use the primitives above directly.

    Returns str. On unrecoverable failure, re-raises the ORIGINAL
    Anthropic exception so downstream error classifiers behave as
    before.
    """
    # CB-open fast path: skip Anthropic entirely and serve from the chain.
    # This is the cooldown FIX-LLMFB §B asks for — while the breaker is open we
    # do not re-hit an exhausted primary on every request.
    if fallback_enabled() and cb_is_open():
        try:
            logger.info(
                "[llm-fallback][%s] circuit open — serving from fallback chain "
                "without trying Anthropic",
                site_label,
            )
            return call_fallback_chain(
                system,
                user,
                max_tokens,
                temperature,
                json_mode=json_mode,
                site_label=site_label,
            )
        except Exception:  # noqa: BLE001 — no tier available; probe Anthropic anyway
            logger.warning(
                "[llm-fallback][%s] circuit open but no fallback tier served — probing Anthropic",
                site_label,
            )

    try:
        result = anthropic_call()
    except Exception as anthropic_exc:
        if not (fallback_enabled() and should_fallback(anthropic_exc)):
            raise
        cb_record_exhaustion()
        # FIX-LLMFB §B: a CHAIN, not a swap — belt Claude Haiku first (same
        # model, 0% markup, separate credit pool), then OpenAI. call_fallback_chain
        # re-raises the ORIGINAL Anthropic exception if every tier fails, because
        # the primary's failure is the diagnostic that matters; surfacing the last
        # tier's error would point the operator at the wrong system.
        return call_fallback_chain(
            system,
            user,
            max_tokens,
            temperature,
            json_mode=json_mode,
            site_label=site_label,
            original_exc=anthropic_exc,
        )

    cb_record_success()
    return result


__all__ = [
    "should_fallback",
    "call_openai_fallback",
    "cb_is_open",
    "cb_record_exhaustion",
    "cb_record_success",
    "fallback_enabled",
    "with_openai_fallback",
    "CircuitOpen",
]


# Back-compat alias; `extract_json` is the public name.
_extract_json = extract_json
