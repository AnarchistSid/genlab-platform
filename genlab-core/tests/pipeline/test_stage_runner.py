"""Tests for genlab_core.pipeline.stage_runner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.pipeline.stage_runner import (
    LocalStageRunner,
    SandboxAwareStageRunner,
    StageResult,
    StageRunnerFactory,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


class FakeStage:
    """Minimal stage that records calls."""

    def __init__(self, *, mutate: dict | None = None, raise_exc: Exception | None = None):
        self._mutate = mutate
        self._raise = raise_exc
        self.called = False

    def execute(self, context: dict) -> dict:
        self.called = True
        if self._raise:
            raise self._raise
        if self._mutate:
            context.update(self._mutate)
        return context


class SandboxAwareStage:
    """Stage that has the _sandbox_runner attribute."""

    _sandbox_runner = None

    def __init__(self, *, raise_exc: Exception | None = None):
        self._raise = raise_exc
        self.executed_with_runner = None

    def execute(self, context: dict) -> dict:
        self.executed_with_runner = self._sandbox_runner
        if self._raise:
            raise self._raise
        return context


GENLAB_ROOT = Path("/fake/genlab")


# ── StageResult ──────────────────────────────────────────────────────────────


class TestStageResult:
    def test_frozen(self):
        r = StageResult(stage_name="X", success=True, elapsed_seconds=1.0)
        with pytest.raises(AttributeError):
            r.stage_name = "Y"  # type: ignore[misc]

    def test_defaults(self):
        r = StageResult(stage_name="A", success=False, elapsed_seconds=0.5)
        assert r.error is None


# ── LocalStageRunner ─────────────────────────────────────────────────────────


class TestLocalStageRunner:
    def test_success(self):
        stage = FakeStage(mutate={"key": "val"})
        ctx = {}
        pipeline_ctx = MagicMock()

        result = LocalStageRunner().run_stage(stage, ctx, pipeline_ctx)

        assert result.success is True
        assert result.stage_name == "FakeStage"
        assert result.elapsed_seconds >= 0
        assert result.error is None
        assert ctx["key"] == "val"
        assert stage.called

    def test_failure_records_error(self):
        exc = ValueError("boom")
        stage = FakeStage(raise_exc=exc)
        ctx = {}
        pipeline_ctx = MagicMock()

        result = LocalStageRunner().run_stage(stage, ctx, pipeline_ctx)

        assert result.success is False
        assert result.error is exc
        pipeline_ctx.record_error.assert_called_once_with("FakeStage", exc, fatal=False)

    def test_stage_returning_new_dict_merges(self):
        """Stages that return a new dict should have it merged back."""

        class ReturnsNewDict:
            def execute(self, context):
                return {"new_key": 42}

        ctx = {"existing": True}
        pipeline_ctx = MagicMock()

        result = LocalStageRunner().run_stage(ReturnsNewDict(), ctx, pipeline_ctx)

        assert result.success is True
        assert ctx["new_key"] == 42
        assert ctx["existing"] is True


# ── P6 fail_mode contract ────────────────────────────────────────────────────


class TestLocalStageRunnerFailMode:
    """P6 (2026-06-19): per-stage ``fail_mode`` policy from niche.yaml."""

    def test_default_fail_mode_is_continue(self):
        """Backward compat: default behavior records error non-fatally."""
        exc = ValueError("transient")
        stage = FakeStage(raise_exc=exc)
        pipeline_ctx = MagicMock()
        LocalStageRunner().run_stage(stage, {}, pipeline_ctx)
        # fatal=False matches every existing niche.yaml since the runner
        # shipped. Any change to this default is a major breaking change.
        pipeline_ctx.record_error.assert_called_once_with("FakeStage", exc, fatal=False)

    def test_fail_mode_continue_explicit(self):
        """Explicit ``continue`` matches the default — operator can declare
        intent in YAML without changing behavior."""
        exc = RuntimeError("api outage")
        stage = FakeStage(raise_exc=exc)
        pipeline_ctx = MagicMock()
        LocalStageRunner(fail_mode="continue").run_stage(stage, {}, pipeline_ctx)
        pipeline_ctx.record_error.assert_called_once_with("FakeStage", exc, fatal=False)

    def test_fail_mode_abort_marks_fatal(self):
        """``fail_mode=abort`` records the error with fatal=True so the
        next pre-stage ``ctx.is_aborted`` check stops the pipeline."""
        exc = RuntimeError("render failed catastrophically")
        stage = FakeStage(raise_exc=exc)
        pipeline_ctx = MagicMock()
        result = LocalStageRunner(fail_mode="abort").run_stage(stage, {}, pipeline_ctx)
        assert result.success is False
        pipeline_ctx.record_error.assert_called_once_with("FakeStage", exc, fatal=True)

    def test_abort_after_retries_exhausted(self):
        """Retry loop still runs first; abort kicks in only after the
        final retry fails."""
        exc = RuntimeError("flaky")
        stage = FakeStage(raise_exc=exc)
        pipeline_ctx = MagicMock()
        result = LocalStageRunner(max_retries=2, fail_mode="abort").run_stage(
            stage, {}, pipeline_ctx
        )
        assert result.success is False
        # record_error is called ONCE (after final retry), with fatal=True
        pipeline_ctx.record_error.assert_called_once_with("FakeStage", exc, fatal=True)

    def test_abort_does_not_fire_on_success(self):
        """Even with fail_mode=abort, a successful stage doesn't abort."""
        stage = FakeStage(mutate={"ran": True})
        pipeline_ctx = MagicMock()
        result = LocalStageRunner(fail_mode="abort").run_stage(stage, {}, pipeline_ctx)
        assert result.success is True
        pipeline_ctx.record_error.assert_not_called()

    def test_invalid_fail_mode_raises_at_construction(self):
        """Typo in YAML should surface at startup, not at runtime."""
        with pytest.raises(ValueError, match="fail_mode must be one of"):
            LocalStageRunner(fail_mode="continue_on_error")  # close but wrong

    def test_factory_reads_fail_mode_from_declaration(self):
        """End-to-end: niche.yaml's ``fail_mode:`` reaches the runner."""
        factory = StageRunnerFactory(genlab_root=GENLAB_ROOT)
        runner = factory.get_runner({"class": "X", "fail_mode": "abort"})
        # The factory returns a non-cached runner when fail_mode != continue
        assert isinstance(runner, LocalStageRunner)
        assert runner._fail_mode == "abort"

    def test_factory_uses_cached_local_for_default_fail_mode(self):
        """No regression: when fail_mode is default + no retries, the
        cached _local runner is returned (no per-stage allocation)."""
        factory = StageRunnerFactory(genlab_root=GENLAB_ROOT)
        runner = factory.get_runner({"class": "X"})
        # _local is the cached default-fail_mode no-retry runner
        assert runner is factory._local


