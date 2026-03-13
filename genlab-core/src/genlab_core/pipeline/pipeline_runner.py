"""Generic pipeline runner — loads stages from niche config, executes in order.

This module provides the reusable orchestrator that all GenLab niches share.
Channel-specific runners (CriticalRush, Blackbox Brief, etc.) instantiate
``GenericPipelineRunner`` with their own ``niche_roots`` mapping and optional
hooks for dry-run output or pre-run credential checks.

Architecture:
    1. Resolve niche root from ``niche_roots`` dict
    2. Load niche config + feature flags via ``genlab_core.niche_loader``
    3. Build ``PipelineContext``
    4. Load stages via importlib from ``pipeline.stages`` in niche.yaml
    5. Group consecutive stages by ``parallel_group`` for concurrency
    6. Execute through ``StageRunnerFactory`` (local or sandboxed)
    7. Sync results back to context, teardown log handler

Usage::

    from genlab_core.pipeline.pipeline_runner import GenericPipelineRunner
    from pathlib import Path

    runner = GenericPipelineRunner(
        niche_roots={"gaming": Path("/path/to/CriticalRush"), ...},
        genlab_root=Path("/path/to/GenLab"),
    )
    ctx = runner.run("gaming")
"""
from __future__ import annotations

import importlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from genlab_core.context import PipelineContext, _current_context, set_current_context
from genlab_core.exceptions import NicheConfigError
from genlab_core.niche_loader import get_feature_flags, load_niche_config
from genlab_core.pipeline.log_streamer import install_log_handler, remove_log_handler
from genlab_core.pipeline.stage_runner import StageRunnerFactory

logger = logging.getLogger(__name__)


