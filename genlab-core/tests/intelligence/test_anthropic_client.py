"""Direct tests for AnthropicStrategistClient — the module that gates every
Strategist LLM call.

`test_strategist_skeleton.py::TestAnthropicClient` covers three high-level
paths (injected-factory happy path, retry-on-transient, propagate-on-non-
transient). This file extends coverage to the ENTIRE public surface:

- CallResult dataclass shape + defaults
- Constructor knobs (timeout, factory injection)
- `_get_client` (factory-first, anthropic import, API-key env-var resolution)
- `_is_transient` heuristic (each documented class + one that shouldn't retry)
- `generate_report` full call shape (model, temperature, max_tokens, timeout
  passed to SDK) + cost math + `record_anthropic_usage` telemetry hook
- Malformed / empty responses (missing content, missing usage attributes)
- Two-attempt exhaustion → RuntimeError with tail exception message
- Cost-accumulator import failure is swallowed (fail-open telemetry)

Style follows `tests/engagement/test_toxicity_extended.py`: one concern per
class, docstrings on every test explaining what invariant it locks in.

Mocking is done via the module's own `client_factory` injection seam (the
source docstring explicitly invites this) — no real anthropic SDK calls
happen and no ANTHROPIC_API_KEY needs to be set for most tests. Where we
DO exercise `_get_client`'s default path, we patch `anthropic.Anthropic`
at the SDK module (matches the CLAUDE.md guidance on lazy imports).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.intelligence.anthropic_client import (
    DEFAULT_TIMEOUT_SEC,
    MAX_OUTPUT_TOKENS,
    STRATEGIST_MODEL,
    AnthropicStrategistClient,
    CallResult,
)

# === Helpers ========================================================


def _mk_response(
    text: str = '{"ok": true}',
    input_tokens: int = 1_000,
    output_tokens: int = 500,
):
    """Build a mock anthropic messages.create response."""
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return response


def _mk_client(response=None, side_effect=None) -> MagicMock:
    """Build a mock anthropic client whose `messages.create` returns/raises."""
    client = MagicMock()
    if side_effect is not None:
        client.messages.create.side_effect = side_effect
    else:
        client.messages.create.return_value = response if response is not None else _mk_response()
    return client


# === CallResult dataclass ==========================================


class TestCallResult:
    """Locks in the on-wire shape of the value returned by generate_report."""

    def test_all_fields_are_positional_and_typed(self):
        """CallResult is a frozen-ish contract — cost accounting depends on
        every field being present and typed as declared."""
        r = CallResult(
            text="hi",
            model="claude-sonnet-4-6",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.001,
            duration_sec=1.23,
        )
        assert r.text == "hi"
        assert r.model == "claude-sonnet-4-6"
        assert r.input_tokens == 10
        assert r.output_tokens == 5
        assert r.cost_usd == 0.001
        assert r.duration_sec == 1.23

    def test_is_regular_dataclass_not_frozen(self):
        """The module declares it with plain @dataclass — fields are mutable.
        If someone adds `frozen=True` later this test flags that as a
        breaking change to callers that mutate it."""
        r = CallResult(
            text="x", model="m", input_tokens=1, output_tokens=1, cost_usd=0.0, duration_sec=0.0
        )
        r.text = "y"  # would raise if frozen
        assert r.text == "y"


# === Module-level constants ========================================


class TestModuleConstants:
    """Constants are load-bearing — the model name is a prompt-versioning
    contract, and the timeout was bumped from 60→180 after a live-fire
    incident. Both are worth pinning."""

    def test_strategist_model_is_sonnet_4_6(self):
        """Bumping this constant requires bumping SYSTEM_PROMPT_VERSION too
        (the module docstring says so). Pin it here so an accidental
        model swap fails a test instead of silently drifting output."""
        assert STRATEGIST_MODEL == "claude-sonnet-4-6"

    def test_default_timeout_is_180_seconds(self):
        """60s caused every prod call to time out (measured p50 was 67s).
        180 gives ~2.7× headroom. If someone reverts this we want the
        test to catch it before the next Strategist fire."""
        assert DEFAULT_TIMEOUT_SEC == 180.0

    def test_max_output_tokens_covers_json_payload(self):
        """Bumped 4K → 16K in Phase 2.D (2026-08-14) to accommodate
        longer strategist proposals + meta-strategist verdicts. Pinned
        so a typo like 400 doesn't ship silently."""
        assert MAX_OUTPUT_TOKENS == 16_000


