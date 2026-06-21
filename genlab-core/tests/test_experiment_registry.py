"""Pin: experiment registry loads ABTest definitions (Lever I3 wire prep).

PR #433 shipped the experimentation primitives (ABTest, is_active,
assign_to_experiment) but with NO loader. Operators couldn't register
experiments declaratively. This module closes that gap.

These pins lock in:

  Loader (6):
  1. Missing file → empty list (fail-quiet)
  2. Empty YAML → empty list
  3. Valid YAML with 1 experiment → 1 ABTest loaded
  4. Per-entry validation: missing field → entry skipped, others loaded
  5. Per-entry validation: invalid allocation (sum≠1) → skipped
  6. YAML int allocation values coerced to float

  find_active_experiment (4):
  7. No experiments → None
  8. Experiment with matching niche_id → returned
  9. Experiment with empty niche_ids (all-niches) → returned for any niche
  10. Inactive experiment (out of date window) → None
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from genlab_core.learning.experiment_registry import (
    find_active_experiment,
    load_experiments_from_yaml,
)


def _write_yaml(tmp_path: Path, content: str) -> Path:
    """Helper: write YAML content to a tmp file and return the path."""
    p = tmp_path / "experiments.yaml"
    p.write_text(content)
    return p


class TestLoadExperimentsFromYaml:
    def test_missing_file_returns_empty(self, tmp_path):
        """Pin: missing file → empty list, no exception. Production
        config-not-yet-deployed must not crash the bandit."""
        result = load_experiments_from_yaml(tmp_path / "does_not_exist.yaml")
        assert result == []

    def test_empty_yaml_returns_empty(self, tmp_path):
        p = _write_yaml(tmp_path, "")
        assert load_experiments_from_yaml(p) == []

    def test_no_experiments_key_returns_empty(self, tmp_path):
        """Pin: YAML loads OK but 'experiments:' key missing → empty
        list. Defensive against operator typos like 'experiment:' or
        'tests:'."""
        p = _write_yaml(tmp_path, "other_key: value\n")
        assert load_experiments_from_yaml(p) == []

    def test_loads_valid_single_experiment(self, tmp_path):
        p = _write_yaml(
            tmp_path,
            """
experiments:
  - experiment_id: hook_style_test_2026_07
    arms: [question, imperative]
    allocation:
      question: 0.5
      imperative: 0.5
    start_date: "2026-07-01T00:00:00+00:00"
    end_date: "2026-08-01T00:00:00+00:00"
    niche_ids: [sports, gaming]
""",
        )
        result = load_experiments_from_yaml(p)
        assert len(result) == 1
        exp = result[0]
        assert exp.experiment_id == "hook_style_test_2026_07"
        assert exp.arms == ["question", "imperative"]
        assert exp.allocation == {"question": 0.5, "imperative": 0.5}
        assert exp.niche_ids == ["sports", "gaming"]
        assert exp.end_date == "2026-08-01T00:00:00+00:00"

    def test_skips_entry_missing_required_field_but_loads_others(self, tmp_path):
        """Pin: one bad entry doesn't poison the whole config. The
        loader is per-entry fail-quiet so operators can add new
        experiments without redeploying on a typo."""
        p = _write_yaml(
            tmp_path,
            """
experiments:
  - experiment_id: good_one
    arms: [a, b]
    allocation: {a: 0.5, b: 0.5}
    start_date: "2026-01-01T00:00:00+00:00"
  - arms: [a, b]
    # missing experiment_id → must be skipped
    allocation: {a: 0.5, b: 0.5}
    start_date: "2026-01-01T00:00:00+00:00"
  - experiment_id: another_good
    arms: [x, y]
    allocation: {x: 0.5, y: 0.5}
    start_date: "2026-01-01T00:00:00+00:00"
""",
        )
        result = load_experiments_from_yaml(p)
        assert len(result) == 2
        ids = [e.experiment_id for e in result]
        assert ids == ["good_one", "another_good"]

    def test_invalid_allocation_entry_is_skipped(self, tmp_path):
        """Pin: allocation sum≠1 → entry skipped (fail-quiet). Defends
        against operator math errors like 0.3/0.3 = 0.6."""
        p = _write_yaml(
            tmp_path,
            """
