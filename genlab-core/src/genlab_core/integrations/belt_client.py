"""Thin subprocess wrapper for the ``belt`` CLI (inference.sh).

## Why this exists

The 86 skills we installed at ``~/.claude/skills/`` all shell out to
``belt app run <app> --input <json>`` internally. GenLab pipeline
stages that want to leverage inference.sh apps (image gen, video gen,
data-viz, audio, etc) need a consistent Python wrapper so:

  1. Each caller doesn't reinvent subprocess handling
  2. Errors surface as clean Python exceptions with actionable info
  3. Timeouts / retries / cost telemetry live in one place
  4. Fail-open discipline (broken belt call NEVER breaks a pipeline)

## Design

  * Fail-open by default: any belt error returns None + logs. Pipeline
    stages that depend on the result should treat None as "app
    unavailable, fall back to legacy path."
  * Cost telemetry: parses the task_id from the JSON response and
    optionally queries ``belt task cost`` afterward for the operator
    briefing / cost dashboard.
  * Timeout: default 300s (aligns with belt's own client timeout for
    long-running image/video gen).
  * No retry by default — pipeline callers should decide retry policy
    since e.g. video gen is expensive and blind retries burn credit.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BeltResult:
    """One belt app run outcome. `output` is the ``output`` field from
    belt's JSON response — a dict whose shape matches the app's
    RunOutput schema. `task_id` lets the caller look up cost later."""
    ok: bool
    output: dict[str, Any] | None = None
    task_id: str | None = None
    error: str | None = None


def _belt_binary() -> str | None:
    """Return the absolute path to the belt CLI, or None when missing.
    Cached-cheap; the shutil call is fast."""
    return shutil.which("belt")


def run_app(
    app: str,
    input_data: dict[str, Any],
    *,
    timeout_seconds: int = 300,
    binary_override: str | None = None,
) -> BeltResult:
    """Run an inference.sh app synchronously via ``belt app run --json``.

    Args:
        app: canonical ``namespace/app-name`` string, e.g.
            ``pruna/flux-dev`` or ``anarchistsid/bt709-metadata-retag``.
        input_data: dict serialized to JSON and passed via ``--input``.
        timeout_seconds: subprocess kill timeout. Callers with long-
            running video-gen apps should raise this to 600+.
        binary_override: test seam — inject a specific belt path.

    Returns:
        BeltResult(ok=True, output=..., task_id=...) on success.
        BeltResult(ok=False, error=...) on any failure path
        (binary missing, subprocess non-zero, JSON parse fail, app-
        reported error). Never raises for pipeline safety.
    """
    binary = binary_override or _belt_binary()
    if not binary:
        return BeltResult(
            ok=False,
            error="belt binary not on PATH — install via "
            "'curl -fsSL cli.inference.sh | sh' or "
            "'brew install inference-sh/tap/belt'",
        )

    try:
        payload = json.dumps(input_data)
    except (TypeError, ValueError) as exc:
        return BeltResult(
            ok=False,
            error=f"input_data not JSON-serialisable: {exc}",
        )

    cmd = [binary, "app", "run", app, "--input", payload, "--json"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "[belt_client] %s timed out after %ds", app, timeout_seconds,
        )
        return BeltResult(
            ok=False,
            error=f"belt run timed out after {timeout_seconds}s",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[belt_client] %s subprocess raised: %s", app, exc,
        )
        return BeltResult(ok=False, error=f"subprocess error: {exc}")

    if proc.returncode != 0:
        logger.warning(
            "[belt_client] %s exit=%d stderr_snip=%r",
            app, proc.returncode, proc.stderr[-200:],
        )
        return BeltResult(
            ok=False,
            error=f"belt exit {proc.returncode}: {proc.stderr[-200:]}",
        )

    # belt --json prints a single JSON object on stdout for a completed
    # run. Some runs print progress noise before the JSON, so we take
    # the LAST JSON-looking line.
    stdout = proc.stdout.strip()
    if not stdout:
        return BeltResult(ok=False, error="belt returned empty stdout")

    parsed: dict[str, Any] | None = None
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if parsed is None:
        return BeltResult(
            ok=False,
            error=f"no JSON object in stdout; last 200 chars: "
            f"{stdout[-200:]!r}",
        )

    # belt embeds app failures in the response (status != 10 or
    # non-null error field). Surface those as ok=False.
    if parsed.get("error"):
        return BeltResult(
            ok=False,
            output=parsed.get("output"),
            task_id=parsed.get("id"),
            error=str(parsed["error"])[:500],
        )
    if parsed.get("status") not in (10, "completed", "10"):
        return BeltResult(
            ok=False,
            output=parsed.get("output"),
            task_id=parsed.get("id"),
            error=(
                f"belt status={parsed.get('status')} "
                f"status_text={parsed.get('status_text')}"
            ),
        )

    return BeltResult(
        ok=True,
        output=parsed.get("output"),
        task_id=parsed.get("id"),
    )


def balance_usd() -> float | None:
    """Belt account balance in USD, or None if it cannot be determined.

    None means "unknown" -- NOT "zero". Callers deciding severity must treat
    unknown as "assume degraded", never as "assume funded".

    Measured 2026-09-15: `belt balance --json` emits
    ``{"balance": 10581423226, "balance_dollars": 105.81423226}`` -- credits
    and dollars side by side, same 100_000_000-credits-per-dollar scale as
    ``task_cost_usd``. The CLI also prints an update-available banner to
    stdout, so the JSON must be located by line, not by parsing all of stdout.

    HOME matters: belt reads its credentials from ``$HOME/.belt`` and the
    systemd units set ``HOME=/opt/genlab``. Probing under any other HOME
    returns "not logged in", which reads exactly like an auth failure.
    """
    binary = _belt_binary()
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "balance", "--json"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[belt_client] balance probe failed: %s", exc)
        return None
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        # An error payload parses as clean JSON too. Checking for the field
        # is what distinguishes data from a well-formed failure.
        if "error" in parsed:
            logger.warning("[belt_client] balance: %s", parsed["error"])
            return None
        if "balance_dollars" in parsed:
            return float(parsed["balance_dollars"])
        if "balance" in parsed:
            return float(parsed["balance"]) / 100_000_000
    return None


def task_cost_usd(task_id: str) -> float | None:
    """Look up the charged cost for a completed belt task.

    Best-effort — returns None on any error. Not meant to gate work,
    only for logging + cost-attribution dashboards.
    """
    binary = _belt_binary()
    if not binary or not task_id:
        return None
    try:
        proc = subprocess.run(
            [binary, "task", "cost", task_id, "--json"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[belt_client] task_cost lookup failed: %s", exc)
        return None
    if proc.returncode != 0:
        return None
    # Try JSON first. NOTE: belt's --json output reports cost in
    # integer "credits" where 100_000_000 credits == $1.00 (verified
    # 2026-08-18: JSON `charged`=500000 for a task the plain text
    # reports as `$0.005`). The plain-text branch below is already
    # dollar-formatted.
    _BELT_CREDITS_PER_USD = 100_000_000
    try:
        for line in reversed(proc.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                parsed = json.loads(line)
                for key in ("charged", "total", "cost", "cost_usd"):
                    if key in parsed:
                        raw = parsed[key]
                        # Heuristic: cost_usd field is already a
                        # float; the integer credit fields need
                        # dividing. Numeric type check keeps us
                        # robust to future field-name changes.
                        if key == "cost_usd":
                            return float(raw)
                        return float(raw) / _BELT_CREDITS_PER_USD
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    # Fallback: parse "Charged: $0.005" line format
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.lower().startswith("charged:"):
            frag = line.split(":", 1)[1].strip().lstrip("$")
            try:
                return float(frag)
            except ValueError:
                return None
    return None

# Markers the CLI prints when the credential is bad, as opposed to when the
# network or the binary is the problem. Distinguishing them is the whole point:
# "session expired" is an operator action, "timed out" is a retry.
_UNAUTH_MARKERS = ("not logged in", "session expired", "unauthorized",
                   "invalid api key", "invalid token")


def auth_status() -> str:
    """``"ok"`` | ``"unauthenticated"`` | ``"unavailable"``.

    ``balance_usd()`` collapses every failure to None, so a caller cannot tell
    an expired session from a network blip and has to treat both the same. On
    2026-09-17 that mattered: the belt session expired at 12:30, every LLM write
    fell through to an unfunded OpenAI, and the only thing that noticed was the
    ANTHROPIC credit check -- which escalates on belt only because it happens to
    probe the balance. Had Anthropic been funded, belt could have been dead for
    days in silence.

    ``INFSH_API_KEY`` is worth knowing about here: with a key set, an
    unauthenticated result means a BAD KEY (a config error), and without one it
    means the interactive session has expired again (the recurring failure).
    """
    binary = _belt_binary()
    if not binary:
        return "unavailable"
    try:
        proc = subprocess.run(
            [binary, "balance", "--json"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[belt_client] auth probe failed: %s", exc)
        return "unavailable"
    blob = f"{proc.stdout}\n{proc.stderr}".lower()
    if any(m in blob for m in _UNAUTH_MARKERS):
        return "unauthenticated"
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if "balance" in parsed or "balance_dollars" in parsed:
                return "ok"
    return "unavailable"


def using_api_key() -> bool:
    """True when a non-expiring credential is supplied via the environment.

    The CLI prefers INFSH_API_KEY over its stored session -- verified by setting
    it to a bogus value, which makes an otherwise-working `belt balance` report
    "not logged in". The docs name INFERENCE_API_KEY for the SDK; the CLI does
    not read that one.
    """
    import os

    return bool(os.environ.get("INFSH_API_KEY", "").strip())
