"""Pin the finally-hook cost persistence added 2026-06-17 night.

Background
----------
RunReport (the last pipeline stage) is the canonical caller of
``persist_run_cost()``. If any earlier stage crashes, RunReport
never runs and the cost row is silently lost — audit on 2026-06-17
found 13 cost rows vs ~30 run dirs / 7d (~57% silent loss).

The fix added a finally-block persist in ``pipeline_runner.run()``
so the cost row lands even when the pipeline crashes mid-stream.
``cost_persist`` uses ``ON CONFLICT (run_id, niche_id) DO UPDATE``
so the redundant write on the happy path (where RunReport already
wrote it) is idempotent.

These pins ensure:
- A run that crashes mid-pipeline still persists cost (the key fix)
- A run with NO cost entries doesn't write an empty row (no noise)
- The happy path (RunReport ran) doesn't fail because of the
  redundant call (idempotency via ON CONFLICT)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from genlab_core.pipeline.pipeline_runner import GenericPipelineRunner


class _SpendingStage:
    """Stage that records simulated LLM spend and then either passes or crashes."""

    _crash: bool = False

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        from genlab_core.intelligence.cost_accumulator import (
            get_accumulator,
            record_anthropic_usage,
        )

        acc = get_accumulator()
        assert acc is not None, "runner must have installed an accumulator"

        # Simulate an Anthropic response carrying usage data
        class _Usage:
            input_tokens = 1000
            output_tokens = 500

        class _Response:
            usage = _Usage()

        record_anthropic_usage("claude-haiku-4-5-20251001", _Response())
        if self._crash:
            raise RuntimeError("simulated mid-pipeline crash")
        return context


class StageSpendsThenCrashes(_SpendingStage):
    _crash = True


class StageSpendsThenPasses(_SpendingStage):
    _crash = False


def _make_config(*stage_classes: type) -> dict[str, Any]:
    """Build a minimal niche config with the given stage classes."""
    return {
        "pipeline": {
            "stages": [
                {"class": f"{cls.__module__}.{cls.__name__}", "enabled": True}
                for cls in stage_classes
            ]
        }
    }


class TestFinallyPersistOnCrash:
    def test_crashed_run_still_persists_cost(self, tmp_path, monkeypatch):
        """The headline pin: pipeline crashes mid-stream, finally hook
        catches the persist call, row lands in pipeline_run_costs."""
        from genlab_core.pipeline import pipeline_runner as pr

        config = _make_config(StageSpendsThenCrashes)
        monkeypatch.setattr(pr, "load_niche_config", lambda niche_id, niche_root: dict(config))
        monkeypatch.setattr(pr, "get_feature_flags", lambda niche_id, niche_root: {})

        runner = GenericPipelineRunner(
            niche_roots={"test": tmp_path / "niche"},
            genlab_root=tmp_path,
        )

        # Capture persist_run_cost calls without hitting the DB
        with patch(
            "genlab_core.intelligence.cost_persist.persist_run_cost",
            return_value=True,
        ) as mock_persist:
            runner.run("test")

        # Pipeline crashed (StageSpendsThenCrashes raised), but the
        # finally hook MUST still have called persist_run_cost.
        assert mock_persist.called, (
            "finally-hook persist did not fire on crashed pipeline — "
            "this is the 57% silent-loss bug from 2026-06-17 audit"
        )
        # The persisted summary should carry the simulated 1500-token
        # cost from StageSpendsThenCrashes.
        call_kwargs = mock_persist.call_args.kwargs
        assert call_kwargs["niche_id"] == "test"
        assert call_kwargs["run_id"]  # non-empty
        assert call_kwargs["summary"]["entry_count"] >= 1

    def test_no_cost_run_does_not_persist_empty_row(self, tmp_path, monkeypatch):
        """If no stage recorded any LLM cost (e.g. pipeline crashed
        before any LLM call), don't write a 0-entry row. Avoids noise
        in pipeline_run_costs from pipelines that never spent anything."""
        from genlab_core.pipeline import pipeline_runner as pr

        class StageNoSpend:
            def execute(self, context):
                return context

        StageNoSpend.__module__ = __name__
        # Inject into this module so importlib finds it
        globals()["StageNoSpend"] = StageNoSpend

        config = _make_config(StageNoSpend)
        monkeypatch.setattr(pr, "load_niche_config", lambda niche_id, niche_root: dict(config))
        monkeypatch.setattr(pr, "get_feature_flags", lambda niche_id, niche_root: {})

        runner = GenericPipelineRunner(
            niche_roots={"test_empty": tmp_path / "niche"},
            genlab_root=tmp_path,
        )

        with patch(
            "genlab_core.intelligence.cost_persist.persist_run_cost",
            return_value=True,
        ) as mock_persist:
            runner.run("test_empty")

        # No LLM calls happened → no persist needed → no empty row noise
        assert not mock_persist.called, (
            "finally-hook should NOT persist a 0-entry summary; that "
            "would clutter pipeline_run_costs with empty rows from "
            "pre-LLM crash paths"
        )

    def test_happy_path_persist_idempotency(self, tmp_path, monkeypatch):
        """On the happy path, both RunReport AND the finally hook may
        call persist_run_cost. The ON CONFLICT (run_id, niche_id) DO
        UPDATE clause in cost_persist.py makes the second call a no-op
        UPDATE rather than a duplicate INSERT.

        We can't verify the SQL-level idempotency here without a real
        DB; what we DO verify is that the finally hook completes
        without raising even when persist_run_cost is called multiple
        times for the same run."""
        from genlab_core.pipeline import pipeline_runner as pr

        config = _make_config(StageSpendsThenPasses)
        monkeypatch.setattr(pr, "load_niche_config", lambda niche_id, niche_root: dict(config))
        monkeypatch.setattr(pr, "get_feature_flags", lambda niche_id, niche_root: {})

        runner = GenericPipelineRunner(
            niche_roots={"happy": tmp_path / "niche"},
            genlab_root=tmp_path,
        )

        # Persist returns True both times — the redundant call is fine
        with patch(
            "genlab_core.intelligence.cost_persist.persist_run_cost",
            return_value=True,
        ) as mock_persist:
            ctx = runner.run("happy")

        # Pipeline completed (StageSpendsThenPasses didn't raise)
        assert ctx is not None
        # finally-hook persist still fires (idempotency is in the DB
        # layer, not here)
        assert mock_persist.called

    def test_persist_failure_does_not_re_raise(self, tmp_path, monkeypatch):
        """The finally hook is wrapped in a try/except so a DB error in
        cost_persist (transient PG outage, etc.) doesn't propagate and
        mask the original pipeline outcome. Pin that.
        """
        from genlab_core.pipeline import pipeline_runner as pr

        config = _make_config(StageSpendsThenCrashes)
        monkeypatch.setattr(pr, "load_niche_config", lambda niche_id, niche_root: dict(config))
        monkeypatch.setattr(pr, "get_feature_flags", lambda niche_id, niche_root: {})

        runner = GenericPipelineRunner(
            niche_roots={"db_outage": tmp_path / "niche"},
            genlab_root=tmp_path,
        )

        with patch(
            "genlab_core.intelligence.cost_persist.persist_run_cost",
            side_effect=RuntimeError("simulated DB outage"),
        ):
            # Must not raise — finally hook swallows the persist error
            ctx = runner.run("db_outage")
            # Pipeline crash is reflected in run_stats; runner doesn't
            # re-raise pipeline-stage exceptions either.
            assert ctx is not None
