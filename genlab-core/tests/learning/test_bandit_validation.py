"""Pins for the bandit validation harness.

Covers:
  * _interpret returns the correct verdict for each (n, spearman, lift)
    region (cold start, useful, ineffective, developing)
  * _compute_correlations matches scipy expectations for known vectors
    (perfect linear, perfect inverse, no correlation, too-few-points)
  * compute_bandit_engagement_correlation returns sensible defaults
    on DSN-missing / empty data
  * persist_result + check_three_day_ineffective sanity paths
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

# ── _interpret (pure function) ──────────────────────────────────────


@pytest.mark.parametrize(
    "n,spearman,lift,expected",
    [
        # Cold-start regardless of correlation strength
        (5, 0.9, 5.0, "low signal"),
        (29, 0.5, 2.0, "low signal"),
        # Useful at +30% spearman or above
        (30, 0.3, 1.5, "bandit useful"),
        (100, 0.7, 4.0, "bandit useful"),
        (50, 1.0, 10.0, "bandit useful"),
        # Ineffective requires BOTH low spearman AND low lift
        (40, 0.05, 1.0, "bandit ineffective"),
        (40, 0.1, 1.2, "bandit ineffective"),
        (40, -0.2, 0.9, "bandit ineffective"),
        # Developing: somewhere in between
        (40, 0.2, 1.5, "developing"),
        (40, 0.05, 1.5, "developing"),  # low spearman but lift > 1.2
        (40, 0.25, 1.0, "developing"),  # low lift but spearman > 0.1
    ],
)
def test_interpret_buckets(n, spearman, lift, expected):
    """The _interpret function must produce the right verdict for
    each region of the (n, spearman, lift) space."""
    from genlab_core.learning.bandit_validation import _interpret

    assert _interpret(n, spearman, lift) == expected


# ── _compute_correlations (with scipy) ──────────────────────────────


def test_compute_correlations_perfect_positive():
    """Strictly increasing vectors → spearman = 1.0, pearson = 1.0,
    top-quartile lift well above 1."""
    from genlab_core.learning.bandit_validation import _compute_correlations

    predicted = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    actual = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0]
    spearman, pearson, lift = _compute_correlations(predicted, actual)
    assert spearman == pytest.approx(1.0, abs=1e-6)
    assert pearson == pytest.approx(1.0, abs=1e-6)
    # Top quartile = [70, 80], bottom = [10, 20]; lift = 75/15 = 5.0
    assert lift == pytest.approx(5.0, abs=1e-6)


def test_compute_correlations_perfect_negative():
    """Strictly decreasing actual → spearman = -1.0."""
    from genlab_core.learning.bandit_validation import _compute_correlations

    predicted = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    actual = [80.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0, 10.0]
    spearman, pearson, lift = _compute_correlations(predicted, actual)
    assert spearman == pytest.approx(-1.0, abs=1e-6)
    assert pearson == pytest.approx(-1.0, abs=1e-6)
    # Lift is INVERSE — top quartile actual = [10, 20], bottom = [70, 80]
    # so lift = 15/75 = 0.2 (bandit is ANTI-useful)
    assert lift == pytest.approx(0.2, abs=1e-6)


def test_compute_correlations_no_correlation():
    """Constant actual vector → undefined spearman (NaN) clamped to 0.0;
    interpretation downstream treats this as 'ineffective'."""
    from genlab_core.learning.bandit_validation import _compute_correlations

    predicted = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    actual = [50.0] * 8  # constant
    spearman, pearson, lift = _compute_correlations(predicted, actual)
    assert spearman == 0.0  # NaN-clamped
    assert pearson == 0.0
    # Top and bottom both average 50, lift = 1.0
    assert lift == pytest.approx(1.0, abs=1e-6)


def test_compute_correlations_too_few_points():
    """Vectors shorter than 4 (quartile minimum) → all zeros + lift=1."""
    from genlab_core.learning.bandit_validation import _compute_correlations

    predicted = [0.1, 0.2, 0.3]  # n=3
    actual = [1.0, 2.0, 3.0]
    spearman, pearson, lift = _compute_correlations(predicted, actual)
    assert spearman == 0.0
    assert pearson == 0.0
    assert lift == 1.0


def test_compute_correlations_zero_bottom_does_not_div_by_zero():
    """When bottom quartile has zero engagement, lift must not be inf."""
    from genlab_core.learning.bandit_validation import _compute_correlations

    predicted = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    actual = [0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 100.0]
    spearman, pearson, lift = _compute_correlations(predicted, actual)
    # lift should be capped at top_avg (52.5), not inf
    assert math.isfinite(lift)
    assert lift > 0


# ── compute_bandit_engagement_correlation (fail-OPEN paths) ─────────


def test_compute_correlation_returns_defaults_on_missing_dsn(monkeypatch):
    """No DATABASE_URL → BanditValidationResult with defaults +
    interpretation='low signal'."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from genlab_core.learning.bandit_validation import (
        compute_bandit_engagement_correlation,
    )

    result = compute_bandit_engagement_correlation("gaming")
    assert result.niche_id == "gaming"
    assert result.n_posts == 0
    assert result.interpretation == "low signal"


