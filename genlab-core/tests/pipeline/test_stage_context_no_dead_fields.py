"""Pin: dead fields surfaced by the 2026-06-30 audit stay removed.

Background — ``docs/architecture/stage_context_population_audit.md``:
    The audit enumerated every field declared in StageContext and
    surfaced ``backlog_client`` as the single DEAD field — declared,
    never populated, with a graceful-skip consumer in
    ``fetch_insights.py``. Removed in the same commit as this test.

    If somebody re-adds the field without also wiring population in
    ``pipeline_runner.py``, this test fails — preventing the dead
    declaration from re-entering the codebase and creating another
    sources_config-style silent failure later.
"""

from __future__ import annotations

from genlab_core.pipeline.stage_context import StageContext


def test_backlog_client_field_is_NOT_declared_in_stage_context() -> None:
    """Removed 2026-06-30: ``backlog_client`` had zero writers and a
    graceful-skip consumer in FetchInsights. If re-added, the pipeline
    runner MUST also start populating it; otherwise we resurrect the
    dead-field pattern documented in
    docs/architecture/stage_context_population_audit.md.
    """
    optional_keys = set(StageContext.__optional_keys__)  # type: ignore[attr-defined]
    assert "backlog_client" not in optional_keys, (
        "backlog_client was removed from StageContext on 2026-06-30 — "
        "zero writers, FetchInsights gracefully skips on absence. "
        "If you need to re-add it, ALSO wire population in "
        "pipeline_runner.py's run_niche context_dict construction, "
        "otherwise this resurrects the sources_config-style silent-failure "
        "pattern."
    )


def test_intentional_fallback_fields_are_still_declared() -> None:
    """These fields are declared but never populated by the runner —
    consumers fall back to sensible defaults. They are NOT dead; they
    exist as the documented contract for callers that want to inject
    overrides. If they ever stop being declared, the type-checker
    discipline on the override contract goes away.
    """
    optional_keys = set(StageContext.__optional_keys__)  # type: ignore[attr-defined]
    for field in ("backlog_config_path", "trending_steam_app_ids", "trending_game_ids"):
        assert field in optional_keys, (
            f"{field} is an intentional-fallback contract field — "
            "see stage_context.py comment + the audit doc"
        )
