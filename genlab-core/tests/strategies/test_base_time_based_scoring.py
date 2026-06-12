"""R-70 part 2 — PR 5b unit tests for ``BaseTimeBasedScoringStrategy``.

The second-tier scoring base lifts the three SR + FD shared bodies:
``__init__`` shape, ``_ensure_config``, and ``_score_timeliness``.
CW does NOT inherit from this — it stays on the narrow
``BaseScoringStrategy``. These tests pin the lifted contract:

1. A subclass that supplies ``_scoring_yaml_path`` + the still-
   abstract methods (``_score_magnitude``, ``score_item``,
   ``execute``) instantiates cleanly. The remaining abstract
   methods stay abstract because the empirical body-diff said the
   bodies aren't shared.

2. ``_ensure_config`` reads from the subclass-provided
   ``self._scoring_yaml_path`` (not a hardcoded constant) and
   populates ``_config`` / ``_scoring`` / ``_thresholds`` exactly
   like the per-channel bodies did before extraction. Calling it
   twice is a no-op (the cache holds).

3. ``_score_timeliness`` applies exponential decay against
   ``fetched_at`` with ``decay_half_life_hours`` read from
   ``_scoring.timeliness``. When the config key is absent, the
   subclass-set ``_default_timeliness_half_life_hours`` is used
   — that's the literal-only divergence between SR (48h) and FD
   (24h).

4. ``_score_timeliness`` honours the guard clauses the per-channel
   bodies had: empty ``fetched_at`` → 0.0, future ``fetched_at`` →
   1.0, malformed ``fetched_at`` → 0.0 (ValueError/TypeError).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from genlab_core.strategies.base_scoring import BaseScoringStrategy
from genlab_core.strategies.base_time_based_scoring import (
    BaseTimeBasedScoringStrategy,
)


def _write_yaml(path: Path, payload: dict) -> Path:
    path.write_text(yaml.safe_dump(payload))
    return path


class _StubTimeBased(BaseTimeBasedScoringStrategy):
    """Concrete subclass with the still-abstract methods stubbed.

    Mirrors the shape a real channel subclass takes after PR 5b:
    each channel sets ``_scoring_yaml_path`` in its own ``__init__``
    and supplies its own ``_score_magnitude`` /
    ``_score_engagement_potential`` / ``score_item`` / ``execute``.
    """

    _log_prefix = "[stub]"

    def __init__(self, scoring_yaml_path: Path) -> None:
        super().__init__()
        self._scoring_yaml_path = scoring_yaml_path

    def _score_magnitude(self, item: dict) -> float:
        return 0.0

    def score_item(self, item: dict) -> dict:
        return {**item, "scores": {}, "score": 0.0, "final_score": 0.0}

    def execute(self, context: Any) -> Any:
        return context


def test_r70_pr5b_subclass_with_full_abstract_impl_instantiates(
    tmp_path: Path,
) -> None:
    """The second-tier base's contract: ``_score_magnitude`` /
    ``score_item`` / ``execute`` stay abstract; ``_ensure_config``
    / ``_score_timeliness`` are inherited concrete. A subclass that
    provides the still-abstract trio + sets ``_scoring_yaml_path``
    must instantiate cleanly."""
    yaml_path = _write_yaml(tmp_path / "scoring.yaml", {"scoring_dimensions": {}})
    stage = _StubTimeBased(yaml_path)
    assert isinstance(stage, BaseTimeBasedScoringStrategy)
    assert isinstance(stage, BaseScoringStrategy)  # PR 5a base inherited


def test_r70_pr5b_cannot_instantiate_base_directly() -> None:
    """The second-tier base is abstract — direct instantiation must
    fail. Pin so a future "let's make this a concrete fallback"
    edit can't silently weaken the contract."""
    with pytest.raises(TypeError, match="abstract"):
        BaseTimeBasedScoringStrategy()  # type: ignore[abstract]


def test_r70_pr5b_ensure_config_reads_subclass_yaml_path(
    tmp_path: Path,
) -> None:
    """The lifted ``_ensure_config`` must load from the subclass-
    provided ``self._scoring_yaml_path`` and populate the documented
    instance attrs. Pin so per-channel YAML paths survive a future
    refactor."""
    yaml_path = _write_yaml(
        tmp_path / "scoring.yaml",
        {
            "scoring_dimensions": {"timeliness": {"decay_half_life_hours": 12.0}},
            "thresholds": {"min_score": 0.42},
        },
    )
    stage = _StubTimeBased(yaml_path)
    assert stage._config is None
    stage._ensure_config()
    assert stage._config is not None
    assert stage._scoring == {"timeliness": {"decay_half_life_hours": 12.0}}
    assert stage._thresholds == {"min_score": 0.42}


def test_r70_pr5b_ensure_config_is_cached(tmp_path: Path) -> None:
    """Calling ``_ensure_config`` twice must NOT re-read the YAML.
    Pin so a future "let's make config hot-reloadable" edit doesn't
    silently change the cold-path performance characteristics."""
    yaml_path = _write_yaml(tmp_path / "scoring.yaml", {"scoring_dimensions": {"timeliness": {}}})
    stage = _StubTimeBased(yaml_path)
    stage._ensure_config()
    first_config = stage._config
    # Mutate file on disk; cached attr must remain unchanged
    _write_yaml(yaml_path, {"scoring_dimensions": {"timeliness": {"changed": True}}})
    stage._ensure_config()
    assert stage._config is first_config