experiments:
  - experiment_id: bad_allocation
    arms: [a, b]
    allocation: {a: 0.3, b: 0.3}
    start_date: "2026-01-01T00:00:00+00:00"
  - experiment_id: good_allocation
    arms: [c, d]
    allocation: {c: 0.5, d: 0.5}
    start_date: "2026-01-01T00:00:00+00:00"
""",
        )
        result = load_experiments_from_yaml(p)
        # bad_allocation skipped, good_allocation kept
        assert len(result) == 1
        assert result[0].experiment_id == "good_allocation"

    def test_int_allocation_values_coerced_to_float(self, tmp_path):
        """Pin: YAML may emit ints for 0/1 — must coerce to float.
        validate_allocation accepts floats only."""
        p = _write_yaml(
            tmp_path,
            """
experiments:
  - experiment_id: int_alloc
    arms: [a, b]
    allocation: {a: 1, b: 0}
    start_date: "2026-01-01T00:00:00+00:00"
""",
        )
        result = load_experiments_from_yaml(p)
        assert len(result) == 1
        assert result[0].allocation == {"a": 1.0, "b": 0.0}


class TestFindActiveExperiment:
    @pytest.fixture
    def in_window_exp(self):
        from genlab_core.learning.experimentation import ABTest

        return ABTest(
            experiment_id="in_window",
            arms=["a", "b"],
            allocation={"a": 0.5, "b": 0.5},
            start_date="2026-01-01T00:00:00+00:00",
            end_date="2030-12-31T00:00:00+00:00",
            niche_ids=["sports", "gaming"],
        )

    @pytest.fixture
    def all_niches_exp(self):
        from genlab_core.learning.experimentation import ABTest

        return ABTest(
            experiment_id="all_niches",
            arms=["a", "b"],
            allocation={"a": 0.5, "b": 0.5},
            start_date="2026-01-01T00:00:00+00:00",
            end_date="2030-12-31T00:00:00+00:00",
            niche_ids=[],  # empty → all niches
        )

    @pytest.fixture
    def out_of_window_exp(self):
        from genlab_core.learning.experimentation import ABTest

        return ABTest(
            experiment_id="expired",
            arms=["a", "b"],
            allocation={"a": 0.5, "b": 0.5},
            start_date="2020-01-01T00:00:00+00:00",
            end_date="2020-12-31T00:00:00+00:00",
            niche_ids=["sports"],
        )

    def test_empty_list_returns_none(self):
        assert find_active_experiment("sports", []) is None

    def test_matching_niche_returns_experiment(self, in_window_exp):
        now = datetime(2026, 6, 15, tzinfo=UTC)
        result = find_active_experiment("sports", [in_window_exp], now=now)
        assert result is in_window_exp

    def test_non_matching_niche_returns_none(self, in_window_exp):
        """Pin: niche-scoped experiment doesn't leak to other niches."""
        now = datetime(2026, 6, 15, tzinfo=UTC)
        result = find_active_experiment("anime", [in_window_exp], now=now)
        assert result is None

    def test_empty_niche_ids_matches_any_niche(self, all_niches_exp):
        """Pin: niche_ids=[] means "all niches" — global experiments."""
        now = datetime(2026, 6, 15, tzinfo=UTC)
        for niche in ["sports", "gaming", "anime", "movies", "ai_creators"]:
            result = find_active_experiment(niche, [all_niches_exp], now=now)
            assert result is all_niches_exp

    def test_inactive_experiment_returns_none(self, out_of_window_exp):
        """Pin: expired experiment is filtered out even when niche matches."""
        now = datetime(2026, 6, 15, tzinfo=UTC)
        result = find_active_experiment("sports", [out_of_window_exp], now=now)
        assert result is None

    def test_returns_first_active_when_multiple_match(self, in_window_exp, all_niches_exp):
        """Pin: deterministic-by-list-order pick when two experiments
        match. Operators are expected to design non-overlapping
        experiments per niche; if two are active, the first wins."""
        now = datetime(2026, 6, 15, tzinfo=UTC)
        # Sports matches both — in_window_exp comes first
        result = find_active_experiment("sports", [in_window_exp, all_niches_exp], now=now)
        assert result is in_window_exp