def test_compute_correlation_returns_empty_for_empty_niche_id():
    """Empty niche_id is invalid input; return safe-default result."""
    from genlab_core.learning.bandit_validation import (
        compute_bandit_engagement_correlation,
    )

    result = compute_bandit_engagement_correlation("")
    assert result.niche_id == ""
    assert result.n_posts == 0


# ── End-to-end with mocked DB returning known data ──────────────────


def _patch_conn(predicted_actual_pairs: list[tuple[float, float]]) -> MagicMock:
    """Mock psycopg connection returning the given (predicted, actual) pairs
    as bandit_validation._fetch_paired_data SELECT rows."""
    rows = [{"predicted_score": p, "actual_engagement": a} for p, a in predicted_actual_pairs]
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.__enter__ = lambda self: self
    cursor.__exit__ = lambda *a: False
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda *a: False
    return conn


def test_compute_correlation_useful_path(monkeypatch):
    """Mock 50 posts with strong positive correlation → interpretation
    should be 'bandit useful' and spearman > 0.3."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    pairs = [(i * 0.01, i * 1.0) for i in range(50)]  # n=50, perfect rank
    conn = _patch_conn(pairs)

    with patch("genlab_core.learning.bandit_validation._connect", return_value=conn):
        from genlab_core.learning.bandit_validation import (
            compute_bandit_engagement_correlation,
        )

        result = compute_bandit_engagement_correlation("gaming")

    assert result.n_posts == 50
    assert result.spearman_rank_correlation > 0.95
    assert result.interpretation == "bandit useful"


def test_compute_correlation_low_signal_path(monkeypatch):
    """With fewer than 30 posts, interpretation MUST be 'low signal'
    regardless of how well-correlated the small sample is."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    pairs = [(i * 0.01, i * 1.0) for i in range(10)]  # n=10, perfect rank
    conn = _patch_conn(pairs)
    with patch("genlab_core.learning.bandit_validation._connect", return_value=conn):
        from genlab_core.learning.bandit_validation import (
            compute_bandit_engagement_correlation,
        )

        result = compute_bandit_engagement_correlation("gaming")
    assert result.n_posts == 10
    assert result.interpretation == "low signal"


def test_compute_correlation_ineffective_path(monkeypatch):
    """50 posts with random correlation → 'bandit ineffective'."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    # Constant actual — undefined correlation, no lift
    pairs = [(i * 0.01, 100.0) for i in range(50)]
    conn = _patch_conn(pairs)
    with patch("genlab_core.learning.bandit_validation._connect", return_value=conn):
        from genlab_core.learning.bandit_validation import (
            compute_bandit_engagement_correlation,
        )

        result = compute_bandit_engagement_correlation("gaming")
    assert result.n_posts == 50
    assert result.interpretation == "bandit ineffective"


# ── persist_result ──────────────────────────────────────────────────


def test_persist_result_fails_open_when_dsn_missing(monkeypatch):
    """No DSN → persist_result returns False, never raises."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from genlab_core.learning.bandit_validation import (
        BanditValidationResult,
        persist_result,
    )

    result = BanditValidationResult(niche_id="gaming", n_posts=50)
    assert persist_result(result) is False


