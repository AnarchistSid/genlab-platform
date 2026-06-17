"""Anthropic Batch API client — 50% cost saving on non-realtime calls.

U-02 (2026-06-17): the Anthropic Batch API discounts input + output
tokens by 50% in exchange for a 24-hour SLA (usually completes in
minutes-to-an-hour for typical batches). Per the audit doc, the
``1 reel/channel/day rendered before the 06:30 UTC window`` is the
ideal batch workload — writing + hook generation runs once per niche
per day, the operator's daily approval window is 06:30 UTC, so even
a worst-case 24h SLA is comfortable headroom.

This module ships the *infrastructure* — a `BatchClient` that wraps
the Anthropic SDK's `messages.batches.{create, retrieve, results}`
surface — so individual pipelines can opt-in incrementally. The first
caller migrations land in follow-up PRs (the writing pipeline +
LLM hook generator are the obvious candidates).

Design choices
==============
* **Polling, not webhooks** — Anthropic doesn't ship a webhook for
  batch completion. The caller decides between blocking-poll (`run`)
  vs fire-and-forget (`submit` + `wait` later). The blocking-poll
  path is the simplest migration target: pipeline calls
  `BatchClient.run(items)` and gets back a dict. Wall-clock is
  whatever the batch takes (5min-24h) — that's the cost-saving
  trade-off the caller has chosen by opting in.

* **`custom_id` is the caller's choice** — we don't generate IDs.
  This is how the caller correlates submitted requests with results
  (the batch result endpoint streams JSONL where each line has the
  custom_id you submitted). For pipeline use, blueprint_id or
  story_id is the natural choice.

* **Failures surface as exceptions on the per-item path** — if the
  batch itself fails (auth, rate limit, malformed), `run()` raises.
  If individual items inside the batch fail (one prompt 400s while
  others succeed), the failed item appears in the result dict as a
  value of type `BatchItemError` so the caller can decide whether
  to retry that one item or skip it. This matches the "batch
  succeeds but some items error" reality of the Anthropic API.

* **Cost recording** — every successful per-item result records its
  `response.usage` via `record_anthropic_usage` so the existing
  R-27 cost-tracking surface continues to work transparently. The
  50% discount is automatic at the rate-card level (Anthropic
  charges the discounted rate; we just record the token count).

Usage::

    from genlab_core.llm.batch import BatchClient, BatchItem

    client = BatchClient(model="claude-haiku-4-5-20251001")
    items = [
        BatchItem(
            custom_id=f"hook-{bp['id']}",
            system="You write viral gaming hooks.",
            user=f"Write 5 hook variants for: {bp['title']}",
            max_tokens=400,
            temperature=0.9,
        )
        for bp in blueprints
    ]
    results = client.run(items, poll_interval_s=30)
    for bp in blueprints:
        text = results[f"hook-{bp['id']}"]
        # text is either str (success) or BatchItemError (per-item failure)

Why not async/await
===================
The Gen Lab pipeline is sync (psycopg3 sync, FFmpeg blocking, etc.).
A blocking poll is the simplest fit. Anthropic's SDK has async
support — when we move pieces to async (e.g. dashboard server),
add an `aiorun()` alongside, mirror the surface.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ── Public types ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class BatchItem:
    """One request inside a batch — mirrors the args to ``messages.create``.

    The ``custom_id`` is the caller's correlator (we don't generate
    UUIDs because the caller usually already has a natural key like
    blueprint_id). Must be unique within the batch.
    """

    custom_id: str
    system: str
    user: str
    max_tokens: int = 1024
    temperature: float = 0.7


@dataclass(frozen=True)
class BatchItemError:
    """Returned in the result dict when one item inside the batch failed
    (vs the whole batch failing, which raises).

    The caller decides whether to retry the single item via the
    realtime path, skip it, or surface to operator.
    """

    custom_id: str
    error_type: str
    message: str


# ── Polling configuration ────────────────────────────────────────────

# Conservative defaults; callers can override per-call.
_DEFAULT_POLL_INTERVAL_S = 30.0
# Hard ceiling — Anthropic's SLA is 24h. We give 26h headroom for
# clock drift / SDK retries / poll jitter, then give up so a wedged
# batch can't block a pipeline indefinitely. Caller's exception
# handler decides recovery (retry next run, fall back to realtime).
_DEFAULT_MAX_WAIT_S = 26 * 60 * 60

# Anthropic batch API terminal states (per docs):
#   "in_progress"  — still running, keep polling
#   "canceling"    — being cancelled, will land in "ended" soon
#   "ended"        — completed (per-item success or per-item error)
_TERMINAL_STATE = "ended"


# ── Client ───────────────────────────────────────────────────────────


class BatchClient:
    """Thin wrapper around ``anthropic.Anthropic().messages.batches``.

    Stateless across calls — each ``run()`` submits a fresh batch.
    Splits "I want this NOW" callers (use ``AnthropicLLMClient``) from
    "I'm OK waiting for a 50% discount" callers (use this).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ):
        import os

        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = model or "claude-haiku-4-5-20251001"
        self._client: Any = None

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    def _ensure_client(self) -> None:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._api_key)

    # ── Submit + wait (single round trip) ─────────────────────────────

    def run(
        self,
        items: list[BatchItem],
        *,
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
        max_wait_s: float = _DEFAULT_MAX_WAIT_S,
    ) -> dict[str, str | BatchItemError]:
        """Submit a batch, block until results, return dict by custom_id.

        Raises on:
          * empty ``items`` (catches caller logic bugs — submitting an
            empty batch wastes a round trip and Anthropic 400s anyway)
          * duplicate custom_ids (the result dict would lose data)
          * batch never reaches terminal state within ``max_wait_s``
          * authentication / network / SDK errors during submit or poll

        Returns dict[custom_id, str | BatchItemError] — one entry per
        submitted item.
        """
        if not items:
            raise ValueError("BatchClient.run requires at least one item")
        custom_ids = [it.custom_id for it in items]
        if len(set(custom_ids)) != len(custom_ids):
            raise ValueError(
                "BatchClient.run requires unique custom_ids — duplicates "
                f"would conflict in the result dict (got {len(custom_ids)} "
                f"items, {len(set(custom_ids))} unique)"
            )

        batch_id = self.submit(items)
        logger.info(
            "[batch_client] submitted batch=%s items=%d model=%s",
            batch_id,
            len(items),
            self._model,
        )
        return self.wait_and_collect(
            batch_id,
            poll_interval_s=poll_interval_s,
            max_wait_s=max_wait_s,
        )

    # ── Submit (fire-and-forget) ──────────────────────────────────────

    def submit(self, items: list[BatchItem]) -> str:
        """Submit a batch and return the batch_id immediately.

        Callers who want fire-and-forget (e.g. submit-at-end-of-pipeline-day,
        collect-at-start-of-next-day) use this + ``wait_and_collect`` later.
        Persist the returned batch_id yourself.
        """
        self._ensure_client()
        requests = [
            {
                "custom_id": it.custom_id,
                "params": {
                    "model": self._model,
                    "max_tokens": it.max_tokens,
                    "temperature": it.temperature,
                    "system": it.system,
                    "messages": [{"role": "user", "content": it.user}],
                },
            }
            for it in items
        ]
        batch = self._client.messages.batches.create(requests=requests)
        return batch.id

    # ── Wait + collect ────────────────────────────────────────────────

    def wait_and_collect(
        self,
        batch_id: str,
        *,
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
        max_wait_s: float = _DEFAULT_MAX_WAIT_S,
    ) -> dict[str, str | BatchItemError]:
        """Poll ``batch_id`` until terminal, then stream + parse results.

        Records ``response.usage`` for each per-item success via
        ``record_anthropic_usage`` so existing cost-tracking continues
        to work transparently.
        """
        self._ensure_client()
        deadline = time.monotonic() + max_wait_s
        while True:
            batch = self._client.messages.batches.retrieve(batch_id)
            state = getattr(batch, "processing_status", None) or getattr(batch, "status", None)
            if state == _TERMINAL_STATE:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Batch {batch_id} did not reach terminal state within "
                    f"{max_wait_s}s (last state={state!r})"
                )
            sleep_for = min(poll_interval_s, max(1.0, remaining))
            logger.debug(
                "[batch_client] batch=%s state=%s — sleeping %.1fs",
                batch_id,
                state,
                sleep_for,
            )
            time.sleep(sleep_for)

        return self._collect_results(batch_id)

    # ── Result parsing ────────────────────────────────────────────────

    def _collect_results(self, batch_id: str) -> dict[str, str | BatchItemError]:
        """Stream JSONL results, parse to dict[custom_id, str | error].

        Anthropic returns results as a JSONL stream where each line has
        shape::

            {"custom_id": "...", "result": {"type": "succeeded",
             "message": {...full Messages API response...}}}
            {"custom_id": "...", "result": {"type": "errored",
             "error": {"type": "...", "message": "..."}}}
            {"custom_id": "...", "result": {"type": "canceled"}}
            {"custom_id": "...", "result": {"type": "expired"}}

        We only record cost for ``succeeded``; the other three states
        are returned as BatchItemError so the caller can decide policy.
        """
        from genlab_core.intelligence.cost_accumulator import record_anthropic_usage

        results: dict[str, str | BatchItemError] = {}
        for entry in self._client.messages.batches.results(batch_id):
            cid = getattr(entry, "custom_id", None) or ""
            result = getattr(entry, "result", None)
            result_type = getattr(result, "type", None) if result else None

            if result_type == "succeeded":
                message = getattr(result, "message", None)
                if message is None:
                    results[cid] = BatchItemError(
                        custom_id=cid,
                        error_type="malformed_result",
                        message="succeeded result had no message",
                    )
                    continue
                # Record cost (mirrors AnthropicLLMClient.complete)
                try:
                    record_anthropic_usage(self._model, message)
                except Exception:  # noqa: BLE001 — cost tracking is best-effort
                    logger.warning(
                        "[batch_client] cost recording failed for cid=%s",
                        cid,
                        exc_info=True,
                    )
                # Extract assistant text — same shape as messages.create
                try:
                    text = message.content[0].text
                except (AttributeError, IndexError, TypeError) as exc:
                    results[cid] = BatchItemError(
                        custom_id=cid,
                        error_type="malformed_content",
                        message=f"could not extract text: {exc}",
                    )
                    continue
                results[cid] = text
            elif result_type == "errored":
                err = getattr(result, "error", None)
                results[cid] = BatchItemError(
                    custom_id=cid,
                    error_type=getattr(err, "type", "unknown") if err else "unknown",
                    message=getattr(err, "message", "no message") if err else "no message",
                )
            else:
                # canceled / expired / unknown — all caller-visible
                results[cid] = BatchItemError(
                    custom_id=cid,
                    error_type=result_type or "unknown",
                    message=f"batch item ended with non-success state: {result_type!r}",
                )

        return results