# ── SandboxAwareStageRunner ──────────────────────────────────────────────────


class TestSandboxAwareStageRunner:
    def test_falls_back_to_local_when_sandbox_disabled(self):
        stage = FakeStage(mutate={"ran": True})
        ctx = {}
        pipeline_ctx = MagicMock()

        with patch("genlab_core.pipeline.stage_runner.SandboxAwareStageRunner.run_stage"):
            # Instead, test the actual fallback path
            pass

        # Test by calling real method with sandbox disabled
        with patch(
            "genlab_core.media.sandbox_runner.sandbox_rendering_enabled",
            return_value=False,
        ):
            runner = SandboxAwareStageRunner(genlab_root=GENLAB_ROOT)
            result = runner.run_stage(stage, ctx, pipeline_ctx)

        assert result.success is True
        assert ctx.get("ran") is True

    @patch("genlab_core.media.sandbox_runner.sandbox_rendering_enabled", return_value=True)
    def test_injects_runner_into_stage(self, _mock_enabled):
        mock_sbx_runner = MagicMock()
        mock_sbx_runner.__enter__ = MagicMock(return_value=mock_sbx_runner)
        mock_sbx_runner.__exit__ = MagicMock(return_value=False)

        stage = SandboxAwareStage()
        ctx = {}
        pipeline_ctx = MagicMock()

        with patch(
            "genlab_core.media.sandbox_runner.SandboxedFFmpegRunner",
            return_value=mock_sbx_runner,
        ):
            runner = SandboxAwareStageRunner(genlab_root=GENLAB_ROOT)
            result = runner.run_stage(stage, ctx, pipeline_ctx)

        assert result.success is True
        assert stage.executed_with_runner is mock_sbx_runner
        # Runner should be cleaned up
        mock_sbx_runner.__enter__.assert_called_once()
        mock_sbx_runner.__exit__.assert_called_once_with(None, None, None)
        # Attribute should be cleared
        assert stage._sandbox_runner is None

    @patch("genlab_core.media.sandbox_runner.sandbox_rendering_enabled", return_value=True)
    def test_cleans_up_on_stage_failure(self, _mock_enabled):
        mock_sbx_runner = MagicMock()
        mock_sbx_runner.__enter__ = MagicMock(return_value=mock_sbx_runner)
        mock_sbx_runner.__exit__ = MagicMock(return_value=False)

        exc = RuntimeError("render failed")
        stage = SandboxAwareStage(raise_exc=exc)
        ctx = {}
        pipeline_ctx = MagicMock()

        with patch(
            "genlab_core.media.sandbox_runner.SandboxedFFmpegRunner",
            return_value=mock_sbx_runner,
        ):
            runner = SandboxAwareStageRunner(genlab_root=GENLAB_ROOT)
            result = runner.run_stage(stage, ctx, pipeline_ctx)

        assert result.success is False
        assert result.error is exc
        # Sandbox still cleaned up
        mock_sbx_runner.__exit__.assert_called_once()
        pipeline_ctx.record_error.assert_called_once()

    def test_egress_allow_passed_to_ffmpeg_runner(self):
        with (
            patch(
                "genlab_core.media.sandbox_runner.sandbox_rendering_enabled",
                return_value=True,
            ),
            patch(
                "genlab_core.media.sandbox_runner.SandboxedFFmpegRunner",
            ) as MockRunner,
        ):
            mock_instance = MagicMock()
            mock_instance.__enter__ = MagicMock(return_value=mock_instance)
            mock_instance.__exit__ = MagicMock(return_value=False)
            MockRunner.return_value = mock_instance

            stage = SandboxAwareStage()
            ctx = {}
            pipeline_ctx = MagicMock()

            runner = SandboxAwareStageRunner(
                genlab_root=GENLAB_ROOT,
                egress_allow=["api.twitch.tv"],
            )
            runner.run_stage(stage, ctx, pipeline_ctx)

            MockRunner.assert_called_once_with(
                genlab_root=GENLAB_ROOT,
                egress_allow=["api.twitch.tv"],
            )