def test_persist_result_writes_insert(monkeypatch):
    """Happy path: execute() called with the INSERT SQL + result fields."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    conn = MagicMock()
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda *a: False

    from genlab_core.learning.bandit_validation import (
        BanditValidationResult,
        persist_result,
    )

    result = BanditValidationResult(
        niche_id="gaming",
        n_posts=42,
        spearman_rank_correlation=0.5,
        pearson_correlation=0.45,
        top_quartile_lift=2.1,
        interpretation="bandit useful",
    )

    with patch("genlab_core.learning.bandit_validation._connect", return_value=conn):
        assert persist_result(result) is True

    conn.execute.assert_called_once()
    sql, params = conn.execute.call_args.args
    assert "INSERT INTO bandit_validation" in sql
    assert params == (
        "gaming",
        42,
        0.5,
        0.45,
        2.1,
        "bandit useful",
    )


def test_persist_result_fails_open_on_empty_niche_id():
    """Defensive: empty niche_id → False, no DB call."""
    from genlab_core.learning.bandit_validation import (
        BanditValidationResult,
        persist_result,
    )

    assert persist_result(BanditValidationResult(niche_id="")) is False


# ── check_three_day_ineffective ─────────────────────────────────────


def test_three_day_check_fires_when_all_three_ineffective(monkeypatch):
    """Three consecutive 'bandit ineffective' rows → True."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    rows = [
        {"interpretation": "bandit ineffective"},
        {"interpretation": "bandit ineffective"},
        {"interpretation": "bandit ineffective"},
    ]
    conn = MagicMock()
    cur_result = MagicMock()
    cur_result.fetchall.return_value = rows
    conn.execute.return_value = cur_result
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda *a: False

    from genlab_core.learning.bandit_validation import check_three_day_ineffective

    with patch("genlab_core.learning.bandit_validation._connect", return_value=conn):
        assert check_three_day_ineffective("gaming") is True


def test_three_day_check_does_not_fire_with_mixed_results(monkeypatch):
    """Mixed verdicts → False (no alert)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    rows = [
        {"interpretation": "bandit ineffective"},
        {"interpretation": "developing"},
        {"interpretation": "bandit ineffective"},
    ]
    conn = MagicMock()
    cur_result = MagicMock()
    cur_result.fetchall.return_value = rows
    conn.execute.return_value = cur_result
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda *a: False

    from genlab_core.learning.bandit_validation import check_three_day_ineffective

    with patch("genlab_core.learning.bandit_validation._connect", return_value=conn):
        assert check_three_day_ineffective("gaming") is False


def test_three_day_check_does_not_fire_with_insufficient_history(monkeypatch):
    """<3 rows in window → False (not enough history for verdict)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    rows = [
        {"interpretation": "bandit ineffective"},
        {"interpretation": "bandit ineffective"},
    ]  # only 2 days
    conn = MagicMock()
    cur_result = MagicMock()
    cur_result.fetchall.return_value = rows
    conn.execute.return_value = cur_result
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda *a: False

    from genlab_core.learning.bandit_validation import check_three_day_ineffective

    with patch("genlab_core.learning.bandit_validation._connect", return_value=conn):
        assert check_three_day_ineffective("gaming") is False


# ── run_for_all_niches CLI ──────────────────────────────────────────


def test_run_for_all_niches_returns_zero_on_full_success():
    """All niches succeed → exit code 0."""
    from genlab_core.learning import bandit_validation as mod

    fake_result = mod.BanditValidationResult(
        niche_id="gaming", n_posts=50, interpretation="developing"
    )
    with (
        patch.object(mod, "compute_bandit_engagement_correlation", return_value=fake_result),
        patch.object(mod, "persist_result", return_value=True),
    ):
        rc = mod.run_for_all_niches(("gaming",))
    assert rc == 0


def test_run_for_all_niches_returns_one_on_persist_failure():
    """Any persist failure → exit code 1."""
    from genlab_core.learning import bandit_validation as mod

    fake_result = mod.BanditValidationResult(niche_id="gaming")
    with (
        patch.object(mod, "compute_bandit_engagement_correlation", return_value=fake_result),
        patch.object(mod, "persist_result", return_value=False),
    ):
        rc = mod.run_for_all_niches(("gaming",))
    assert rc == 1


def test_run_for_all_niches_continues_on_per_niche_exception():
    """One niche raising MUST not skip the rest; exit 1 reported."""
    from genlab_core.learning import bandit_validation as mod

    call_count = {"n": 0}

    def fake_compute(niche_id, days=14):
        call_count["n"] += 1
        if niche_id == "gaming":
            raise RuntimeError("simulated DB outage")
        return mod.BanditValidationResult(niche_id=niche_id)

    with (
        patch.object(mod, "compute_bandit_engagement_correlation", side_effect=fake_compute),
        patch.object(mod, "persist_result", return_value=True),
    ):
        rc = mod.run_for_all_niches(("gaming", "sports", "movies"))

    assert call_count["n"] == 3  # all niches attempted
    assert rc == 1  # failure surfaced
