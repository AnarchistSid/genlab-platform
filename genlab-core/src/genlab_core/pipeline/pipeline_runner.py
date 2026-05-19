"""Generic pipeline runner — loads stages from niche config, executes in order.

This module provides the reusable orchestrator that all GenLab niches share.
Channel-specific runners (CriticalRush, Blackbox Brief, etc.) instantiate
``GenericPipelineRunner`` with their own ``niche_roots`` mapping and optional
hooks for dry-run output or pre-run credential checks.

Architecture:
    1. Resolve niche root from ``niche_roots`` dict
    2. Acquire a per-niche file lock to prevent concurrent runs for the
       same niche from racing on .tmp/runs/ dirs, Postgres stories upserts,
       and content_memory writes. Different niches can still run in parallel.
    3. Load niche config + feature flags via ``genlab_core.niche_loader``
    4. Build ``PipelineContext``
    5. Load stages via importlib from ``pipeline.stages`` in niche.yaml
    6. Group consecutive stages by ``parallel_group`` for concurrency
    7. Execute through ``StageRunnerFactory`` (local or sandboxed)
    8. Sync results back to context, teardown log handler, release lock

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

import fcntl
import importlib
import logging
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genlab_core.context import PipelineContext, _current_context, set_current_context
from genlab_core.exceptions import NicheConfigError
from genlab_core.niche_loader import get_feature_flags, load_niche_config
from genlab_core.pipeline.log_streamer import install_log_handler, remove_log_handler
from genlab_core.pipeline.stage_runner import StageRunnerFactory

logger = logging.getLogger(__name__)


class _NicheLockError(RuntimeError):
    """Raised when a niche lock is already held by another process."""


class _NicheLock:
    """Flock-based per-niche mutex.

    Uses ``fcntl.LOCK_EX | LOCK_NB`` so the acquire is non-blocking — if
    another pipeline instance already holds the lock, we raise immediately
    rather than wait. Different niches get different lock files, so they
    can run in parallel. Stores holder PID inside the file for debugging.

    The lock is released automatically on file-descriptor close (via
    context-manager ``__exit__``) or when the process exits.
    """

    def __init__(self, niche_id: str, genlab_root: Path) -> None:
        self._niche_id = niche_id
        self._lock_dir = genlab_root / ".tmp" / "locks"
        self._lock_path = self._lock_dir / f"pipeline-{niche_id}.lock"
        self._fd: int | None = None

    def __enter__(self) -> "_NicheLock":
        self._lock_dir.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(
            str(self._lock_path),
            os.O_RDWR | os.O_CREAT,
            0o644,
        )
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Read existing PID for the error message (best-effort)
            try:
                with open(self._lock_path) as fh:
                    holder = fh.read().strip() or "unknown"
            except OSError:
                holder = "unknown"
            os.close(self._fd)
            self._fd = None
            raise _NicheLockError(
                f"Niche '{self._niche_id}' is already running "
                f"(lock held by pid {holder}). Refusing to race."
            )
        # Record our PID in the lock file so other processes can see who holds it
        os.ftruncate(self._fd, 0)
        os.write(self._fd, f"{os.getpid()}\n".encode())
        os.fsync(self._fd)
        logger.info(
            "[Pipeline] Acquired niche lock '%s' (pid=%d, lock=%s)",
            self._niche_id, os.getpid(), self._lock_path,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            logger.info("[Pipeline] Released niche lock '%s'", self._niche_id)


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
        niche_roots: dict[str, Path],
        genlab_root: Path,
        *,
        pre_run_hook: Callable[..., None] | None = None,
        post_run_hook: Callable[..., None] | None = None,
    ) -> None:
        self._niche_roots = niche_roots
        self._genlab_root = genlab_root
        self._pre_run_hook = pre_run_hook
        self._post_run_hook = post_run_hook

    @property
    def supported_niches(self) -> list[str]:
        return list(self._niche_roots.keys())

    def run(
        self,
        niche_id: str,
        dry_run: bool = False,
        verbose: bool = False,
        stages_filter: list[str] | None = None,
    ) -> PipelineContext:
        """Execute the full pipeline for a niche.

        Args:
            niche_id: Must be in ``niche_roots``.
            dry_run: If True, load config and stages but don't execute.
            verbose: If True, set root log level to DEBUG.
            stages_filter: If provided, only run stages whose class name
                matches one of these strings (case-insensitive).

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

        # Acquire per-niche file lock. Non-blocking: if another pipeline
        # instance is already running this niche we raise immediately
        # rather than race. Different niches can still run in parallel.
        lock = _NicheLock(niche_id, self._genlab_root)
        try:
            lock.__enter__()
        except _NicheLockError as exc:
            logger.warning("[Pipeline] %s", exc)
            raise

        niche_root = self._niche_roots[niche_id]
        config = load_niche_config(niche_id, niche_root)
        flags = get_feature_flags(niche_id, niche_root)

        if self._pre_run_hook:
            self._pre_run_hook(niche_id, config)

        run_id = f"{niche_id}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

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

        # Inject niche_root into config so _load_stages can add it to sys.path
        config["_niche_root"] = str(self._niche_roots.get(niche_id, ""))

        try:
            stages, declarations = self._load_stages(niche_id, config)

            # Apply --stages filter if provided
            if stages_filter:
                filter_lower = [f.lower() for f in stages_filter]
                filtered = [
                    (s, d) for s, d in zip(stages, declarations)
                    if s.__class__.__name__.lower() in filter_lower
                ]
                if filtered:
                    stages, declarations = zip(*filtered)
                    stages, declarations = list(stages), list(declarations)
                else:
                    logger.warning(
                        "[Pipeline] --stages filter matched no stages: %s",
                        stages_filter,
                    )
                    stages, declarations = [], []

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

            run_dir = self._genlab_root / ".tmp" / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)

            context_dict: dict[str, Any] = {
                "niche_id": niche_id,
                "niche_root": str(niche_root),
                "run_id": run_id,
                "run_dir": str(run_dir),
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
            # Release the niche lock. Safe to call even if acquire failed
            # (the __enter__ path raises before reaching here in that case).
            try:
                lock.__exit__(None, None, None)
            except Exception as exc:
                logger.warning(
                    "[Pipeline] Lock release failed for niche '%s': %s",
                    niche_id, exc,
                )

    @staticmethod
    def _group_stages(
        stages: list[Any],
        declarations: list[dict[str, Any]],
    ) -> list[list[tuple]]:
        """Group consecutive stages by ``parallel_group`` for concurrent execution.

        Returns a list of batches. Each batch is a list of (declaration, stage)
        tuples. Batches with a single entry run sequentially; batches with
        multiple entries run in parallel.
        """
        batches: list[list[tuple]] = []
        current_group: str | None = None
        current_batch: list[tuple] = []

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

    # Per-niche foreign strategy/stage module prefixes. If a niche.yaml gets
    # contaminated with another niche's stages (cluster A scp leak pattern),
    # the foreign import is rejected at boot before any DB writes happen.
    _FOREIGN_PREFIX_MAP: dict[str, frozenset[str]] = {
        "ai_creators": frozenset({
            "sr_strategies", "cw_strategies", "fd_strategies",
            "CriticalRush.niches.gaming",
            "genlab_core.pipeline.stages.fetch_tmdb_trailers",
            "genlab_core.pipeline.stages.fetch_scorebat",
            "genlab_core.pipeline.stages.fetch_anime_promos",
            "genlab_core.pipeline.stages.fetch_twitch_clips",
        }),
        "gaming": frozenset({
            "bb_strategies", "sr_strategies", "cw_strategies", "fd_strategies",
            "genlab_core.pipeline.stages.fetch_tmdb_trailers",
            "genlab_core.pipeline.stages.fetch_scorebat",
            "genlab_core.pipeline.stages.fetch_anime_promos",
        }),
        "sports": frozenset({
            "bb_strategies", "sr_strategies", "fd_strategies",
            "CriticalRush.niches.gaming",
            "genlab_core.pipeline.stages.fetch_tmdb_trailers",
            "genlab_core.pipeline.stages.fetch_anime_promos",
            "genlab_core.pipeline.stages.fetch_twitch_clips",
        }),
        "movies": frozenset({
            "bb_strategies", "cw_strategies", "fd_strategies",
            "CriticalRush.niches.gaming",
            "genlab_core.pipeline.stages.fetch_scorebat",
            "genlab_core.pipeline.stages.fetch_anime_promos",
            "genlab_core.pipeline.stages.fetch_twitch_clips",
        }),
        "anime": frozenset({
            "bb_strategies", "sr_strategies", "cw_strategies",
            "CriticalRush.niches.gaming",
            "genlab_core.pipeline.stages.fetch_tmdb_trailers",
            "genlab_core.pipeline.stages.fetch_scorebat",
            "genlab_core.pipeline.stages.fetch_twitch_clips",
        }),
    }

    @classmethod
    def _check_foreign_stage(cls, niche_id: str, module_path: str) -> None:
        forbidden = cls._FOREIGN_PREFIX_MAP.get(niche_id, frozenset())
        for prefix in forbidden:
            if module_path == prefix or module_path.startswith(prefix + "."):
                raise NicheConfigError(
                    f"CROSS-NICHE LEAK BLOCKED: niche '{niche_id}' cannot load "
                    f"stage from '{module_path}' (forbidden prefix '{prefix}'). "
                    f"niche.yaml is contaminated with another niche's stages — "
                    f"aborting before any DB writes. Verify "
                    f"{cls.__module__.rsplit('.', 1)[0]}/.../{niche_id} config."
                )

    def _load_stages(
        self,
        niche_id: str, config: dict[str, Any],
    ) -> tuple[list[Any], list[dict[str, Any]]]:
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

        # Ensure niche root is on sys.path so niche-specific strategy
        # modules (bb_strategies, cw_strategies, etc.) can be imported.
        niche_root = str(config.get("_niche_root", ""))
        if niche_root and niche_root not in sys.path:
            sys.path.insert(0, niche_root)
            logger.debug("[Pipeline] Added %s to sys.path for stage imports", niche_root)

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

            # Cross-niche guard: abort if niche.yaml was contaminated with
            # another niche's strategy modules (cluster A scp leak pattern).
            self._check_foreign_stage(niche_id, module_path)

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
