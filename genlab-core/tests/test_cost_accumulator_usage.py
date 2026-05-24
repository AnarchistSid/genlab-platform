"""U-03: record_anthropic_usage feeds every Anthropic call site's token usage
into the active cost accumulator (the R-27 "cost unmeasured" gap), and is a
safe no-op when there's no accumulator / no usage / a broken accumulator.
"""

from __future__ import annotations

from types import SimpleNamespace

from genlab_core.intelligence.cost_accumulator import (
    CostAccumulator,
    record_anthropic_usage,
    reset_accumulator,
    set_accumulator,
)


def _resp(inp: int, out: int) -> SimpleNamespace:
    return SimpleNamespace(usage=SimpleNamespace(input_tokens=inp, output_tokens=out))


def test_records_usage_when_accumulator_active() -> None:
    acc = CostAccumulator(run_id="u03")
    token = set_accumulator(acc)
    try:
        record_anthropic_usage("claude-haiku-4-5-20251001", _resp(1000, 200))
    finally:
        reset_accumulator(token)

    assert len(acc.entries) == 1
    assert acc.entries[0].model == "claude-haiku-4-5-20251001"
    assert acc.entries[0].input_tokens == 1000
    assert acc.entries[0].output_tokens == 200


def test_noop_when_no_accumulator() -> None:
    # No accumulator active in this context — must not raise.
    record_anthropic_usage("claude-haiku-4-5-20251001", _resp(10, 5))


def test_noop_when_response_has_no_usage() -> None:
    acc = CostAccumulator(run_id="u03-nousage")
    token = set_accumulator(acc)
    try:
        record_anthropic_usage("m", SimpleNamespace())  # no .usage attribute
    finally:
        reset_accumulator(token)
    assert acc.entries == []


def test_never_raises_on_accumulator_error() -> None:
    class _Boom:
        def record_llm(self, **kwargs):
            raise RuntimeError("boom")

    token = set_accumulator(_Boom())  # type: ignore[arg-type]
    try:
        record_anthropic_usage("m", _resp(1, 1))  # must swallow the error
    finally:
        reset_accumulator(token)
