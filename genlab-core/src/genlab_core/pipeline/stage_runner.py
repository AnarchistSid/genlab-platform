"""Stage execution wrappers for the GenLab pipeline.

Provides a StageRunner protocol and concrete implementations:
- LocalStageRunner: direct in-process execution (default)
- SandboxAwareStageRunner: injects SandboxedFFmpegRunner before execution

The runner wraps each stage with timing, structured logging, and error
recording to PipelineContext — replacing the bare try/except loop in
pipeline_runner.py.

Parallel execution:
    Stages that declare ``parallel_group: "group_name"`` in niche.yaml
    are batched by the pipeline runner and executed concurrently via
    ``StageRunnerFactory.run_parallel()``.

Usage in pipeline_runner.py:
    factory = StageRunnerFactory(genlab_root=GENLAB_ROOT)
    for stage, declaration in zip(stages, declarations):
        if ctx.is_aborted:
            break
        result = factory.get_runner(declaration).run_stage(
            stage, context_dict, ctx,
        )
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from genlab_core.observability.metrics_writer import PipelineMetrics

logger = logging.getLogger(__name__)

# Stages that support sandbox injection must have this attribute.
_SANDBOX_RUNNER_ATTR = "_sandbox_runner"


# ── Result dataclass ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StageResult:
    """Outcome of a single stage execution."""

    stage_name: str
    success: bool
    elapsed_seconds: float
    error: Exception | None = None


# ── Protocol ─────────────────────────────────────────────────────────────────


@runtime_checkable
class StageRunner(Protocol):
    """Protocol for stage execution wrappers."""

    def run_stage(
        self,
        stage: Any,
        context: dict[str, Any],
        pipeline_ctx: Any,
    ) -> StageResult:
        """Execute a stage, returning a StageResult.

        The ``context`` dict is mutated in-place by the stage.
        ``pipeline_ctx`` is the PipelineContext (for error recording).
        """
        ...


# ── Local runner ─────────────────────────────────────────────────────────────


class LocalStageRunner:
    """Run a stage directly in the current process.

    This is the default runner — equivalent to the original bare
    ``stage.execute(context)`` call but with timing and error recording.

    When a ``metrics`` instance is provided, each stage execution is
    automatically recorded with timing and status.
    """

    def __init__(self, *, metrics: PipelineMetrics | None = None) -> None:
        self._metrics = metrics

    def run_stage(
        self,
        stage: Any,
        context: dict[str, Any],
        pipeline_ctx: Any,
    ) -> StageResult:
        stage_name = stage.__class__.__name__
        logger.info("[Pipeline] Running stage: %s", stage_name)

        t0 = time.monotonic()
        try:
            result_ctx = stage.execute(context)
            # Stages should return the same dict, but some return a new one
            if result_ctx is not context:
                context.update(result_ctx)
            elapsed = time.monotonic() - t0
            logger.info(
                "[Pipeline] Stage %s completed in %.1fs", stage_name, elapsed,
            )
            if self._metrics is not None:
                self._metrics.record_stage(
                    stage_name, duration_ms=elapsed * 1000.0, status="ok",
                )
            return StageResult(
                stage_name=stage_name, success=True, elapsed_seconds=elapsed,
            )
        except Exception as e:
            elapsed = time.monotonic() - t0
            pipeline_ctx.record_error(stage_name, e, fatal=False)
            logger.error(
                "[Pipeline] Stage %s failed after %.1fs: %s",
                stage_name, elapsed, e,
            )
            if self._metrics is not None:
                self._metrics.record_stage(
                    stage_name,
                    duration_ms=elapsed * 1000.0,
                    status="error",
                    error_msg=str(e),
                )
            return StageResult(
                stage_name=stage_name,
                success=False,
                elapsed_seconds=elapsed,
                error=e,
            )


# ── Sandbox-aware runner ─────────────────────────────────────────────────────


class SandboxAwareStageRunner:
    """Run a stage with a SandboxedFFmpegRunner injected.

    Creates a sandbox before the stage runs and tears it down afterward.
    The sandbox runner is set on the stage via ``_sandbox_runner`` attribute
    (stages that support it, like RenderGamingVideo, read this in their
    lazy-init methods).

    This runner also accepts an egress allowlist, defaulting to deny-all
    for render stages.
    """

    def __init__(
        self,
        genlab_root: Path,
        *,
        egress_allow: list[str] | None = None,
        metrics: PipelineMetrics | None = None,
    ) -> None:
        self._genlab_root = genlab_root
        self._egress_allow = egress_allow
        self._metrics = metrics

    def run_stage(
        self,
        stage: Any,
        context: dict[str, Any],
        pipeline_ctx: Any,
    ) -> StageResult:
        from genlab_core.media.sandbox_runner import (
            SandboxedFFmpegRunner,
            sandbox_rendering_enabled,
        )

        stage_name = stage.__class__.__name__

        # If sandbox rendering isn't enabled, fall back to local
        if not sandbox_rendering_enabled():
            logger.info(
                "[Pipeline] Sandbox not enabled — running %s locally",
                stage_name,
            )
            return LocalStageRunner(metrics=self._metrics).run_stage(
                stage, context, pipeline_ctx,
            )

        logger.info("[Pipeline] Running stage: %s (sandboxed)", stage_name)
        runner = SandboxedFFmpegRunner(
            genlab_root=self._genlab_root,
            egress_allow=self._egress_allow,
        )
        runner.__enter__()

        t0 = time.monotonic()
        try:
            # Inject the sandbox runner into the stage if it supports it
            if hasattr(stage, _SANDBOX_RUNNER_ATTR):
                setattr(stage, _SANDBOX_RUNNER_ATTR, runner)

            result_ctx = stage.execute(context)
            if result_ctx is not context:
                context.update(result_ctx)

            elapsed = time.monotonic() - t0
            logger.info(
                "[Pipeline] Stage %s completed in %.1fs (sandboxed)",
                stage_name, elapsed,
            )
            if self._metrics is not None:
                self._metrics.record_stage(
                    stage_name, duration_ms=elapsed * 1000.0, status="ok",
                )
            return StageResult(
                stage_name=stage_name, success=True, elapsed_seconds=elapsed,
            )
        except Exception as e:
            elapsed = time.monotonic() - t0
            pipeline_ctx.record_error(stage_name, e, fatal=False)
            logger.error(
                "[Pipeline] Stage %s failed after %.1fs (sandboxed): %s",
                stage_name, elapsed, e,
            )
            if self._metrics is not None:
                self._metrics.record_stage(
                    stage_name,
                    duration_ms=elapsed * 1000.0,
                    status="error",
                    error_msg=str(e),
                )
            return StageResult(
                stage_name=stage_name,
                success=False,
                elapsed_seconds=elapsed,
                error=e,
            )
        finally:
            runner.__exit__(None, None, None)
            # Clear injection so the stage doesn't hold a dead runner
            if hasattr(stage, _SANDBOX_RUNNER_ATTR):
                setattr(stage, _SANDBOX_RUNNER_ATTR, None)
            logger.info("[Pipeline] Sandbox cleaned up for %s", stage_name)


# ── Factory ──────────────────────────────────────────────────────────────────


class StageRunnerFactory:
    """Select the appropriate StageRunner for a stage declaration.

    Reads the ``sandbox`` field from the stage declaration dict:
    - ``sandbox: true`` → SandboxAwareStageRunner (deny-all egress)
    - ``sandbox: {egress: [domains]}`` → SandboxAwareStageRunner with allowlist
    - absent / ``sandbox: false`` → LocalStageRunner

    When a ``stage_log_filter`` is provided, the factory sets the
    current stage name on the filter before execution and clears it
    after — so log records emitted during stage execution carry the
    ``stage`` field.

    Example niche.yaml declarations:
        - class: stages.render_video.RenderVideo
          sandbox: true

        - class: stages.fetch_stories.FetchStories
          sandbox:
            egress:
              - api.twitch.tv
              - store.steampowered.com
    """

    def __init__(
        self,
        genlab_root: Path,
        *,
        stage_log_filter: Any | None = None,
        metrics: PipelineMetrics | None = None,
    ) -> None:
        self._genlab_root = genlab_root
        self._metrics = metrics
        self._local = LocalStageRunner(metrics=metrics)
        self._stage_log_filter = stage_log_filter

    def run(
        self,
        declaration: dict[str, Any],
        stage: Any,
        context: dict[str, Any],
        pipeline_ctx: Any,
    ) -> StageResult:
        """Select runner, set log filter stage, execute, clear filter."""
        stage_name = stage.__class__.__name__
        if self._stage_log_filter is not None:
            self._stage_log_filter.current_stage = stage_name
        try:
            runner = self.get_runner(declaration)
            return runner.run_stage(stage, context, pipeline_ctx)
        finally:
            if self._stage_log_filter is not None:
                self._stage_log_filter.current_stage = None

    def run_parallel(
        self,
        batch: list[tuple[dict[str, Any], Any]],
        context: dict[str, Any],
        pipeline_ctx: Any,
    ) -> list[StageResult]:
        """Execute a batch of stages concurrently.

        Each entry in *batch* is a ``(declaration, stage_instance)`` pair.
        All stages share the same *context* dict — they must write to
        non-overlapping keys to avoid races.

        The log filter cycles through stage names as futures complete
        (not during execution, since threads run concurrently).

        Returns a list of StageResult in the same order as *batch*.
        """
        names = [s.__class__.__name__ for _, s in batch]
        logger.info(
            "[Pipeline] Running %d stages in parallel: %s", len(batch), names,
        )

        t0 = time.monotonic()
        results: dict[int, StageResult] = {}

        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            future_to_idx = {}
            for idx, (decl, stage) in enumerate(batch):
                runner = self.get_runner(decl)
                future = pool.submit(runner.run_stage, stage, context, pipeline_ctx)
                future_to_idx[future] = idx

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                stage_name = batch[idx][1].__class__.__name__
                try:
                    result = future.result()
                except Exception as exc:
                    # Should not happen — runners catch internally
                    result = StageResult(
                        stage_name=stage_name,
                        success=False,
                        elapsed_seconds=time.monotonic() - t0,
                        error=exc,
                    )
                results[idx] = result

                status = "OK" if result.success else "FAIL"
                logger.info(
                    "[Pipeline] Parallel stage %s finished (%s, %.1fs)",
                    stage_name, status, result.elapsed_seconds,
                )

        elapsed = time.monotonic() - t0
        logger.info(
            "[Pipeline] Parallel group completed in %.1fs (wall-clock)", elapsed,
        )

        return [results[i] for i in range(len(batch))]

    def get_runner(self, declaration: dict[str, Any]) -> StageRunner:
        """Return the StageRunner for a given stage declaration."""
        sandbox_cfg = declaration.get("sandbox")

        if not sandbox_cfg:
            return self._local

        if sandbox_cfg is True:
            # Simple flag — deny-all egress (render stages)
            return SandboxAwareStageRunner(
                genlab_root=self._genlab_root, metrics=self._metrics,
            )

        if isinstance(sandbox_cfg, dict):
            egress = sandbox_cfg.get("egress", [])
            return SandboxAwareStageRunner(
                genlab_root=self._genlab_root,
                egress_allow=egress,
                metrics=self._metrics,
            )

        return self._local
