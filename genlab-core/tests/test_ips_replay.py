"""Tests for the IPS counterfactual replay library + CLI.

10 pins covering loader filters, IPS math, edge cases, and the CLI's
empty-data exit code. All tests mock the DB cursor — no live psycopg
calls; this suite runs in the same hermetic envelope as the rest of
``genlab-core/tests/``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from genlab_core.learning.ips_replay import (
    MIN_DECISIONS_FOR_ESTIMATE,
    DecisionRecord,
    compute_ips_per_arm,
    load_decisions,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_decision(
    *,
    arm_id: str = "style:gaming:question",
    propensity: float = 0.5,
    reward: float | None = 0.4,
) -> DecisionRecord:
    return DecisionRecord(
        decision_id="dec_1",
        niche_id="gaming",
        arm_id=arm_id,
        propensity=propensity,
        reward=reward,
        decided_at=datetime(2026, 6, 28, tzinfo=UTC),
        platform="instagram",
    )


# ---------------------------------------------------------------------------
# Loader tests
# ---------------------------------------------------------------------------


class _SQLCapture:
    """Minimal fake psycopg connection that records the SQL + params.

    We patch ``pg_connect`` (the helper genlab_core uses for all
    tenant-scoped reads) to return this object. The real
    ``_query_pending_feedback`` body then executes against it.
    """

    def __init__(self, return_rows: list[dict] | None = None) -> None:
        self.sql: str = ""
        self.params: list = []
        self._rows = return_rows or []

    # Connection-context-manager protocol.
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, *args, **kwargs):
        return self

    # Cursor-context-manager protocol (same object — minimal but enough).
    def execute(self, sql, params=None):
        self.sql = sql
        self.params = list(params) if params else []

    def fetchall(self):
        return list(self._rows)


def _patch_pg_connect(monkeypatch, capture: _SQLCapture) -> None:
    """Patch the pg_connect call site inside ips_replay."""
    monkeypatch.setattr(
        "genlab_core.learning.ips_replay.pg_connect",
        lambda *a, **kw: capture,
        raising=False,
    )
    # DATABASE_URL must be set or the function returns early.
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")


def test_load_decisions_returns_only_rows_with_propensity(monkeypatch):
    """The SELECT must include ``WHERE propensity IS NOT NULL``. Without
    this filter, legacy rows (pre-v3q4r5s6t7u8 migration) would have
    None propensity and crash the IPS denominator with div-by-zero.
    """
    cap = _SQLCapture(return_rows=[])
    _patch_pg_connect(monkeypatch, cap)

    load_decisions()
    assert "propensity IS NOT NULL" in cap.sql


def test_load_decisions_respects_niche_filter(monkeypatch):
    """Passing ``niche_id='gaming'`` must inject a niche_id filter
    into the SQL and into the params list."""
    cap = _SQLCapture(return_rows=[])
    _patch_pg_connect(monkeypatch, cap)

    load_decisions(niche_id="gaming")
    assert "niche_id = %s" in cap.sql
    assert "gaming" in cap.params


def test_load_decisions_respects_since_filter(monkeypatch):
    """Passing ``since=...`` must inject a publish_time filter +
    the datetime value into params."""
    cap = _SQLCapture(return_rows=[])
    _patch_pg_connect(monkeypatch, cap)

    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    load_decisions(since=cutoff)
    assert "publish_time >= %s" in cap.sql
    assert cutoff in cap.params


# ---------------------------------------------------------------------------
# IPS math tests
# ---------------------------------------------------------------------------


def test_compute_ips_basic_two_arm_split():
    """Hand-computable IPS for a 2-arm, equal-N scenario.

    Arm A: 12 decisions, propensity=0.5 throughout, reward=0.4 each.
        IPS = (1/12) * 12 * (0.4 / 0.5) = 0.8
        naive = 0.4
        lift = 0.8 / 0.4 - 1 = +1.0 (+100%)

    Arm B: 10 decisions, propensity=0.5, reward=0.2 each.
        IPS = (1/10) * 10 * (0.2 / 0.5) = 0.4
        naive = 0.2
        lift = +1.0
    """
    decisions: list[DecisionRecord] = []
    for _ in range(12):
        decisions.append(_make_decision(arm_id="A", propensity=0.5, reward=0.4))
    for _ in range(10):
        decisions.append(_make_decision(arm_id="B", propensity=0.5, reward=0.2))

    out = compute_ips_per_arm(decisions, min_decisions=5)
    assert set(out) == {"A", "B"}
    assert out["A"].ips_reward == pytest.approx(0.8, rel=1e-9)
    assert out["A"].naive_reward == pytest.approx(0.4, rel=1e-9)
    assert out["A"].relative_lift == pytest.approx(1.0, rel=1e-9)
    assert out["B"].ips_reward == pytest.approx(0.4, rel=1e-9)
    assert out["B"].naive_reward == pytest.approx(0.2, rel=1e-9)


def test_compute_ips_truncates_at_min_propensity():
    """A propensity of 1e-5 (below the 1e-3 floor) must be clamped.

    Without truncation: weight = 1/1e-5 = 100000.
    With truncation at 1e-3: weight = 1/1e-3 = 1000.
    Single decision: IPS = reward * weight / N = 0.5 * 1000 / 1 = 500.

    Pinning the truncated value proves the clamp is active. Without
    the clamp, the same decision would compute 50000 instead.
    """
    decisions = [_make_decision(arm_id="C", propensity=1e-5, reward=0.5)]
    # min_decisions=1 so the single observation is scored.
    out = compute_ips_per_arm(decisions, min_decisions=1)
    assert out["C"].ips_reward == pytest.approx(500.0, rel=1e-6)
    # The exposed weight must reflect the truncated value too.
    assert out["C"].weights == [pytest.approx(1000.0)]


def test_compute_ips_returns_none_for_arm_below_min_decisions():
    """An arm with 3 decisions when min_decisions=10 gets ips_reward=None
    but n_decisions / n_with_reward still populated for visibility."""
    decisions = [_make_decision(arm_id="rare", propensity=0.3, reward=0.5) for _ in range(3)]
    out = compute_ips_per_arm(decisions, min_decisions=10)
    assert out["rare"].n_decisions == 3
    assert out["rare"].n_with_reward == 3
    assert out["rare"].ips_reward is None
    assert out["rare"].naive_reward is None
    assert out["rare"].relative_lift is None


def test_compute_ips_handles_no_reward_decisions():
    """Decisions with reward=None must be counted in n_decisions but
    excluded from the IPS denominator. Mixed populations must not
    crash."""
    decisions = []
    # 5 with reward
    for _ in range(5):
        decisions.append(_make_decision(arm_id="mixed", propensity=0.5, reward=0.3))
    # 7 without (48h window still open)
    for _ in range(7):
        decisions.append(_make_decision(arm_id="mixed", propensity=0.5, reward=None))

    out = compute_ips_per_arm(decisions, min_decisions=3)
    assert out["mixed"].n_decisions == 12
    assert out["mixed"].n_with_reward == 5
    # IPS computed from the 5 rewarded decisions only.
    # weight = 1/0.5 = 2; IPS = (5 * 0.3 * 2) / 5 = 0.6
    assert out["mixed"].ips_reward == pytest.approx(0.6, rel=1e-9)


def test_relative_lift_is_ips_over_naive_minus_one():
    """Math sanity check that ``relative_lift = ips/naive - 1``.

    Uses non-uniform propensities so IPS ≠ naive.

    3 decisions:
        (p=0.8, r=1.0), (p=0.5, r=0.0), (p=0.2, r=0.4)

    naive = (1.0 + 0.0 + 0.4) / 3 = 0.46666...
    IPS = (1.0/0.8 + 0/0.5 + 0.4/0.2) / 3 = (1.25 + 0 + 2.0) / 3 = 1.08333...
    lift = 1.08333 / 0.46666 - 1 = 1.32142857...
    """
    decisions = [
        _make_decision(arm_id="x", propensity=0.8, reward=1.0),
        _make_decision(arm_id="x", propensity=0.5, reward=0.0),
        _make_decision(arm_id="x", propensity=0.2, reward=0.4),
    ]
    out = compute_ips_per_arm(decisions, min_decisions=2)
    expected_lift = (out["x"].ips_reward or 0.0) / (out["x"].naive_reward or 1.0) - 1
    assert out["x"].relative_lift == pytest.approx(expected_lift, rel=1e-9)
    # Pin the numeric value too, in case both numerator and denominator drift together.
    assert out["x"].relative_lift == pytest.approx(1.3214285714, rel=1e-6)


def test_effective_sample_size_drops_with_uneven_weights():
    """Uniform weights → ESS = N exactly.
    Concentrated weights → ESS << N.
    """
    # Uniform: 10 decisions all at p=0.5 → weight 2 each.
    uniform = [_make_decision(arm_id="u", propensity=0.5, reward=0.3) for _ in range(10)]
    out_uniform = compute_ips_per_arm(uniform, min_decisions=5)
    assert out_uniform["u"].effective_sample_size == pytest.approx(10.0, rel=1e-9)

    # Concentrated: 9 at p=0.5 + 1 at p=0.01.
    # weights = [2]*9 + [100]; sum=118; sum2 = 9*4 + 10000 = 10036
    # ESS = 118^2 / 10036 ≈ 1.387 (very small — one outlier dominates)
    concentrated = [_make_decision(arm_id="c", propensity=0.5, reward=0.3) for _ in range(9)]
    concentrated.append(_make_decision(arm_id="c", propensity=0.01, reward=0.3))
    out_conc = compute_ips_per_arm(concentrated, min_decisions=5)
    assert out_conc["c"].effective_sample_size < 5.0
    assert out_conc["c"].effective_sample_size < out_uniform["u"].effective_sample_size


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_cli_exits_zero_on_empty_data(monkeypatch, capsys):
    """When the loader returns [], the CLI must still exit 0 (empty
    data is a valid result, not an error). The output must include
    a helpful message about why the data might be empty.

    The CLI lives at ``scripts/ips_replay.py`` (sibling to other
    ``scripts/`` CLIs like ``archive_stale_drafted.py``). It's NOT
    importable as ``scripts.ips_replay`` because ``scripts/`` is not
    a package — load via importlib like ``test_check_rls_policy_drift``.
    """
    import importlib.util
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "ips_replay.py"
    spec = importlib.util.spec_from_file_location("scripts_ips_replay", script_path)
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    sys.modules["scripts_ips_replay"] = cli
    spec.loader.exec_module(cli)

    monkeypatch.setattr(cli, "load_decisions", lambda **kw: [])

    exit_code = cli.main(["--niche", "gaming", "--window-days", "7"])
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "no decisions" in captured.out.lower()


# ---------------------------------------------------------------------------
# Extra defensive pin: the constants themselves
# ---------------------------------------------------------------------------


def test_min_decisions_constant_is_at_least_one():
    """Defensive: a future drop to 0 would make ``n_with_reward >= 0``
    trivially true and cause every empty-reward arm to compute IPS
    over an empty sum (div-by-zero).
    """
    assert MIN_DECISIONS_FOR_ESTIMATE >= 1
