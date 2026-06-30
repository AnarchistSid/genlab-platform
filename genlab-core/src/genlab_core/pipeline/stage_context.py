"""Canonical schema for the dict that flows between pipeline stages.

For most of the project's life the inter-stage interface has been
``context: dict[str, Any]`` — a plain dict, schema-less, mutated by every
stage by convention. The render-captions bug fixed in PR #55 is the
textbook cost of that pattern::

    # render_whisper_captions.py — original
    config = context.get("config", {})
    # ... but pipeline_runner.run() seeds context["niche_config"], not "config"
    # → silent no-op on every production run for weeks.

This module introduces :class:`StageContext`, a ``TypedDict`` capturing the
canonical key surface that ``pipeline_runner.run()`` seeds and that stages
read + mutate. It is structurally identical to the runtime ``dict`` (so
zero behaviour changes) but lets static type checkers catch the
``context["config"]`` family of typos *at write time*.

Migration policy
----------------
* Stages **MAY** add their own scratch keys on top of ``StageContext`` —
  ``total=False`` makes every key optional and the dict is open to extra
  keys at runtime. Stage-specific intermediates (``clip_index``,
  ``trending_steam_app_ids``, etc.) are listed below as the union of what
  currently flows; new stages add to the union here when they introduce a
  new shared key.
* Stages **SHOULD** type their ``execute`` signature as
  ``def execute(self, context: StageContext) -> StageContext`` to opt into
  the type-checker discipline. The migration is intentionally
  incremental — see PR #73 for the first stage migrated
  (``RenderWhisperCaptions``); subsequent PRs migrate the rest.
* Anything still typed ``dict[str, Any]`` continues to work — the
  TypedDict is structurally compatible so there is no runtime impact.

Relationship to :class:`genlab_core.context.PipelineContext`
------------------------------------------------------------
``PipelineContext`` (dataclass, in ``genlab_core.context``) is the
**run-wide state bag** held by the runner and exposed via
``set_current_context``. The dict-shaped ``StageContext`` defined here is
what the runner reconstructs from that state bag and passes into each
stage's ``execute()`` — see ``pipeline_runner.run()`` for the construction
site. The two coexist deliberately: the dataclass carries identity +
error-recording behaviour, the TypedDict carries the per-stage data
contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from genlab_core.http.backlog_client import BacklogClient
    from genlab_core.observability.metrics_writer import PipelineMetrics


class StageContext(TypedDict, total=False):
    """The schema of the dict flowing between pipeline stages.

    ``total=False`` so that every key is optional at the type-checker
    level — the dict is built progressively, the runner seeds the core
    keys at the top of ``pipeline_runner.run()``, and stages add their
    own scratch as they execute.

    Listed below are the keys *currently in flight* across the codebase
    (surveyed via ``grep -rEho 'context\\.get\\("[a-z_]+"'`` over
    ``pipeline/stages/`` at the time of the PR #73 extraction). New
    shared keys go here; stage-private scratch can be added on top of
    the dict at runtime without listing here, but doing so forfeits the
    type-checker discipline for that key.
    """

    # ── Core: always seeded by pipeline_runner.run() ────────────────
    niche_id: str
    niche_root: str
    run_id: str
    run_dir: str
    stories: list[dict[str, Any]]
    blueprints: list[dict[str, Any]]
    run_stats: dict[str, Any]
    feature_flags: dict[str, bool]
    niche_config: dict[str, Any]
    # 2026-06-22 — working memory for within-run cross-stage
    # coordination. See ``genlab_core/pipeline/reasoning_trace.py``
    # for the append_trace helper. List of
    # {stage, decision, confidence, reasons, entity_id, metadata}.
    reasoning_trace: list[dict[str, Any]]
    # C3 (2026-06-30) — PipelineMetrics instance for per-stage timing
    # AND per-content_type drop tracking via record_filter_drops.
    # Same object that's threaded into StageRunnerFactory; surfaced
    # here so filter stages can call
    # ``context["metrics"].record_filter_drops(...)`` without an extra
    # plumbing layer. Stages defensively check ``context.get("metrics")``
    # since tests may build a context dict without seeding it.
    metrics: PipelineMetrics

    # ── Populated by specific stages (each stage that uses one is
    #    responsible for setting it on the way through) ──────────────
    sources_config: dict[str, Any]
    backlog_client: BacklogClient | None
    backlog_config_path: str
    clip_index: dict[str, Any]
    trending_steam_app_ids: list[str]
    trending_game_ids: list[str]
    existing_titles: list[str]
