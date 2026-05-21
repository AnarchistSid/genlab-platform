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

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        """Call the Anthropic Messages API and return the assistant text.

        Raises on network / auth errors — callers should handle gracefully.
        """
        self._ensure_client()
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        # Track cost if accumulator is available in current context
        try:
            from genlab_core.intelligence.cost_accumulator import get_accumulator

            acc = get_accumulator()
            if acc is not None:
                acc.record_llm(
                    model=self._model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )
        except Exception:
            pass  # cost tracking is non-critical

        return response.content[0].text
