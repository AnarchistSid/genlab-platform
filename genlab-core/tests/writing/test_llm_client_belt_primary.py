"""LLM-PRIMARY-01: belt is tried before the direct providers, and a
completed-but-empty belt response is a failure, not a success.

2026-09-14: Anthropic returned 400 "credit balance is too low" and OpenAI 429
"no credits remaining" in the same fire. The failover chain executed perfectly
and had nowhere to go; content generation stopped on every niche while belt held
$102 the writer could not reach. These pin the inversion that fixed it.

The empty-response test is the important one. `status_text == "completed"` has
been a false green four separate times in this codebase — a belt task can
complete and carry nothing, and every naive check reads that as success.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from genlab_core.writing import llm_client as mod


class _Belt:
    """Stand-in for belt_client.run_app."""

    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.apps: list[str] = []

    def __call__(self, app: str, input_data: dict, **kw: Any):
        self.apps.append(app)
        return self.results.pop(0) if self.results else _res(ok=False, error="exhausted")


def _res(*, ok: bool, response: str | None = None, error: str | None = None):
    from genlab_core.integrations.belt_client import BeltResult

    out = {"response": response} if response is not None else None
    return BeltResult(ok=ok, output=out, task_id="t_test", error=error)


@pytest.fixture(autouse=True)
def _allow_belt(monkeypatch: pytest.MonkeyPatch):
    """The production guard disables belt under pytest so the suite never makes
    real billed subprocess calls. These tests opt in explicitly and stub the
    transport, so nothing leaves the process."""
    monkeypatch.setenv("GENLAB_ALLOW_TEST_BELT_CALLS", "1")
    monkeypatch.delenv("GENLAB_LLM_BELT_PRIMARY", raising=False)


def _client() -> Any:
    c = mod.AnthropicLLMClient.__new__(mod.AnthropicLLMClient)
    c._model = "claude-sonnet-4-6"
    c._client = None
    return c


def test_belt_is_tried_before_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    belt = _Belt(_res(ok=True, response="from belt"))
    monkeypatch.setattr("genlab_core.integrations.belt_client.run_app", belt)

    def _boom(*a: Any, **k: Any):
        raise AssertionError("Anthropic must not be called while belt succeeds")

    monkeypatch.setattr(_client(), "_ensure_client", _boom, raising=False)

    out = mod.AnthropicLLMClient.complete(_client(), system="s", user="u", max_tokens=10)
    assert out == "from belt"
    assert belt.apps == ["anthropic/claude-sonnet-4-6"], f"Sonnet must be tier 1; got {belt.apps}"


def test_completed_but_empty_response_falls_to_the_next_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The false-green shape: ok=True, status completed, payload empty."""
    belt = _Belt(_res(ok=True, response="   "), _res(ok=True, response="from haiku"))
    monkeypatch.setattr("genlab_core.integrations.belt_client.run_app", belt)

    out = mod.AnthropicLLMClient.complete(_client(), system="s", user="u", max_tokens=10)
    assert out == "from haiku"
    assert belt.apps == [
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-haiku-4-5",
    ], f"an empty Sonnet response must fall through to Haiku; got {belt.apps}"


def test_all_belt_tiers_failing_falls_through_to_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt down must not swallow the call — the direct path still runs, and its
    error still surfaces when those accounts are empty too."""
    belt = _Belt(_res(ok=False, error="down"), _res(ok=False, error="down"))
    monkeypatch.setattr("genlab_core.integrations.belt_client.run_app", belt)

    reached = {"direct": False}

    def _ensure(self_: Any) -> None:
        reached["direct"] = True
        raise RuntimeError("anthropic unfunded")

    monkeypatch.setattr(mod.AnthropicLLMClient, "_ensure_client", _ensure, raising=False)
    monkeypatch.setattr(mod, "_fallback_enabled", lambda: False)

    with pytest.raises(RuntimeError, match="anthropic unfunded"):
        mod.AnthropicLLMClient.complete(_client(), system="s", user="u", max_tokens=10)
    assert reached["direct"], "the direct provider must still be attempted"
    assert len(belt.apps) == 2, "both belt tiers must be tried first"


def test_kill_switch_restores_the_old_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENLAB_LLM_BELT_PRIMARY", "0")
    belt = _Belt(_res(ok=True, response="from belt"))
    monkeypatch.setattr("genlab_core.integrations.belt_client.run_app", belt)

    def _ensure(self_: Any) -> None:
        raise RuntimeError("direct path reached")

    monkeypatch.setattr(mod.AnthropicLLMClient, "_ensure_client", _ensure, raising=False)
    monkeypatch.setattr(mod, "_fallback_enabled", lambda: False)

    with pytest.raises(RuntimeError, match="direct path reached"):
        mod.AnthropicLLMClient.complete(_client(), system="s", user="u", max_tokens=10)
    assert belt.apps == [], "with the kill switch set, belt must not be called at all"


def test_belt_is_off_under_pytest_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard that stopped the suite making real billed calls.

    Without it the suite went 2.3s -> 114s and spent $0.14 of real credit.
    """
    monkeypatch.delenv("GENLAB_ALLOW_TEST_BELT_CALLS", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    assert mod._belt_primary_enabled() is False
    monkeypatch.setenv("GENLAB_ALLOW_TEST_BELT_CALLS", "1")
    assert mod._belt_primary_enabled() is True


def test_pytest_guard_does_not_disable_belt_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard the guard: it must key on PYTEST_CURRENT_TEST only."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("GENLAB_ALLOW_TEST_BELT_CALLS", raising=False)
    assert mod._belt_primary_enabled() is True
    assert os.environ.get("PYTEST_CURRENT_TEST") is None


def test_belt_tier_order_honours_the_requested_model() -> None:
    """A caller asking for Haiku must get Haiku first, not a silent upgrade.

    Found by gate 2 on 2026-09-14: a fixed Sonnet-first list routed
    music_mood_llm_fit, dynamic_matcher and chart_data_extract — all of which
    construct the client with Haiku — to Sonnet instead. ~20x the input cost on
    calls budgeted at ~$0.00004, plus a behaviour change.
    """
    haiku_first = mod._belt_tiers_for("claude-haiku-4-5-20251001")
    assert haiku_first[0][0] == "anthropic/claude-haiku-4-5", haiku_first
    assert haiku_first[1][0] == "anthropic/claude-sonnet-4-6", (
        "the other model must remain as a second tier so one being down degrades"
    )

    sonnet_first = mod._belt_tiers_for("claude-sonnet-4-6")
    assert sonnet_first[0][0] == "anthropic/claude-sonnet-4-6", sonnet_first

    # Unknown / empty model falls back to Sonnet-first rather than crashing.
    assert mod._belt_tiers_for("")[0][0] == "anthropic/claude-sonnet-4-6"


def test_haiku_client_actually_calls_the_haiku_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through complete(), not just the ordering helper."""
    belt = _Belt(_res(ok=True, response="from haiku"))
    monkeypatch.setattr("genlab_core.integrations.belt_client.run_app", belt)
    c = _client()
    c._model = "claude-haiku-4-5-20251001"
    out = mod.AnthropicLLMClient.complete(c, system="s", user="u", max_tokens=10)
    assert out == "from haiku"
    assert belt.apps == ["anthropic/claude-haiku-4-5"], (
        f"a Haiku client must hit the Haiku app first; got {belt.apps}"
    )