# === Construction ==================================================


class TestConstruction:
    """The constructor is intentionally minimal but the two knobs — factory
    injection and timeout — matter for test hermeticity and live-fire
    resilience respectively."""

    def test_default_timeout_used_when_not_specified(self):
        """No timeout arg → module DEFAULT_TIMEOUT_SEC (180s) is used."""
        client = AnthropicStrategistClient()
        assert client._timeout == DEFAULT_TIMEOUT_SEC

    def test_custom_timeout_is_honored(self):
        """Callers may pass a shorter timeout (e.g. tests) — must stick."""
        client = AnthropicStrategistClient(timeout_sec=5.0)
        assert client._timeout == 5.0

    def test_no_factory_defaults_to_none(self):
        """Default factory is None so `_get_client` falls back to the real
        anthropic SDK path."""
        client = AnthropicStrategistClient()
        assert client._client_factory is None

    def test_factory_stored_when_provided(self):
        """Injected factory is stored verbatim (not called at construction)."""

        def sentinel():
            return None

        client = AnthropicStrategistClient(client_factory=sentinel)
        assert client._client_factory is sentinel

    def test_construction_does_not_call_factory(self):
        """Constructing must not touch the network / SDK. Factory is only
        invoked lazily inside `generate_report`."""
        factory = MagicMock()
        AnthropicStrategistClient(client_factory=factory)
        factory.assert_not_called()


# === _get_client resolution ========================================


