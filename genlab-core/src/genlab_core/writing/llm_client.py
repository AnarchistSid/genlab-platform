"""Thin adapter bridging write_video_content's llm_client interface to Anthropic.

Usage:
    from genlab_core.writing.llm_client import AnthropicLLMClient
    from genlab_core.cost.model_router import get_model

    client = AnthropicLLMClient(model=get_model("write_sports_content"))
    result = client.complete(system="...", user="...", max_tokens=600, temperature=0.8)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class AnthropicLLMClient:
    """Adapter: .complete(system, user, max_tokens, temperature) -> str

    Lazily initialises the Anthropic SDK client on first call so that
    importing this module never triggers network I/O or requires the
    ``anthropic`` package to be installed (graceful degradation).
    """

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = model or "claude-haiku-4-5-20251001"
        self._client = None

    @property
    def is_available(self) -> bool:
        """True when an API key is configured."""
        return bool(self._api_key)

    def _ensure_client(self):
        if self._client is None:
            import anthropic  # noqa: F811 — lazy import

            self._client = anthropic.Anthropic(api_key=self._api_key)

    # U-01 (2026-06-17): minimum prompt size (in chars, ~4 chars/token)
    # below which prompt caching is NOT enabled. Anthropic's docs state
    # caching requires the cached prefix to be ≥1024 tokens (Haiku) /
    # ≥2048 tokens (Sonnet) to actually take effect — anything smaller
    # is billed at the cache-write rate (1.25×) for nothing. ~4 chars/token
    # is the common English heuristic; pad to 1500 chars (=~375 tokens) for
    # the smallest gate, since Haiku's 1024 threshold needs ~4000 chars and
    # we want a safety margin. The pad is intentionally conservative:
    # 4000-char threshold means short-prompt callers (engagement reply,
    # hook generator's per-platform variants) skip caching and pay the
    # normal input rate — exactly the desired behaviour.
    _CACHE_THRESHOLD_CHARS: int = 4000

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        """Call the Anthropic Messages API and return the assistant text.

        U-01: when ``system`` exceeds ``_CACHE_THRESHOLD_CHARS`` the system
        prompt is sent as a list-of-dict with ``cache_control: {"type":
        "ephemeral"}`` to enable Anthropic prompt caching. Subsequent
        calls within the 5-minute cache TTL pay only ~10% of normal input
        cost for the cached portion. The 5-niche writing pipeline runs
        ~30 candidates per niche with the same per-niche system prompt,
        so caching saves ~90% of input tokens on calls 2-30 within a
        niche.

        Falls back to the plain-string system format when below threshold
        — caching short prompts is net-negative (cache writes cost 1.25×
        the input rate).

        Raises on network / auth errors — callers should handle gracefully.
        """
        self._ensure_client()
        if len(system) >= self._CACHE_THRESHOLD_CHARS:
            system_arg = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_arg = system
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_arg,
            messages=[{"role": "user", "content": user}],
        )

        # Track cost if accumulator is available in current context (U-03:
        # shared helper, now used at every Anthropic call site). Cache
        # hit/miss is reflected by ``usage.input_tokens`` (excludes cached
        # tokens) + the separate ``cache_creation_input_tokens`` /
        # ``cache_read_input_tokens`` fields that record_anthropic_usage
        # will surface in a future refinement. The cost SAVINGS land
        # automatically since cached tokens aren't billed as input tokens.
        from genlab_core.intelligence.cost_accumulator import record_anthropic_usage

        record_anthropic_usage(self._model, response)

        return response.content[0].text
