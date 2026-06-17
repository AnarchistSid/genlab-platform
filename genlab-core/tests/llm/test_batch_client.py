"""Tests for the Anthropic Batch API wrapper (U-02).

The Anthropic SDK is mocked end-to-end — no network. Tests cover the
shape of the batch lifecycle (submit → poll → results) + the per-item
error model + the policy choices baked into the wrapper (dedup
custom_ids, timeout, cost recording).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.llm.batch import (
    BatchClient,
    BatchItem,
    BatchItemError,
)

# ── Fake SDK objects (shape-match anthropic SDK return values) ──────


@dataclass
class _FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class _FakeContent:
    text: str
    type: str = "text"


@dataclass
class _FakeMessage:
    """Mirrors anthropic.types.Message enough for our extractor."""

    content: list[_FakeContent]
    usage: _FakeUsage
    model: str = "claude-haiku-4-5-20251001"
    id: str = "msg_fake"
    role: str = "assistant"
    stop_reason: str = "end_turn"
    type: str = "message"


@dataclass
class _FakeResult:
    type: str  # "succeeded" | "errored" | "canceled" | "expired"
    message: Any = None
    error: Any = None


@dataclass
class _FakeResultEntry:
    custom_id: str
    result: _FakeResult


@dataclass
class _FakeError:
    type: str
    message: str


class _FakeBatch:
    def __init__(self, state: str, id: str = "batch_fake_001"):
        self.id = id
        self.processing_status = state


def _make_succeeded_entry(cid: str, text: str = "ok") -> _FakeResultEntry:
    return _FakeResultEntry(
        custom_id=cid,
        result=_FakeResult(
            type="succeeded",
            message=_FakeMessage(content=[_FakeContent(text=text)], usage=_FakeUsage()),
        ),
    )


def _make_errored_entry(cid: str, err_type: str, msg: str) -> _FakeResultEntry:
    return _FakeResultEntry(
        custom_id=cid,
        result=_FakeResult(type="errored", error=_FakeError(type=err_type, message=msg)),
    )


# ── Construction + availability ──────────────────────────────────────


class TestConstruction:
    def test_uses_env_api_key_by_default(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key-xyz")
        c = BatchClient()
        assert c._api_key == "env-key-xyz"

    def test_explicit_api_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
        c = BatchClient(api_key="explicit-key")
        assert c._api_key == "explicit-key"

    def test_default_model_is_haiku(self):
        c = BatchClient(api_key="x")
        assert c._model == "claude-haiku-4-5-20251001"

    def test_explicit_model_used(self):
        c = BatchClient(api_key="x", model="claude-sonnet-4-6")
        assert c._model == "claude-sonnet-4-6"

    def test_is_available_false_without_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        c = BatchClient(api_key="")
        assert c.is_available is False

    def test_is_available_true_with_key(self):
        c = BatchClient(api_key="x")
        assert c.is_available is True


# ── Input validation ─────────────────────────────────────────────────


class TestInputValidation:
    def test_empty_items_raises(self):
        c = BatchClient(api_key="x")
        with pytest.raises(ValueError, match="at least one item"):
            c.run([])

    def test_duplicate_custom_ids_raise(self):
        c = BatchClient(api_key="x")
        items = [
            BatchItem(custom_id="dup", system="s", user="u"),
            BatchItem(custom_id="other", system="s", user="u"),
            BatchItem(custom_id="dup", system="s", user="u"),
        ]
        with pytest.raises(ValueError, match="unique custom_ids"):
            c.run(items)


# ── Submit path ──────────────────────────────────────────────────────


class TestSubmit:
    def test_submit_returns_batch_id(self):
        fake_sdk = MagicMock()
        fake_sdk.messages.batches.create.return_value = MagicMock(id="batch_abc")

        c = BatchClient(api_key="x")
        c._client = fake_sdk

        bid = c.submit([BatchItem(custom_id="a", system="s", user="u")])
        assert bid == "batch_abc"

    def test_submit_passes_model_and_params(self):
        fake_sdk = MagicMock()
        fake_sdk.messages.batches.create.return_value = MagicMock(id="batch_xyz")

        c = BatchClient(api_key="x", model="claude-sonnet-4-6")
        c._client = fake_sdk
        c.submit(
            [
                BatchItem(
                    custom_id="a",
                    system="sys-prompt",
                    user="user-prompt",
                    max_tokens=2000,
                    temperature=0.3,
                )
            ]
        )

        req = fake_sdk.messages.batches.create.call_args.kwargs["requests"][0]
        assert req["custom_id"] == "a"
        assert req["params"]["model"] == "claude-sonnet-4-6"
        assert req["params"]["max_tokens"] == 2000
        assert req["params"]["temperature"] == 0.3
        assert req["params"]["system"] == "sys-prompt"
        assert req["params"]["messages"] == [{"role": "user", "content": "user-prompt"}]


# ── Poll + collect path ──────────────────────────────────────────────


class TestPollAndCollect:
    def test_skips_sleep_when_already_terminal(self):
        """If the batch is already 'ended' on first poll (typical for
        small synthetic batches in tests + fast prod batches), we
        must NOT sleep — that would be wasted wall-clock."""
        fake_sdk = MagicMock()
        fake_sdk.messages.batches.retrieve.return_value = _FakeBatch(state="ended")
        fake_sdk.messages.batches.results.return_value = [_make_succeeded_entry("a", "hello")]

        c = BatchClient(api_key="x")
        c._client = fake_sdk
        with patch("genlab_core.llm.batch.time.sleep") as sleep_mock:
            results = c.wait_and_collect("batch_001", poll_interval_s=999)

        sleep_mock.assert_not_called()
        assert results == {"a": "hello"}

    def test_polls_until_terminal(self):
        fake_sdk = MagicMock()
        # Three retrieve calls: in_progress, in_progress, ended
        fake_sdk.messages.batches.retrieve.side_effect = [
            _FakeBatch(state="in_progress"),
            _FakeBatch(state="in_progress"),
            _FakeBatch(state="ended"),
        ]
        fake_sdk.messages.batches.results.return_value = [_make_succeeded_entry("a", "done")]

        c = BatchClient(api_key="x")
        c._client = fake_sdk
        with patch("genlab_core.llm.batch.time.sleep") as sleep_mock:
            results = c.wait_and_collect("b", poll_interval_s=5)

        assert sleep_mock.call_count == 2  # two pre-terminal polls
        assert results == {"a": "done"}

    def test_timeout_raises_if_never_terminal(self):
        fake_sdk = MagicMock()
        fake_sdk.messages.batches.retrieve.return_value = _FakeBatch(state="in_progress")

        c = BatchClient(api_key="x")
        c._client = fake_sdk
        # max_wait=0.01 ensures even one sleep cycle exhausts the deadline
        with (
            patch("genlab_core.llm.batch.time.sleep"),
            patch("genlab_core.llm.batch.time.monotonic", side_effect=[0.0, 0.5, 1.0, 2.0]),
        ):
            with pytest.raises(TimeoutError, match="did not reach terminal state"):
                c.wait_and_collect("b", poll_interval_s=0.1, max_wait_s=1.0)


# ── Per-item result types ────────────────────────────────────────────


class TestResultParsing:
    def _client_returning(self, entries: list[_FakeResultEntry]) -> BatchClient:
        fake_sdk = MagicMock()
        fake_sdk.messages.batches.retrieve.return_value = _FakeBatch(state="ended")
        fake_sdk.messages.batches.results.return_value = entries
        c = BatchClient(api_key="x")
        c._client = fake_sdk
        return c

    def test_succeeded_returns_text(self):
        c = self._client_returning([_make_succeeded_entry("a", "hello world")])
        results = c.wait_and_collect("b")
        assert results == {"a": "hello world"}

    def test_errored_returns_batch_item_error(self):
        c = self._client_returning(
            [_make_errored_entry("a", "rate_limit_error", "too many requests")]
        )
        results = c.wait_and_collect("b")
        assert isinstance(results["a"], BatchItemError)
        assert results["a"].error_type == "rate_limit_error"
        assert results["a"].message == "too many requests"
        assert results["a"].custom_id == "a"

    def test_canceled_returns_batch_item_error(self):
        c = self._client_returning(
            [_FakeResultEntry(custom_id="a", result=_FakeResult(type="canceled"))]
        )
        results = c.wait_and_collect("b")
        assert isinstance(results["a"], BatchItemError)
        assert results["a"].error_type == "canceled"

    def test_expired_returns_batch_item_error(self):
        c = self._client_returning(
            [_FakeResultEntry(custom_id="a", result=_FakeResult(type="expired"))]
        )
        results = c.wait_and_collect("b")
        assert isinstance(results["a"], BatchItemError)
        assert results["a"].error_type == "expired"

    def test_mixed_success_and_errors_in_same_batch(self):
        """The whole-batch-succeeds-but-some-items-fail case — operators
        see per-item outcomes and can retry just the failures via
        realtime."""
        c = self._client_returning(
            [
                _make_succeeded_entry("a", "ok-a"),
                _make_errored_entry("b", "invalid_request_error", "bad prompt"),
                _make_succeeded_entry("c", "ok-c"),
            ]
        )
        results = c.wait_and_collect("b")
        assert results["a"] == "ok-a"
        assert isinstance(results["b"], BatchItemError)
        assert results["b"].error_type == "invalid_request_error"
        assert results["c"] == "ok-c"

    def test_malformed_succeeded_with_no_message_returns_error(self):
        """Defensive — if the API returns a 'succeeded' shape with
        no message field (SDK bug, API drift), don't crash; surface
        as a per-item error so the pipeline keeps going."""
        c = self._client_returning(
            [_FakeResultEntry(custom_id="a", result=_FakeResult(type="succeeded", message=None))]
        )
        results = c.wait_and_collect("b")
        assert isinstance(results["a"], BatchItemError)
        assert results["a"].error_type == "malformed_result"

    def test_cost_recorded_on_each_success(self):
        """Each per-item success must call record_anthropic_usage so
        the R-27 cost-tracking surface continues to work transparently."""
        c = self._client_returning(
            [_make_succeeded_entry("a", "x"), _make_succeeded_entry("b", "y")]
        )
        with patch(
            "genlab_core.intelligence.cost_accumulator.record_anthropic_usage"
        ) as record_mock:
            c.wait_and_collect("batch")
        assert record_mock.call_count == 2

    def test_cost_not_recorded_on_per_item_error(self):
        c = self._client_returning([_make_errored_entry("a", "rate_limit_error", "msg")])
        with patch(
            "genlab_core.intelligence.cost_accumulator.record_anthropic_usage"
        ) as record_mock:
            c.wait_and_collect("batch")
        record_mock.assert_not_called()

    def test_cost_recording_failure_does_not_break_other_items(self):
        """Cost recording is best-effort — if it crashes for one item,
        the other items still parse correctly."""
        c = self._client_returning(
            [_make_succeeded_entry("a", "ok-a"), _make_succeeded_entry("b", "ok-b")]
        )
        with patch(
            "genlab_core.intelligence.cost_accumulator.record_anthropic_usage",
            side_effect=RuntimeError("cost db down"),
        ):
            results = c.wait_and_collect("batch")
        # Both texts came through even though cost recording crashed
        assert results == {"a": "ok-a", "b": "ok-b"}


# ── End-to-end run() lifecycle ───────────────────────────────────────


class TestRunLifecycle:
    def test_run_submits_then_collects(self):
        fake_sdk = MagicMock()
        fake_sdk.messages.batches.create.return_value = MagicMock(id="batch_e2e")
        fake_sdk.messages.batches.retrieve.return_value = _FakeBatch(state="ended", id="batch_e2e")
        fake_sdk.messages.batches.results.return_value = [
            _make_succeeded_entry("x", "result-x"),
            _make_succeeded_entry("y", "result-y"),
        ]

        c = BatchClient(api_key="k")
        c._client = fake_sdk

        results = c.run(
            [
                BatchItem(custom_id="x", system="s", user="u"),
                BatchItem(custom_id="y", system="s", user="u"),
            ]
        )

        assert results == {"x": "result-x", "y": "result-y"}
        # Both submit + retrieve hit the same batch_id
        fake_sdk.messages.batches.create.assert_called_once()
        fake_sdk.messages.batches.retrieve.assert_called_with("batch_e2e")
        fake_sdk.messages.batches.results.assert_called_with("batch_e2e")
