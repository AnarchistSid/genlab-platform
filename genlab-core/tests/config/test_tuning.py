"""Tests for the platform-wide tuning config loader.

Guards three things:

1. The Pydantic model accepts the canonical defaults — what's in the
   committed ``tuning.yaml`` parses without error.
2. The loader returns field defaults when the yaml is missing (zero-
   config path for tests + scratch imports).
3. composite_scorer's module-level constants stay in sync with the
   tuning values — the migration's whole point is that recalibration
   means editing yaml, not code.
"""

from __future__ import annotations

import pytest
import yaml
from genlab_core.config.tuning import (
    _DEFAULT_YAML_PATH,
    CompositeScoringConfig,
    TuningConfig,
    _reset_cache_for_tests,
    get_tuning_config,
)


@pytest.fixture(autouse=True)
def _clear_tuning_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


class TestSchemaDefaults:
    def test_composite_scoring_defaults_match_pr52_calibration(self):
        cfg = CompositeScoringConfig()
        assert cfg.target_like_ratio == 0.03
        assert cfg.engagement_floor == 0.5
        assert cfg.default_min_view_count == {
            "gaming": 5000,
            "sports": 5000,
            "movies": 5000,
            "anime": 2000,
            "ai_creators": 2000,
        }

    def test_root_model_constructs_from_partial_dict(self):
        # Partial input — Pydantic fills in defaults for the rest.
        cfg = TuningConfig.model_validate({"composite_scoring": {"target_like_ratio": 0.05}})
        assert cfg.composite_scoring.target_like_ratio == 0.05
        # Other fields fall back to defaults.
        assert cfg.composite_scoring.engagement_floor == 0.5


class TestLoaderBehaviour:
    def test_committed_yaml_parses_against_schema(self):
        """If ``genlab-core/config/tuning.yaml`` exists, it must validate."""
        assert _DEFAULT_YAML_PATH.exists(), (
            f"expected committed tuning.yaml at {_DEFAULT_YAML_PATH}"
        )
        with open(_DEFAULT_YAML_PATH) as f:
            raw = yaml.safe_load(f) or {}
        # Round-trip through the model — raises on any drift.
        cfg = TuningConfig.model_validate(raw)
        # Committed values must agree with the field defaults (the migration
        # is byte-stable — the yaml restates what's in code).
        assert cfg.composite_scoring.target_like_ratio == 0.03
        assert cfg.composite_scoring.engagement_floor == 0.5

    def test_missing_yaml_returns_field_defaults(self, monkeypatch, tmp_path):
        """Scratch-import + unit-test paths must work without a yaml file."""
        monkeypatch.setattr(
            "genlab_core.config.tuning._DEFAULT_YAML_PATH",
            tmp_path / "does_not_exist.yaml",
        )
        _reset_cache_for_tests()
        cfg = get_tuning_config()
        assert cfg.composite_scoring.target_like_ratio == 0.03

    def test_loader_caches_across_calls(self):
        # Single read per process — the lru_cache ensures we don't re-parse
        # the yaml on every scoring run.
        a = get_tuning_config()
        b = get_tuning_config()
        assert a is b

    def test_invalid_yaml_fails_loudly_not_silently(self, monkeypatch, tmp_path):
        from pydantic import ValidationError

        bad = tmp_path / "tuning.yaml"
        bad.write_text("composite_scoring:\n  target_like_ratio: not a number\n")
        monkeypatch.setattr("genlab_core.config.tuning._DEFAULT_YAML_PATH", bad)
        _reset_cache_for_tests()
        with pytest.raises(ValidationError):
            get_tuning_config()


class TestCompositeScorerUsesTuning:
    """Regression: editing tuning.yaml must change composite_scorer's
    module-level aliases. The whole point of the migration is that
    recalibration is yaml-only — if these tests pass and ops edits the
    yaml, composite_scorer follows."""

    def test_module_aliases_match_tuning_at_import_time(self):
        from genlab_core.scoring import composite_scorer

        cfg = get_tuning_config().composite_scoring
        assert composite_scorer._TARGET_LIKE_RATIO == cfg.target_like_ratio
        assert composite_scorer._ENGAGEMENT_FLOOR == cfg.engagement_floor
        assert composite_scorer.DEFAULT_MIN_VIEW_COUNT == cfg.default_min_view_count

    def test_existing_default_min_view_count_callers_still_work(self):
        """``DEFAULT_MIN_VIEW_COUNT`` was the public name; callers reach in
        with ``DEFAULT_MIN_VIEW_COUNT["sports"]`` etc. — the migration must
        not break that surface."""
        from genlab_core.scoring.composite_scorer import DEFAULT_MIN_VIEW_COUNT

        for niche in ("gaming", "sports", "movies", "anime", "ai_creators"):
            assert niche in DEFAULT_MIN_VIEW_COUNT
            assert isinstance(DEFAULT_MIN_VIEW_COUNT[niche], int)


class TestYamlFileShape:
    """The file on disk is the operational source of truth — guard its
    structure so a future ops edit can't accidentally break the loader."""

    def test_yaml_path_resolves_to_expected_location(self):
        """``parents[3]`` arithmetic in tuning.py must keep landing at
        ``genlab-core/config/tuning.yaml`` even as the package layout
        evolves."""
        assert _DEFAULT_YAML_PATH.name == "tuning.yaml"
        assert _DEFAULT_YAML_PATH.parent.name == "config"
        # Walks back to the package root (genlab-core/).
        assert _DEFAULT_YAML_PATH.parent.parent.name == "genlab-core"

    def test_yaml_top_level_keys_are_all_known_to_schema(self):
        with open(_DEFAULT_YAML_PATH) as f:
            raw = yaml.safe_load(f) or {}
        known = set(TuningConfig.model_fields)
        unknown = set(raw.keys()) - known
        assert not unknown, (
            f"tuning.yaml has top-level keys the schema doesn't model: "
            f"{unknown}. Add them to TuningConfig or remove from yaml."
        )