class GenericPipelineRunner:
    """Niche-agnostic pipeline runner.

    Args:
        niche_roots: Mapping of niche_id → Path to the channel's root directory.
        genlab_root: Path to the GenLab workspace root (for log output).
        pre_run_hook: Optional callback invoked with (niche_id, config) before
            stage execution. Use for credential checks, dry-run gates, etc.
        post_run_hook: Optional callback invoked with (ctx,) after execution.
            Use for dry-run summaries, metrics reporting, etc.
    """

    def __init__(
        self,
        niche_roots: Dict[str, Path],
        genlab_root: Path,
        *,
        pre_run_hook: Optional[Callable[..., None]] = None,
        post_run_hook: Optional[Callable[..., None]] = None,
    ) -> None:
        self._niche_roots = niche_roots
        self._genlab_root = genlab_root
        self._pre_run_hook = pre_run_hook
        self._post_run_hook = post_run_hook

    @property
    def supported_niches(self) -> List[str]:
        return list(self._niche_roots.keys())

    def run(
        self,
        niche_id: str,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> PipelineContext:
        """Execute the full pipeline for a niche.

        Args:
            niche_id: Must be in ``niche_roots``.
            dry_run: If True, load config and stages but don't execute.
            verbose: If True, set root log level to DEBUG.

        Returns:
            The PipelineContext with all accumulated state.

        Raises:
            ValueError: If niche_id is not in niche_roots.
        """
        if verbose:
            logging.basicConfig(
                level=logging.DEBUG,
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            )
        else:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            )
        for noisy in ("httpx", "httpcore", "hpack", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        if niche_id not in self._niche_roots:
            raise ValueError(
                f"Unsupported niche '{niche_id}'. "
                f"Supported: {self.supported_niches}"
            )

        niche_root = self._niche_roots[niche_id]
        config = load_niche_config(niche_id, niche_root)
        flags = get_feature_flags(niche_id, niche_root)

        if self._pre_run_hook:
            self._pre_run_hook(niche_id, config)

        run_id = f"{niche_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

        ctx = PipelineContext(
            niche_id=niche_id,
            run_id=run_id,
            feature_flags=flags,
            niche_config=config,
        )

        token = set_current_context(ctx)

        log_dir = self._genlab_root / ".tmp"
        log_handler, stage_filter, _log_path = install_log_handler(
            niche_id, run_id, log_dir,
        )

        try:
            stages, declarations = self._load_stages(niche_id, config)
            logger.info(
                "[Pipeline] %s%d stages for '%s': %s",
                "[DryRun] " if dry_run else "",
                len(stages),
                niche_id,
                [s.__class__.__name__ for s in stages],
            )

            if dry_run:
                if self._post_run_hook:
                    self._post_run_hook(ctx)
                return ctx

            context_dict: Dict[str, Any] = {
                "stories": ctx.stories,
                "blueprints": ctx.blueprints,
                "run_stats": ctx.run_stats,
                "feature_flags": ctx.feature_flags,
                "niche_config": ctx.niche_config,
            }

            runner_factory = StageRunnerFactory(
                genlab_root=self._genlab_root,
                stage_log_filter=stage_filter,
            )

            for batch in self._group_stages(stages, declarations):
                if ctx.is_aborted:
                    logger.error(
                        "[Pipeline] Aborted before %s",
                        [s.__class__.__name__ for _, s in batch],
                    )
                    break

                if len(batch) == 1:
                    decl, stage = batch[0]
                    result = runner_factory.run(decl, stage, context_dict, ctx)
                    context_dict.setdefault("run_stats", {}).setdefault(
                        "_stage_timings", {},
                    )[result.stage_name] = result.elapsed_seconds
                else:
                    results = runner_factory.run_parallel(
                        batch, context_dict, ctx,
                    )
                    for result in results:
                        context_dict.setdefault("run_stats", {}).setdefault(
                            "_stage_timings", {},
                        )[result.stage_name] = result.elapsed_seconds

            ctx.stories = context_dict.get("stories", ctx.stories)
            ctx.blueprints = context_dict.get("blueprints", ctx.blueprints)
            ctx.run_stats = context_dict.get("run_stats", ctx.run_stats)

            if self._post_run_hook:
                self._post_run_hook(ctx)

            return ctx

        finally:
            remove_log_handler(log_handler)
            _current_context.reset(token)

    @staticmethod
    def _group_stages(
        stages: List[Any],
        declarations: List[Dict[str, Any]],
    ) -> List[List[tuple]]:
        """Group consecutive stages by ``parallel_group`` for concurrent execution.

        Returns a list of batches. Each batch is a list of (declaration, stage)
        tuples. Batches with a single entry run sequentially; batches with
        multiple entries run in parallel.
        """
        batches: List[List[tuple]] = []
        current_group: str | None = None
        current_batch: List[tuple] = []

        for decl, stage in zip(declarations, stages):
            group = decl.get("parallel_group")

            if group and group == current_group:
                current_batch.append((decl, stage))
            else:
                if current_batch:
                    batches.append(current_batch)
                current_batch = [(decl, stage)]
                current_group = group

        if current_batch:
            batches.append(current_batch)

        return batches

    @staticmethod
    def _load_stages(
        niche_id: str, config: Dict[str, Any],
    ) -> tuple[List[Any], List[Dict[str, Any]]]:
        """Dynamically load pipeline stages from niche configuration.

        Reads ``pipeline.stages`` from niche.yaml — each entry declares a
        fully-qualified class path. Classes are imported via importlib
        and instantiated with no arguments.

        Returns:
            (stages, declarations) — parallel lists of stage instances
            and their raw YAML declaration dicts.
        """
        stage_declarations = config.get("pipeline", {}).get("stages")

        if stage_declarations is None:
            raise NicheConfigError(
                f"niche.yaml for '{niche_id}' is missing pipeline.stages. "
                f"Add a pipeline.stages list to enable this niche's pipeline. "
                f"See niches/gaming/config/niche.yaml for reference."
            )

        if not stage_declarations:
            raise NicheConfigError(
                f"pipeline.stages for '{niche_id}' is empty. "
                f"Declare at least one stage to run the pipeline."
            )

        stages = []
        declarations = []
        for declaration in stage_declarations:
            if not declaration.get("enabled", True):
                logger.info(
                    "[Pipeline] Stage skipped (enabled=false): %s",
                    declaration.get("class", "unknown"),
                )
                continue

            class_path = declaration["class"]
            module_path, class_name = class_path.rsplit(".", 1)

            try:
                module = importlib.import_module(module_path)
            except ImportError as e:
                raise ImportError(
                    f"Cannot import stage module '{module_path}' "
                    f"for niche '{niche_id}': {e}"
                ) from e

            try:
                stage_class = getattr(module, class_name)
            except AttributeError as e:
                raise AttributeError(
                    f"Module '{module_path}' has no class '{class_name}' "
                    f"(declared in niche.yaml for '{niche_id}')"
                ) from e

            stages.append(stage_class())
            declarations.append(declaration)
            logger.debug("[Pipeline] Loaded stage: %s", class_name)

        logger.info(
            "[Pipeline] Loaded %d stages for niche '%s'",
            len(stages), niche_id,
        )
        return stages, declarations