# ── P6 fail_mode contract for SandboxAwareStageRunner ───────────────────────


class TestSandboxAwareStageRunnerFailMode:
    """P6 broader adoption (2026-06-19): the sandbox runner must also honor
    fail_mode=abort. Previously hardcoded fatal=False — silently no-op'd
    fail_mode on any sandboxed stage (notably RenderGamingVideo)."""

    def test_default_fail_mode_is_continue(self):
        """Backward compat: default sandbox-runner behavior is unchanged."""
        runner = SandboxAwareStageRunner(genlab_root=GENLAB_ROOT)
        assert runner._fail_mode == "continue"

    def test_fail_mode_abort_records_fatal_when_sandbox_enabled(self):
        """When sandbox is enabled AND stage raises, fail_mode=abort
        promotes the error to fatal."""
        exc = RuntimeError("ffmpeg sigsegv'd")
        stage = SandboxAwareStage(raise_exc=exc)
        pipeline_ctx = MagicMock()
        runner = SandboxAwareStageRunner(genlab_root=GENLAB_ROOT, fail_mode="abort")
        with (
            patch(
                "genlab_core.media.sandbox_runner.sandbox_rendering_enabled",
                return_value=True,
            ),
            patch("genlab_core.media.sandbox_runner.SandboxedFFmpegRunner"),
        ):
            result = runner.run_stage(stage, {}, pipeline_ctx)
        assert result.success is False
        pipeline_ctx.record_error.assert_called_once_with("SandboxAwareStage", exc, fatal=True)

    def test_fail_mode_abort_forwards_to_local_fallback(self):
        """When sandbox is disabled (fallback path), the LocalStageRunner
        must also receive fail_mode=abort — otherwise the gating policy is
        silently dropped when the sandbox feature flag is off."""
        exc = RuntimeError("render exploded")
        stage = SandboxAwareStage(raise_exc=exc)
        pipeline_ctx = MagicMock()
        runner = SandboxAwareStageRunner(genlab_root=GENLAB_ROOT, fail_mode="abort")
        with patch(
            "genlab_core.media.sandbox_runner.sandbox_rendering_enabled",
            return_value=False,
        ):
            result = runner.run_stage(stage, {}, pipeline_ctx)
        assert result.success is False
        # The local fallback runner records with the forwarded fail_mode
        pipeline_ctx.record_error.assert_called_once_with("SandboxAwareStage", exc, fatal=True)

    def test_invalid_fail_mode_raises_at_construction(self):
        """Typo in YAML should surface at startup."""
        with pytest.raises(ValueError, match="fail_mode must be one of"):
            SandboxAwareStageRunner(genlab_root=GENLAB_ROOT, fail_mode="abort_pipeline")

    def test_factory_forwards_fail_mode_to_sandbox_runner(self):
        """End-to-end: niche.yaml's fail_mode on a sandbox: true stage
        reaches SandboxAwareStageRunner via the factory."""
        factory = StageRunnerFactory(genlab_root=GENLAB_ROOT)
        runner = factory.get_runner({"class": "X", "sandbox": True, "fail_mode": "abort"})
        assert isinstance(runner, SandboxAwareStageRunner)
        assert runner._fail_mode == "abort"

    def test_factory_forwards_fail_mode_for_dict_sandbox_cfg(self):
        """The egress-allowlist sandbox shape also forwards fail_mode."""
        factory = StageRunnerFactory(genlab_root=GENLAB_ROOT)
        runner = factory.get_runner(
            {"class": "X", "sandbox": {"egress": ["api.example.com"]}, "fail_mode": "abort"}
        )
        assert isinstance(runner, SandboxAwareStageRunner)
        assert runner._fail_mode == "abort"


