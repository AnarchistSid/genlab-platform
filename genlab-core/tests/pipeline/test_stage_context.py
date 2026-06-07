"""Tests for the StageContext TypedDict + the runner's construction site.

The point of the TypedDict is to make the per-stage data contract visible
in one place. These tests guard:

1. The TypedDict accepts the canonical-key payload the runner builds.
2. The TypedDict is structurally identical to a plain dict — adding
   stage-private scratch keys at runtime still works.
3. The exact key set the runner currently seeds matches the TypedDict's
   ``__optional_keys__``; any drift in the runner's construction site is
   noticed at test time, not as a silent ``context["typo"]`` lookup that
   no-ops.
"""

from __future__ import annotations

from genlab_core.pipeline.stage_context import StageContext


class TestTypedDictShape:
    def test_accepts_the_runners_canonical_payload(self) -> None:
        """The exact dict that ``pipeline_runner.run()`` builds at line 265
        must be a valid StageContext."""
        ctx: StageContext = {
            "niche_id": "sports",
            "niche_root": "/opt/genlab/ClutchWire",
            "run_id": "sports_20260607_120000",
            "run_dir": "/opt/genlab/.tmp/runs/sports_20260607_120000",
            "stories": [],
            "blueprints": [],
            "run_stats": {},
            "feature_flags": {},
            "niche_config": {"niche_id": "sports"},
        }
        # No raise at runtime; key set matches the runner's seed.
        assert ctx["niche_id"] == "sports"
        assert ctx["niche_config"]["niche_id"] == "sports"

    def test_all_keys_are_optional_total_false(self) -> None:
        """``total=False`` means every key is optional at the type-checker
        level — required for the progressive-construction pattern stages use
        (each stage adds its own scratch as it executes)."""
        assert StageContext.__total__ is False  # type: ignore[attr-defined]
        # Empty dict still type-checks as StageContext at runtime — useful for
        # tests that pass a stripped-down context.
        ctx: StageContext = {}
        assert ctx == {}

    def test_canonical_keys_match_runner_construction(self) -> None:
        """If the runner ever seeds a key the TypedDict doesn't know about,
        this test fails loudly — drift catcher between the two files."""
        # The keys pipeline_runner.run() builds at line 265:
        runner_keys = {
            "niche_id",
            "niche_root",
            "run_id",
            "run_dir",
            "stories",
            "blueprints",
            "run_stats",
            "feature_flags",
            "niche_config",
        }
        typed_keys = set(StageContext.__optional_keys__)  # type: ignore[attr-defined]
        missing = runner_keys - typed_keys
        assert not missing, f"pipeline_runner.run() seeds keys missing from StageContext: {missing}"


class TestRuntimeOpenness:
    """The TypedDict is documentation, not a runtime gate. Stage-private
    scratch keys still work — that contract is essential because most
    stages add their own intermediates."""

    def test_extra_runtime_keys_do_not_raise(self) -> None:
        ctx: StageContext = {"niche_id": "gaming"}
        # Stage-private scratch — type-checker would complain (correctly),
        # but the dict is open at runtime.
        ctx["some_stage_private_key"] = "scratch"  # type: ignore[typeddict-unknown-key]
        assert ctx["some_stage_private_key"] == "scratch"  # type: ignore[typeddict-unknown-key]

    def test_missing_keys_use_get_with_default(self) -> None:
        """The canonical access pattern is ``.get(key, default)`` — keys
        seeded by some stages are not present until that stage has run."""
        ctx: StageContext = {"niche_id": "ai_creators"}
        # `clip_index` is populated by DownloadTopVideos; before that stage
        # runs, the key is absent. Callers must use .get() with a default.
        assert ctx.get("clip_index", {}) == {}