class TestGetClientResolution:
    """`_get_client` is the SDK boundary — three branches (factory / SDK
    missing / API key missing) each need explicit coverage."""

    def test_factory_takes_precedence_over_sdk(self):
        """When a factory is set, `_get_client` returns whatever it gives.
        The real anthropic SDK must not be touched — critical for
        hermetic tests."""
        sentinel = object()
        client = AnthropicStrategistClient(client_factory=lambda: sentinel)
        assert client._get_client() is sentinel

    def test_missing_api_key_raises_runtime_error(self, monkeypatch):
        """No factory, no ANTHROPIC_API_KEY → RuntimeError with a
        helpful message. Prevents accidental 'None' being passed to
        the SDK where the error would be an obscure 401 later."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Ensure the SDK import succeeds by injecting a stub anthropic module.
        fake_anthropic = SimpleNamespace(Anthropic=MagicMock())
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
        client = AnthropicStrategistClient()
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            client._get_client()

    def test_sdk_missing_raises_runtime_error_with_uv_hint(self, monkeypatch):
        """Simulate a fresh env with no `anthropic` installed. The module
        should surface a RuntimeError that names uv (the workspace
        installer) so operators know how to remediate."""
        # Force ImportError by making the anthropic module raise on import.
        # We do that by inserting a mapping that raises when accessed via
        # `import anthropic`.
        monkeypatch.setitem(sys.modules, "anthropic", None)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        client = AnthropicStrategistClient()
        with pytest.raises(RuntimeError, match="anthropic SDK not installed"):
            client._get_client()

    def test_sdk_present_and_key_present_constructs_real_client(self, monkeypatch):
        """With SDK importable and key set, `_get_client` calls
        `anthropic.Anthropic(api_key=...)`."""
        fake_ctor = MagicMock(return_value="real-anthropic-client")
        fake_anthropic = SimpleNamespace(Anthropic=fake_ctor)
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-abc123")
        client = AnthropicStrategistClient()
        got = client._get_client()
        assert got == "real-anthropic-client"
        fake_ctor.assert_called_once_with(api_key="sk-abc123")


# === _is_transient heuristic =======================================


class TestIsTransientHeuristic:
    """The retry decision is driven by the class-name heuristic. Every
    listed name (and one that isn't listed) needs to be pinned so a
    typo like 'ApiConnectionError' vs 'APIConnectionError' is caught
    at test time."""

    @pytest.mark.parametrize(
        "exc_class_name",
        [
            "APIConnectionError",
            "APITimeoutError",
            "InternalServerError",
            "RateLimitError",
            "TimeoutError",
            "ConnectionError",
        ],
    )
    def test_documented_transient_classes_retry(self, exc_class_name):
        """Each name in the whitelist triggers a retry — verified by
        constructing an ad-hoc exception with that class name."""
        exc_cls = type(exc_class_name, (Exception,), {})
        assert AnthropicStrategistClient._is_transient(exc_cls("boom")) is True

    def test_unrelated_exception_is_not_transient(self):
        """Random exception classes must NOT be retried — otherwise a
        genuine ValueError / KeyError / auth bug would burn 2 attempts
        instead of surfacing immediately."""
        assert AnthropicStrategistClient._is_transient(ValueError("bad key")) is False

    def test_authentication_error_is_not_transient(self):
        """A common footgun: retrying on auth errors just double-charges
        the failure. Pin explicitly."""

        class AuthenticationError(Exception):
            pass

        assert AnthropicStrategistClient._is_transient(AuthenticationError("401")) is False

    def test_bad_request_error_is_not_transient(self):
        """Prompt-shape / max_tokens errors must fail fast so the caller
        can fix the input, not retry."""

        class BadRequestError(Exception):
            pass

        assert AnthropicStrategistClient._is_transient(BadRequestError("400")) is False

    # DEV-3 observation fix pins — belt-and-suspenders isinstance
    # path was added 2026-07-09. Locks in the invariant that the
    # heuristic also matches by class hierarchy so a future SDK
    # rename doesn't silently stop retries.

    def test_isinstance_path_catches_renamed_class(self):
        """The DEV-3 fix (2026-07-09): if the anthropic SDK renamed
        `APIConnectionError` to a new class name that STILL inherits
        from the same base, isinstance still catches it — the pre-fix
        name-only check would have silently stopped retrying.

        We simulate this by injecting a fake `anthropic` module with a
        renamed class into ``sys.modules`` for the duration of the test,
        then raising the renamed class. The heuristic should still
        return True because isinstance sees it."""
        import sys
        import types

        # Create a fake anthropic module with a CustomAPIConnectionError
        # class that's the "renamed" successor to APIConnectionError.
        fake_anthropic = types.ModuleType("anthropic")

        class BaseAPIError(Exception):
            pass

        class CustomRenamedConnectionError(BaseAPIError):
            pass

        # Expose it under the OLD attribute name so the isinstance
        # path picks it up — this is what a future SDK version that
        # kept the class but with new backing implementation would
        # look like.
        fake_anthropic.APIConnectionError = CustomRenamedConnectionError
        original = sys.modules.get("anthropic")
        sys.modules["anthropic"] = fake_anthropic
        try:
            # Raise an instance — the CLASS NAME is now
            # CustomRenamedConnectionError, NOT APIConnectionError.
            # The name-based check WON'T match. The isinstance check
            # against fake_anthropic.APIConnectionError WILL match.
            exc = CustomRenamedConnectionError("simulated SDK rename")
            assert type(exc).__name__ == "CustomRenamedConnectionError"
            assert AnthropicStrategistClient._is_transient(exc) is True, (
                "isinstance path failed — the belt-and-suspenders fix "
                "isn't catching class-hierarchy matches. DEV-3 fix "
                "regressed."
            )
        finally:
            if original is not None:
                sys.modules["anthropic"] = original
            else:
                sys.modules.pop("anthropic", None)

    def test_missing_anthropic_module_falls_back_to_name_match(self):
        """When the anthropic SDK isn't installed (test envs), the
        isinstance path must degrade gracefully to name-match only.
        Locks in that removing anthropic doesn't crash _is_transient."""
        import sys

        original = sys.modules.get("anthropic")
        # Simulate SDK not installed by removing from sys.modules AND
        # patching the import to fail. Simplest way: remove from
        # sys.modules; the lazy import inside _is_transient will
        # try to re-import and (in a real "not installed" scenario)
        # raise ImportError. Since anthropic IS installed here, we
        # patch the import machinery.
        sys.modules["anthropic"] = None  # type: ignore[assignment]
        try:
            # Name-match still works — this exception matches the
            # frozenset directly.
            exc_cls = type("APIConnectionError", (Exception,), {})
            assert AnthropicStrategistClient._is_transient(exc_cls("boom")) is True
            # Unrelated exception still returns False even with the
            # isinstance path unable to import.
            assert AnthropicStrategistClient._is_transient(ValueError("bad")) is False
        finally:
            if original is not None:
                sys.modules["anthropic"] = original
            else:
                sys.modules.pop("anthropic", None)

    def test_transient_error_names_frozenset_is_locked(self):
        """The name list is now an explicit class attribute so pin
        tests can assert against it. Locks the set of currently-
        documented transient class names — any addition/removal
        forces an intentional test edit."""
        assert AnthropicStrategistClient._TRANSIENT_ERROR_NAMES == frozenset(
            {
                "APIConnectionError",
                "APITimeoutError",
                "InternalServerError",
                "RateLimitError",
                "TimeoutError",
                "ConnectionError",
            }
        )


# === generate_report — happy path ================================


class TestSuccessPath:
    """One call → one CallResult, correct SDK arguments, correct cost math."""

    def test_returns_call_result_with_expected_fields(self):
        """Basic happy path: mocked SDK returns a well-formed response;
        the wrapper hands back a CallResult with matching fields."""
        mock_client = _mk_client(_mk_response("payload", input_tokens=200, output_tokens=100))
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        result = client.generate_report("sys", "user")
        assert isinstance(result, CallResult)
        assert result.text == "payload"
        assert result.model == STRATEGIST_MODEL
        assert result.input_tokens == 200
        assert result.output_tokens == 100

    def test_passes_all_expected_kwargs_to_sdk(self):
        """The SDK call site owns model, max_tokens, system, messages,
        temperature, timeout. Pin the exact kwargs so an accidental
        rename (e.g. `system_prompt=`) fails at test time, not in prod."""
        mock_client = _mk_client()
        client = AnthropicStrategistClient(client_factory=lambda: mock_client, timeout_sec=42.5)
        client.generate_report("sys-prompt", "user-prompt")
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["model"] == STRATEGIST_MODEL
        assert kwargs["max_tokens"] == MAX_OUTPUT_TOKENS
        assert kwargs["system"] == "sys-prompt"
        assert kwargs["messages"] == [{"role": "user", "content": "user-prompt"}]
        assert kwargs["temperature"] == 0.3
        assert kwargs["timeout"] == 42.5

    def test_cost_calculation_is_correct(self):
        """cost = (in * 3.00 + out * 15.00) / 1e6. Pin the math so a
        pricing-table typo doesn't silently under- or over-report."""
        mock_client = _mk_client(_mk_response(input_tokens=1_000_000, output_tokens=1_000_000))
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        result = client.generate_report("s", "u")
        # 1M input @ $3.00 + 1M output @ $15.00 = $18.00 exactly.
        assert result.cost_usd == pytest.approx(18.00, rel=1e-6)

    def test_cost_is_rounded_to_six_decimals(self):
        """The dataclass field is `round(cost, 6)`. Tiny costs must not
        underflow to zero — pin the precision floor."""
        mock_client = _mk_client(_mk_response(input_tokens=1, output_tokens=1))
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        result = client.generate_report("s", "u")
        # (3 + 15) / 1e6 = 1.8e-5; rounded to 6 places → 0.000018
        assert result.cost_usd == 0.000018

    def test_duration_is_measured_and_rounded(self):
        """duration_sec is `round(_, 2)` and must be >= 0. Pin the shape
        (non-negative float) rather than an exact value."""
        mock_client = _mk_client()
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        result = client.generate_report("s", "u")
        assert isinstance(result.duration_sec, float)
        assert result.duration_sec >= 0.0

    def test_pricing_reads_from_cost_accumulator(self):
        """Post-2026-07-08 fix: pricing lives in ONE place —
        :data:`cost_accumulator.MODEL_COSTS`. The client reads from
        that dict via `_pricing()` so a pricing update in
        cost_accumulator propagates to CallResult.cost_usd
        automatically.

        Prior to the fix the client held its own INPUT_COST_PER_M /
        OUTPUT_COST_PER_M constants that would silently drift from
        the dashboard aggregate on a pricing change."""
        from genlab_core.intelligence.anthropic_client import (
            STRATEGIST_MODEL,
        )
        from genlab_core.intelligence.cost_accumulator import MODEL_COSTS

        input_per_m, output_per_m = AnthropicStrategistClient._pricing()
        # Should equal MODEL_COSTS entry — locks in the delegation.
        entry = MODEL_COSTS.get(STRATEGIST_MODEL, {})
        assert input_per_m == entry.get("input", 3.00)
        assert output_per_m == entry.get("output", 15.00)

    def test_pricing_falls_back_to_default_on_missing_entry(self, monkeypatch):
        """If MODEL_COSTS is missing STRATEGIST_MODEL (staleness),
        _pricing() logs a warning and falls back to the pre-fix
        default (3.00, 15.00). Locks in the no-behaviour-regression
        contract of the fix."""
        # Patch the module's MODEL_COSTS to force the fallback path.
        from genlab_core.intelligence import cost_accumulator

        original = cost_accumulator.MODEL_COSTS
        monkeypatch.setattr(cost_accumulator, "MODEL_COSTS", {})
        try:
            input_per_m, output_per_m = AnthropicStrategistClient._pricing()
            assert input_per_m == 3.00
            assert output_per_m == 15.00
        finally:
            monkeypatch.setattr(cost_accumulator, "MODEL_COSTS", original)

    def test_pricing_no_longer_exposes_class_constants(self):
        """Guards against a re-inlining that would recreate the drift
        risk. Post-fix, no INPUT_COST_PER_M / OUTPUT_COST_PER_M
        class attribute should exist."""
        assert not hasattr(AnthropicStrategistClient, "INPUT_COST_PER_M"), (
            "INPUT_COST_PER_M re-added — pricing is now single-source via cost_accumulator"
        )
        assert not hasattr(AnthropicStrategistClient, "OUTPUT_COST_PER_M"), (
            "OUTPUT_COST_PER_M re-added — pricing is now single-source via cost_accumulator"
        )


# === Response parsing edge cases ==================================


class TestResponseParsing:
    """The SDK response schema has three failure modes we tolerate:
    empty content list, missing usage token counts, and non-JSON text.
    Text is returned verbatim (parsing lives in the caller)."""

    def test_empty_content_yields_empty_text(self):
        """When Anthropic returns no content blocks (rare but happens
        on refusals), `text` must be empty string — never crash."""
        response = MagicMock()
        response.content = []
        response.usage = MagicMock(input_tokens=50, output_tokens=0)
        mock_client = _mk_client(response=response)
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        result = client.generate_report("s", "u")
        assert result.text == ""
        assert result.output_tokens == 0

    def test_missing_usage_fields_default_to_zero(self):
        """`getattr(usage, "input_tokens", 0)` — if the SDK version
        removes/renames these attrs, tokens must be 0 rather than raise."""
        response = MagicMock()
        response.content = [MagicMock(text="ok")]
        # Use a plain object with NO input_tokens/output_tokens attrs.
        response.usage = object()
        mock_client = _mk_client(response=response)
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        result = client.generate_report("s", "u")
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.cost_usd == 0.0  # zero tokens → zero cost

    def test_non_json_text_is_returned_verbatim(self):
        """This client does NOT parse JSON — that's the Strategist's job
        via schema validation. Malformed JSON must pass through as
        `.text` so the caller decides how to fail."""
        mock_client = _mk_client(_mk_response("this is not json at all"))
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        result = client.generate_report("s", "u")
        assert result.text == "this is not json at all"


# === Retry semantics ===============================================


class TestRetrySemantics:
    """Two-attempt budget: retry once on transient, propagate on non-
    transient, and raise RuntimeError with tail message on double
    transient failure."""

    def test_transient_then_success(self):
        """First call raises a transient class, second returns a response
        → wrapper returns CallResult from the second attempt."""

        class APIConnectionError(Exception):
            pass

        good = _mk_response("recovered")
        mock_client = _mk_client(side_effect=[APIConnectionError("blip"), good])
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        result = client.generate_report("s", "u")
        assert result.text == "recovered"
        assert mock_client.messages.create.call_count == 2

    def test_two_transient_failures_raise_runtime_error(self):
        """After 2 transient failures we exit the loop and raise
        RuntimeError with the last exception message embedded."""

        class RateLimitError(Exception):
            pass

        mock_client = _mk_client(side_effect=[RateLimitError("first"), RateLimitError("second")])
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        with pytest.raises(RuntimeError, match="failed after 2 attempts.*second"):
            client.generate_report("s", "u")
        assert mock_client.messages.create.call_count == 2

    def test_non_transient_raises_immediately_no_retry(self):
        """A non-transient class must NOT trigger a retry — the SDK
        call count stays at 1."""

        class AuthenticationError(Exception):
            pass

        mock_client = _mk_client(side_effect=AuthenticationError("bad key"))
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        with pytest.raises(AuthenticationError):
            client.generate_report("s", "u")
        assert mock_client.messages.create.call_count == 1

    def test_transient_then_non_transient_stops_at_second_attempt(self):
        """Retry once for transient, but if attempt 2 raises a
        non-transient class, propagate it (no third attempt)."""

        class APITimeoutError(Exception):
            pass

        class BadRequestError(Exception):
            pass

        mock_client = _mk_client(side_effect=[APITimeoutError("slow"), BadRequestError("400")])
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        with pytest.raises(BadRequestError):
            client.generate_report("s", "u")
        assert mock_client.messages.create.call_count == 2


# === Cost-accumulator telemetry hook ==============================


class TestCostAccumulatorHook:
    """The block at anthropic_client.py:117-124 wires the Strategist's
    weekly LLM spend into the dashboard cost-per-blueprint aggregate.
    Import failure must be swallowed (fail-open telemetry — never
    break the LLM call over a broken metric write)."""

    def test_record_anthropic_usage_is_called_with_model_and_response(self):
        """Happy path: cost_accumulator.record_anthropic_usage is called
        exactly once with STRATEGIST_MODEL and the SDK response."""
        response = _mk_response()
        mock_client = _mk_client(response=response)
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage") as rec:
            client.generate_report("s", "u")
        rec.assert_called_once_with(STRATEGIST_MODEL, response)

    def test_cost_accumulator_import_failure_is_swallowed(self):
        """If cost_accumulator isn't importable (or raises), the LLM call
        must still return a CallResult — the source deliberately wraps
        the import in try/except to survive test envs and telemetry
        outages. Pin this fail-open contract."""
        mock_client = _mk_client()
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        with patch(
            "genlab_core.intelligence.cost_accumulator.record_anthropic_usage",
            side_effect=RuntimeError("telemetry broken"),
        ):
            result = client.generate_report("s", "u")
        assert isinstance(result, CallResult)
        assert result.text == '{"ok": true}'


# === OpenAI fallback on Anthropic exhaustion (2026-07-21) ==========


class _FakeAnthropicExhaustion(Exception):
    """Mimics Anthropic BadRequestError 400 credit-balance body — matches
    `llm/fallback.should_fallback` classifier."""

    def __str__(self):
        return "credit balance is too low"


class TestOpenAIFallback:
    """Strategist was the 9th Anthropic-direct site — wired to OpenAI
    fallback on 2026-07-21 after Anthropic-exhaustion outage caused the
    strategist systemd service to fail every Sunday for 2 weeks.

    gpt-4o (not gpt-4o-mini like the other 8 sites) because Strategist
    is a reasoning task with JSON-schema-adherence requirements — mini
    is too small for meta-cognition prompts.
    """

    def test_exhaustion_triggers_openai_fallback(self, monkeypatch):
        """Anthropic 400 credit-low → OpenAI gpt-4o returns CallResult."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        exc = _FakeAnthropicExhaustion()
        mock_client = _mk_client(side_effect=exc)
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        with patch(
            "genlab_core.llm.fallback.call_openai_fallback",
            return_value='{"proposals": []}',
        ) as mock_openai:
            result = client.generate_report("sys", "user")
        assert isinstance(result, CallResult)
        assert result.text == '{"proposals": []}'
        assert "fallback" in result.model.lower()
        # gpt-4o (not mini) is the required tier for reasoning
        assert mock_openai.call_args.kwargs["model"] == "gpt-4o"
        # JSON mode required — strategist output must be parseable
        assert mock_openai.call_args.kwargs["json_mode"] is True

    def test_no_openai_key_re_raises_anthropic_error(self, monkeypatch):
        """Without OPENAI_API_KEY, exhaustion re-raises original Anthropic
        exception — no silent degradation."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        exc = _FakeAnthropicExhaustion()
        mock_client = _mk_client(side_effect=exc)
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        with pytest.raises(_FakeAnthropicExhaustion):
            client.generate_report("sys", "user")

    def test_non_exhaustion_error_does_not_trigger_fallback(self, monkeypatch):
        """Non-exhaustion errors (auth, network) skip fallback path even
        when OPENAI_API_KEY is set. Auth errors need operator, not
        another provider."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")

        class _AuthError(Exception):
            def __str__(self):
                return "401 unauthorized"

        mock_client = _mk_client(side_effect=_AuthError())
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        with patch("genlab_core.llm.fallback.call_openai_fallback") as mock_openai:
            with pytest.raises(_AuthError):
                client.generate_report("sys", "user")
        mock_openai.assert_not_called()

    def test_openai_fallback_also_fails_re_raises_anthropic(self, monkeypatch):
        """Belt-and-suspenders: if OpenAI ALSO fails during fallback,
        re-raise the ORIGINAL Anthropic error so downstream error
        classifiers behave as they did pre-fallback."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        exc = _FakeAnthropicExhaustion()
        mock_client = _mk_client(side_effect=exc)
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        with patch(
            "genlab_core.llm.fallback.call_openai_fallback",
            side_effect=RuntimeError("OpenAI also down"),
        ):
            # Fallback returns None → falls into _is_transient check.
            # _FakeAnthropicExhaustion isn't transient → re-raise.
            with pytest.raises(_FakeAnthropicExhaustion):
                client.generate_report("sys", "user")

    def test_fallback_kill_switch(self, monkeypatch):
        """GENLAB_LLM_FALLBACK_ENABLED=0 bypasses fallback entirely."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        monkeypatch.setenv("GENLAB_LLM_FALLBACK_ENABLED", "0")
        exc = _FakeAnthropicExhaustion()
        mock_client = _mk_client(side_effect=exc)
        client = AnthropicStrategistClient(client_factory=lambda: mock_client)
        with patch("genlab_core.llm.fallback.call_openai_fallback") as mock_openai:
            with pytest.raises(_FakeAnthropicExhaustion):
                client.generate_report("sys", "user")
        mock_openai.assert_not_called()