def test_r70_pr5b_score_timeliness_uses_config_half_life(tmp_path: Path) -> None:
    """``_score_timeliness`` reads ``decay_half_life_hours`` from
    config when present. With a 10-hour half-life and a 10-hour-old
    item, the score is 0.5 exactly. Mirrors production call pattern:
    ``score_item`` calls ``_ensure_config`` first, then the per-axis
    scorers read off the populated config."""
    yaml_path = _write_yaml(
        tmp_path / "scoring.yaml",
        {"scoring_dimensions": {"timeliness": {"decay_half_life_hours": 10.0}}},
    )
    stage = _StubTimeBased(yaml_path)
    stage._ensure_config()
    fetched_at = (datetime.now(UTC) - timedelta(hours=10)).isoformat()
    result = stage._score_timeliness({"fetched_at": fetched_at})
    assert math.isclose(result, 0.5, abs_tol=0.01)


def test_r70_pr5b_score_timeliness_falls_back_to_subclass_default(
    tmp_path: Path,
) -> None:
    """When the config doesn't carry ``decay_half_life_hours``, the
    body uses ``self._default_timeliness_half_life_hours`` — the
    class attribute SR sets to 48.0 and FD overrides to 24.0. Pin
    the literal-only-divergence escape hatch the empirical body-
    diff surfaced."""
    yaml_path = _write_yaml(tmp_path / "scoring.yaml", {"scoring_dimensions": {}})

    # FD-style subclass: 24h default
    class _FDLike(_StubTimeBased):
        _default_timeliness_half_life_hours = 24.0

    fd_stage = _FDLike(yaml_path)
    fd_stage._ensure_config()
    fetched_at = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    fd_result = fd_stage._score_timeliness({"fetched_at": fetched_at})
    assert math.isclose(fd_result, 0.5, abs_tol=0.01)

    # SR-style subclass: 48h default (base default)
    sr_stage = _StubTimeBased(yaml_path)
    sr_stage._ensure_config()
    sr_result = sr_stage._score_timeliness({"fetched_at": fetched_at})
    # 24h elapsed with 48h half-life → 2^(-0.5) ≈ 0.707
    assert math.isclose(sr_result, math.pow(0.5, 0.5), abs_tol=0.01)


def test_r70_pr5b_score_timeliness_empty_fetched_at_returns_zero(
    tmp_path: Path,
) -> None:
    """The per-channel bodies returned 0.0 on empty ``fetched_at``.
    Pin so the lifted body keeps the same guard."""
    yaml_path = _write_yaml(tmp_path / "scoring.yaml", {"scoring_dimensions": {}})
    stage = _StubTimeBased(yaml_path)
    assert stage._score_timeliness({"fetched_at": ""}) == 0.0
    assert stage._score_timeliness({}) == 0.0


def test_r70_pr5b_score_timeliness_future_fetched_at_returns_one(
    tmp_path: Path,
) -> None:
    """Future ``fetched_at`` (clock skew, manual override) returns
    1.0 in the per-channel bodies. Pin the lift preserves it."""
    yaml_path = _write_yaml(tmp_path / "scoring.yaml", {"scoring_dimensions": {}})
    stage = _StubTimeBased(yaml_path)
    future = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
    assert stage._score_timeliness({"fetched_at": future}) == 1.0


def test_r70_pr5b_score_timeliness_malformed_fetched_at_returns_zero(
    tmp_path: Path,
) -> None:
    """Malformed ``fetched_at`` strings (ValueError/TypeError) → 0.0,
    matching the per-channel bodies. Pin so a future "let's raise"
    edit can't surprise stages that rely on graceful degradation."""
    yaml_path = _write_yaml(tmp_path / "scoring.yaml", {"scoring_dimensions": {}})
    stage = _StubTimeBased(yaml_path)
    assert stage._score_timeliness({"fetched_at": "not-a-date"}) == 0.0
    assert stage._score_timeliness({"fetched_at": 12345}) == 0.0


def test_r70_pr5b_score_timeliness_naive_datetime_treated_as_utc(
    tmp_path: Path,
) -> None:
    """The per-channel bodies promote naive datetimes to UTC. Pin
    so a future edit doesn't silently break feeds that emit
    timezone-less ISO strings."""
    yaml_path = _write_yaml(
        tmp_path / "scoring.yaml",
        {"scoring_dimensions": {"timeliness": {"decay_half_life_hours": 10.0}}},
    )
    stage = _StubTimeBased(yaml_path)
    stage._ensure_config()
    naive = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=10)).isoformat()
    result = stage._score_timeliness({"fetched_at": naive})
    assert math.isclose(result, 0.5, abs_tol=0.01)


def test_r70_pr5b_inherits_score_novelty_from_pr5a_base(tmp_path: Path) -> None:
    """The second-tier base inherits from ``BaseScoringStrategy``;
    a subclass gets ``_score_novelty`` for free. Pin the chain so
    a future "let's split the hierarchy" doesn't silently break
    SR + FD by requiring them to re-implement novelty."""
    yaml_path = _write_yaml(tmp_path / "scoring.yaml", {"scoring_dimensions": {}})
    stage = _StubTimeBased(yaml_path)
    assert stage._score_novelty({"novelty_score": 0.73}) == 0.73
    assert stage._score_novelty({}) == 0.5