# ── StageRunnerFactory ───────────────────────────────────────────────────────


class TestStageRunnerFactory:
    def setup_method(self):
        self.factory = StageRunnerFactory(genlab_root=GENLAB_ROOT)

    def test_no_sandbox_returns_local(self):
        runner = self.factory.get_runner({"class": "some.Stage"})
        assert isinstance(runner, LocalStageRunner)

    def test_sandbox_false_returns_local(self):
        runner = self.factory.get_runner({"class": "some.Stage", "sandbox": False})
        assert isinstance(runner, LocalStageRunner)

    def test_sandbox_true_returns_sandbox_aware(self):
        runner = self.factory.get_runner({"class": "some.Stage", "sandbox": True})
        assert isinstance(runner, SandboxAwareStageRunner)

    def test_sandbox_dict_with_egress(self):
        runner = self.factory.get_runner(
            {
                "class": "some.Stage",
                "sandbox": {"egress": ["api.twitch.tv", "store.steampowered.com"]},
            }
        )
        assert isinstance(runner, SandboxAwareStageRunner)
        assert runner._egress_allow == ["api.twitch.tv", "store.steampowered.com"]

    def test_sandbox_dict_empty_egress(self):
        runner = self.factory.get_runner(
            {
                "class": "some.Stage",
                "sandbox": {"egress": []},
            }
        )
        assert isinstance(runner, SandboxAwareStageRunner)

    def test_local_runner_reused(self):
        """Factory should reuse the same LocalStageRunner instance."""
        r1 = self.factory.get_runner({"class": "A"})
        r2 = self.factory.get_runner({"class": "B"})
        assert r1 is r2

    def test_run_sets_stage_filter(self):
        """factory.run() should set/clear stage name on the log filter."""
        from genlab_core.pipeline.log_streamer import StageLoggingFilter

        filt = StageLoggingFilter()
        factory = StageRunnerFactory(genlab_root=GENLAB_ROOT, stage_log_filter=filt)

        FakeStage()
        pipeline_ctx = MagicMock()

        # During execution, filter should have the stage name
        captured_stages = []

        class CapturingStage:
            def execute(self, context):
                captured_stages.append(filt.current_stage)
                return context

        result = factory.run(
            {"class": "test.CapturingStage"},
            CapturingStage(),
            {},
            pipeline_ctx,
        )
        assert result.success is True
        assert captured_stages == ["CapturingStage"]
        # After execution, filter should be cleared
        assert filt.current_stage is None

    def test_run_clears_filter_on_error(self):
        """factory.run() clears filter even when stage raises."""
        from genlab_core.pipeline.log_streamer import StageLoggingFilter

        filt = StageLoggingFilter()
        factory = StageRunnerFactory(genlab_root=GENLAB_ROOT, stage_log_filter=filt)

        stage = FakeStage(raise_exc=ValueError("boom"))
        pipeline_ctx = MagicMock()

        result = factory.run({"class": "test.FakeStage"}, stage, {}, pipeline_ctx)
        assert result.success is False
        assert filt.current_stage is None


