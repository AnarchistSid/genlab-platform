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


@runtime_checkable
class Stage(Protocol):
    """Protocol every pipeline stage class must implement.

    Used by ``GenericPipelineRunner._load_stages`` to fail-fast at config
    load time when a niche.yaml entry points at a class that doesn't
    implement ``execute(context)`` (R-07 — was a mid-run AttributeError
    after credentials/quotas/sys.path were already mutated).

    The ``context`` parameter is the same ``StageContext`` dict that
    flows between stages — each stage mutates it in place.
    """

    def execute(self, context: dict[str, Any]) -> Any:
        """Run the stage. Return value is ignored by the runner."""
        ...


# ── Local runner ─────────────────────────────────────────────────────────────


class LocalStageRunner:
    """Run a stage directly in the current process.

    This is the default runner — equivalent to the original bare
    ``stage.execute(context)`` call but with timing and error recording.

    Supports retry via ``max_retries`` and ``retry_delay`` parameters
    (read from the stage declaration in niche.yaml).

    Failure policy (P6, 2026-06-19): the ``fail_mode`` parameter controls
    what happens after all retries are exhausted:

      * ``"continue"`` (default) — log the error, record it on
        ``PipelineContext`` with ``fatal=False``, and continue to the next
        stage. This is the historical default that every stage has used
        since the pipeline runner shipped. Appropriate for fetchers,
        enrichers, and anything where partial pipeline output is still
        useful.

      * ``"abort"`` — record with ``fatal=True``, which sets
        ``ctx.is_aborted`` so the runner's pre-stage check (line 19 / 329
        of pipeline_runner.py) stops iterating. Appropriate for terminal
        stages where downstream work is meaningless if this stage failed
        (PushToBacklog, render, publish, validation gates).

    Per-stage opt-in via niche.yaml::

        - class: niches.gaming.stages.render_gaming_video.RenderGamingVideo
          fail_mode: abort   # render failure → abort the whole run

    Default ``continue`` preserves backward compatibility — every existing
    niche.yaml continues to behave as before unless explicitly opted in.

    When a ``metrics`` instance is provided, each stage execution is
    automatically recorded with timing and status.
    """

    _VALID_FAIL_MODES = frozenset({"continue", "abort"})

    def __init__(
        self,
        *,
        metrics: PipelineMetrics | None = None,
        max_retries: int = 0,
        retry_delay: float = 0.0,
        fail_mode: str = "continue",
    ) -> None:
        if fail_mode not in self._VALID_FAIL_MODES:
            raise ValueError(
                f"fail_mode must be one of {sorted(self._VALID_FAIL_MODES)}, got {fail_mode!r}"
            )
        self._metrics = metrics
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._fail_mode = fail_mode

    def run_stage(
        self,
        stage: Any,
        context: dict[str, Any],
        pipeline_ctx: Any,
    ) -> StageResult:
        stage_name = stage.__class__.__name__
        last_error: Exception | None = None

        for attempt in range(1 + self._max_retries):
            if attempt > 0:
                logger.info(
                    "[Pipeline] Retrying stage %s (attempt %d/%d) after %.0fs",
                    stage_name,
                    attempt + 1,
                    1 + self._max_retries,
                    self._retry_delay,
                )
                if self._retry_delay > 0:
                    time.sleep(self._retry_delay)

            t0 = time.monotonic()
            try:
                result_ctx = stage.execute(context)
                # Stages should return the same dict, but some return a new one
                if result_ctx is not context:
                    context.update(result_ctx)
                elapsed = time.monotonic() - t0
                logger.info(
                    "[Pipeline] Stage %s completed in %.1fs%s",
                    stage_name,
                    elapsed,
                    f" (attempt {attempt + 1})" if attempt > 0 else "",
                )
                if self._metrics is not None:
                    self._metrics.record_stage(
                        stage_name,
                        duration_ms=elapsed * 1000.0,
                        status="ok",
                    )
                return StageResult(
                    stage_name=stage_name,
                    success=True,
                    elapsed_seconds=elapsed,
                )
            except Exception as e:
                last_error = e
                elapsed = time.monotonic() - t0
                if attempt < self._max_retries:
                    logger.warning(
                        "[Pipeline] Stage %s failed (attempt %d/%d, %.1fs): %s — will retry",
                        stage_name,
                        attempt + 1,
                        1 + self._max_retries,
                        elapsed,
                        e,
                    )
                    continue
                # Final attempt failed. fail_mode controls whether this
                # aborts the rest of the pipeline (sets ctx.is_aborted via
                # the record_error(fatal=True) path) or merely records and
                # continues.
                is_fatal = self._fail_mode == "abort"
                pipeline_ctx.record_error(stage_name, e, fatal=is_fatal)
                logger.error(
                    "[Pipeline] Stage %s failed after %d attempt(s) (%.1fs): %s%s",
                    stage_name,
                    attempt + 1,
                    elapsed,
                    e,
                    " — aborting pipeline (fail_mode=abort)" if is_fatal else "",
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

        # Should not reach here, but safety fallback
        return StageResult(
            stage_name=stage_name,
            success=False,
            elapsed_seconds=0.0,
            error=last_error,
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

    P6 (2026-06-19): accepts ``fail_mode: "continue" | "abort"`` matching
    ``LocalStageRunner``'s contract. When ``abort``, a stage exception
    records with ``fatal=True`` so ``ctx.is_aborted`` stops the pipeline.
    Required for ``fail_mode: abort`` to actually work on sandboxed stages
    (notably ``RenderGamingVideo``). Without this, the gating attribute
    on a sandboxed stage would silently no-op.
    """

    _VALID_FAIL_MODES = frozenset({"continue", "abort"})

    def __init__(
        self,
        genlab_root: Path,
        *,
        egress_allow: list[str] | None = None,
        metrics: PipelineMetrics | None = None,
        fail_mode: str = "continue",
    ) -> None:
        if fail_mode not in self._VALID_FAIL_MODES:
            raise ValueError(
                f"fail_mode must be one of {sorted(self._VALID_FAIL_MODES)}, got {fail_mode!r}"
            )
        self._genlab_root = genlab_root
        self._egress_allow = egress_allow
        self._metrics = metrics
        self._fail_mode = fail_mode

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

        # If sandbox rendering isn't enabled, fall back to local — forward
        # fail_mode so the local fallback honors it too.
        if not sandbox_rendering_enabled():
            logger.info(
                "[Pipeline] Sandbox not enabled — running %s locally",
                stage_name,
            )
            return LocalStageRunner(
                metrics=self._metrics,
                fail_mode=self._fail_mode,
            ).run_stage(
                stage,
                context,
                pipeline_ctx,
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
                stage_name,
                elapsed,
            )
            if self._metrics is not None:
                self._metrics.record_stage(
                    stage_name,
                    duration_ms=elapsed * 1000.0,
                    status="ok",
                )
            return StageResult(
                stage_name=stage_name,
                success=True,
                elapsed_seconds=elapsed,
            )
        except Exception as e:
            elapsed = time.monotonic() - t0
            # P6: honor fail_mode like LocalStageRunner does.
            is_fatal = self._fail_mode == "abort"
            pipeline_ctx.record_error(stage_name, e, fatal=is_fatal)
            logger.error(
                "[Pipeline] Stage %s failed after %.1fs (sandboxed): %s%s",
                stage_name,
                elapsed,
                e,
                " — aborting pipeline (fail_mode=abort)" if is_fatal else "",
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
        Each stage receives a shallow copy of the context dict to prevent
        write races. After all stages complete, new/modified keys from
        each copy are merged back into the original context.

        Returns a list of StageResult in the same order as *batch*.
        """
        names = [s.__class__.__name__ for _, s in batch]
        logger.info(
            "[Pipeline] Running %d stages in parallel: %s",
            len(batch),
            names,
        )

        t0 = time.monotonic()
        results: dict[int, StageResult] = {}
        ctx_copies: dict[int, dict[str, Any]] = {}

        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            future_to_idx = {}
            for idx, (decl, stage) in enumerate(batch):
                runner = self.get_runner(decl)
                ctx_copy = dict(context)  # shallow copy for isolation
                ctx_copies[idx] = ctx_copy
                future = pool.submit(runner.run_stage, stage, ctx_copy, pipeline_ctx)
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
                    stage_name,
                    status,
                    result.elapsed_seconds,
                )

        # Merge new/modified keys from each copy back into original context
        for idx in range(len(batch)):
            copy = ctx_copies[idx]
            for key, value in copy.items():
                if key not in context or context[key] is not value:
                    context[key] = value

        elapsed = time.monotonic() - t0
        logger.info(
            "[Pipeline] Parallel group completed in %.1fs (wall-clock)",
            elapsed,
        )

        return [results[i] for i in range(len(batch))]

    def get_runner(self, declaration: dict[str, Any]) -> StageRunner:
        """Return the StageRunner for a given stage declaration.

        Reads ``retries``, ``retry_delay_seconds``, and ``fail_mode`` from
        the declaration to configure retry + failure-policy behavior on
        the returned runner. See ``LocalStageRunner.__init__`` for the
        ``fail_mode`` contract.
        """
        max_retries = int(declaration.get("retries", 0))
        retry_delay = float(declaration.get("retry_delay_seconds", 0))
        fail_mode = str(declaration.get("fail_mode", "continue"))
        sandbox_cfg = declaration.get("sandbox")

        # The cached self._local is shared and has the default fail_mode.
        # If a stage opts into a non-default fail_mode (or retries), build
        # a fresh runner so the cached one isn't mutated for other stages.
        if not sandbox_cfg:
            if max_retries > 0 or fail_mode != "continue":
                return LocalStageRunner(
                    metrics=self._metrics,
                    max_retries=max_retries,
                    retry_delay=retry_delay,
                    fail_mode=fail_mode,
                )
            return self._local

        if sandbox_cfg is True:
            # Simple flag — deny-all egress (render stages)
            return SandboxAwareStageRunner(
                genlab_root=self._genlab_root,
                metrics=self._metrics,
                fail_mode=fail_mode,
            )

        if isinstance(sandbox_cfg, dict):
            egress = sandbox_cfg.get("egress", [])
            return SandboxAwareStageRunner(
                genlab_root=self._genlab_root,
                egress_allow=egress,
                metrics=self._metrics,
                fail_mode=fail_mode,
            )

        return self._local