# ── Parallel execution ───────────────────────────────────────────────────────


class TestRunParallel:
    def test_parallel_runs_concurrently(self):
        """Stages in a batch should execute on threads (via pool)."""
        import threading

        # Use a barrier to prove true concurrency — both must reach it
        # before either can proceed.
        barrier = threading.Barrier(2, timeout=5)
        reached = []

        class BarrierStage:
            def __init__(self, name):
                self._name = name

            def execute(self, context):
                barrier.wait()  # blocks until both threads arrive
                reached.append(self._name)
                return context

        factory = StageRunnerFactory(genlab_root=GENLAB_ROOT)
        pipeline_ctx = MagicMock()

        batch = [
            ({"class": "A"}, BarrierStage("A")),
            ({"class": "B"}, BarrierStage("B")),
        ]

        results = factory.run_parallel(batch, {}, pipeline_ctx)

        assert len(results) == 2
        assert all(r.success for r in results)
        # Both must have passed the barrier (proves concurrent)
        assert set(reached) == {"A", "B"}

    def test_parallel_results_ordered(self):
        """Results should be in the same order as the batch."""
        import time as _t

        class SlowStage:
            def execute(self, context):
                _t.sleep(0.05)
                context["slow"] = True
                return context

        class FastStage:
            def execute(self, context):
                context["fast"] = True
                return context

        factory = StageRunnerFactory(genlab_root=GENLAB_ROOT)
        pipeline_ctx = MagicMock()

        batch = [
            ({"class": "A"}, SlowStage()),
            ({"class": "B"}, FastStage()),
        ]

        results = factory.run_parallel(batch, {}, pipeline_ctx)

        assert results[0].stage_name == "SlowStage"
        assert results[1].stage_name == "FastStage"

    def test_parallel_one_failure_doesnt_block_others(self):
        """A failing stage shouldn't prevent other stages from completing."""
        factory = StageRunnerFactory(genlab_root=GENLAB_ROOT)
        pipeline_ctx = MagicMock()

        batch = [
            ({"class": "A"}, FakeStage(raise_exc=ValueError("boom"))),
            ({"class": "B"}, FakeStage(mutate={"ok": True})),
        ]
        ctx = {}

        results = factory.run_parallel(batch, ctx, pipeline_ctx)

        assert results[0].success is False
        assert results[1].success is True
        assert ctx.get("ok") is True

    def test_parallel_non_overlapping_writes(self):
        """Stages writing to different keys should not conflict."""

        class AudioStage:
            def execute(self, context):
                for s in context.get("stories", []):
                    s["audio_path"] = "/audio.wav"
                return context

        class CaptionStage:
            def execute(self, context):
                for s in context.get("stories", []):
                    s["rendered_path"] = "/captioned.mp4"
                return context

        factory = StageRunnerFactory(genlab_root=GENLAB_ROOT)
        pipeline_ctx = MagicMock()

        stories = [{"title": "A"}, {"title": "B"}]
        ctx = {"stories": stories}

        batch = [
            ({"class": "audio.AudioStage"}, AudioStage()),
            ({"class": "caption.CaptionStage"}, CaptionStage()),
        ]

        results = factory.run_parallel(batch, ctx, pipeline_ctx)

        assert all(r.success for r in results)
        for s in stories:
            assert s["audio_path"] == "/audio.wav"
            assert s["rendered_path"] == "/captioned.mp4"

    def test_parallel_wall_clock_faster_than_sequential(self):
        """Parallel execution should be faster than sum of individual times."""
        import time as _t

        class SleepStage:
            def __init__(self, duration: float):
                self._duration = duration

            def execute(self, context):
                _t.sleep(self._duration)
                return context

        factory = StageRunnerFactory(genlab_root=GENLAB_ROOT)
        pipeline_ctx = MagicMock()

        batch = [
            ({"class": "A"}, SleepStage(0.1)),
            ({"class": "B"}, SleepStage(0.1)),
        ]

        t0 = _t.monotonic()
        results = factory.run_parallel(batch, {}, pipeline_ctx)
        wall_clock = _t.monotonic() - t0

        assert all(r.success for r in results)
        # Sequential would take ~0.2s; parallel should be ~0.1s
        assert wall_clock < 0.18  # generous margin
