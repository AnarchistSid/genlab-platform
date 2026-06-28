"""PR AG (2026-06-29) — per-platform bandit divergence dashboard card.

Pins for:
  * ``server.core.bandit_platform_divergence_pg.fetch_per_platform_divergence``
    — groups platform-variants under their base arm via PR #641's
    :func:`parse_platform_arm_id`, computes posterior means + deltas,
    applies cold-start floor, fail-OPEN on DB error
  * ``server.api.bandit_platform_divergence`` — endpoint shape, niche_id
    filter, min_n_plays clamping, SR-F allowlist filter

PR #641 (shipped + activated 2026-06-29) writes per-platform bandit arms
(``content_type__platform``) alongside the base ``content_type`` arm. This
card surfaces whether those per-platform variants are DIVERGING from the
base (the whole point of the per-platform split) vs collapsing back.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Dashboard tests sometimes run with cwd outside dashboard/ — make
# the ``server`` package importable explicitly (same pattern as
# test_publishing_health_per_niche.py).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import the core module at test-collection time so ``patch(...)`` can
# resolve its attributes via the standard ModuleType lookup.
import server.core.bandit_platform_divergence_pg  # noqa: E402,F401

# ── Flask app fixtures ────────────────────────────────────────────


def _make_app_with_divergence_bp():
    from flask import Flask
    from server.api.bandit_platform_divergence import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _mock_pg_with_rows(rows: list[dict]) -> MagicMock:
    """Build a MagicMock pg_connect context yielding the given rows.

    Two .execute() calls happen inside the library helper:
      1. SELECT set_config(...) — return value unused
      2. SELECT from bandit_arms — .fetchall() returns the rows
    """
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    set_config_result = MagicMock()
    arms_query_result = MagicMock()
    arms_query_result.fetchall.return_value = rows
    fake_conn.execute.side_effect = [set_config_result, arms_query_result]
    return fake_conn


# ── Pin 1: parse_platform_arm_id correctly identifies variants vs base ──


def test_parse_platform_arm_id_round_trip():
    """parse_platform_arm_id correctly identifies IG/YT variants vs base.

    Foundation pin — if PR #641's parser ever changes its contract
    (e.g. switches to single underscore), the divergence card silently
    stops grouping. This pin catches that drift via the round-trip:
    every variant arm produced by ``platform_arm_id`` must round-trip
    back to its base via ``parse_platform_arm_id``.
    """
    from genlab_core.learning.bandit_platform_split import (
        list_split_platforms,
        parse_platform_arm_id,
        platform_arm_id,
    )

    for base in ("gameplay_clip", "style:sports:bold_claim", "esports_highlight"):
        # Base arm — no platform suffix
        assert parse_platform_arm_id(base) == (base, None)
        # Per-platform variants — round-trip
        for plat in list_split_platforms():
            variant = platform_arm_id(base, plat)
            assert parse_platform_arm_id(variant) == (base, plat)

    # Arms with __ but a non-split-platform suffix → treated as base
    # (legacy/unknown platforms must not be misclassified as variants).
    assert parse_platform_arm_id("foo__tiktok") == ("foo__tiktok", None)


# ── Pin 2: fetch_per_platform_divergence groups variants under base ──


def test_fetch_groups_variants_under_base(monkeypatch):
    """The library groups (base, IG-variant, YT-variant) rows into one row.

    Three DB rows for one (niche, base_arm) → one returned divergence
    dict with base_mean + instagram_mean + youtube_mean populated.
    Mirrors what PR #641 actually writes in prod.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    # Base arm: alpha=42, beta=58, n=100 → mean = 0.42
    # IG variant: alpha=51, beta=49, n=100 → mean = 0.51
    # YT variant: alpha=34, beta=66, n=100 → mean = 0.34
    rows = [
        {
            "niche_id": "sports",
            "arm_id": "gameplay_clip",
            "alpha": 42.0,
            "beta": 58.0,
            "n_plays": 100,
        },
        {
            "niche_id": "sports",
            "arm_id": "gameplay_clip__instagram",
            "alpha": 51.0,
            "beta": 49.0,
            "n_plays": 100,
        },
        {
            "niche_id": "sports",
            "arm_id": "gameplay_clip__youtube",
            "alpha": 34.0,
            "beta": 66.0,
            "n_plays": 100,
        },
    ]
    fake_conn = _mock_pg_with_rows(rows)
    with patch(
        "server.core.bandit_platform_divergence_pg.pg_connect",
        return_value=fake_conn,
    ):
        from server.core.bandit_platform_divergence_pg import (
            fetch_per_platform_divergence,
        )

        result = fetch_per_platform_divergence(min_n_plays=5)

    assert len(result) == 1
    row = result[0]
    assert row["niche_id"] == "sports"
    assert row["base_arm_id"] == "gameplay_clip"
    assert row["base_mean"] == 0.42
    assert row["base_n_plays"] == 100
    assert row["instagram_mean"] == 0.51
    assert row["instagram_n_plays"] == 100
    assert row["youtube_mean"] == 0.34
    assert row["youtube_n_plays"] == 100
    # |0.51 - 0.34| = 0.17
    assert row["ig_yt_delta"] == 0.17
    # 0.51 - 0.42 = 0.09 (IG outperforms base)
    assert row["ig_delta_from_base"] == 0.09
    # 0.34 - 0.42 = -0.08 (YT underperforms base)
    assert row["yt_delta_from_base"] == -0.08


# ── Pin 3: min_n_plays filter excludes cold arms ──


def test_min_n_plays_filter_excludes_cold_arms(monkeypatch):
    """Rows where ALL variants have n_plays < min_n_plays are excluded.

    The filter is "all cold" not "any cold" — partial-cold rows are kept
    so the operator can see divergence emerging.

    Two groups:
      cold_arm: base + IG + YT all n=2 (below default min=5) → excluded
      warm_arm: base n=10, IG n=12, YT n=3 → kept (partial cold but
        warm enough overall)
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    rows = [
        # cold_arm — all below floor
        {
            "niche_id": "gaming",
            "arm_id": "cold_arm",
            "alpha": 1.0,
            "beta": 1.0,
            "n_plays": 2,
        },
        {
            "niche_id": "gaming",
            "arm_id": "cold_arm__instagram",
            "alpha": 1.0,
            "beta": 1.0,
            "n_plays": 2,
        },
        {
            "niche_id": "gaming",
            "arm_id": "cold_arm__youtube",
            "alpha": 1.0,
            "beta": 1.0,
            "n_plays": 2,
        },
        # warm_arm — base + IG warm, YT cold (partial)
        {
            "niche_id": "gaming",
            "arm_id": "warm_arm",
            "alpha": 5.0,
            "beta": 5.0,
            "n_plays": 10,
        },
        {
            "niche_id": "gaming",
            "arm_id": "warm_arm__instagram",
            "alpha": 6.0,
            "beta": 6.0,
            "n_plays": 12,
        },
        {
            "niche_id": "gaming",
            "arm_id": "warm_arm__youtube",
            "alpha": 1.0,
            "beta": 1.0,
            "n_plays": 3,
        },
    ]
    fake_conn = _mock_pg_with_rows(rows)
    with patch(
        "server.core.bandit_platform_divergence_pg.pg_connect",
        return_value=fake_conn,
    ):
        from server.core.bandit_platform_divergence_pg import (
            fetch_per_platform_divergence,
        )

        result = fetch_per_platform_divergence(min_n_plays=5)

    assert len(result) == 1
    # Only warm_arm survives the filter
    assert result[0]["base_arm_id"] == "warm_arm"
    # IG kept (warm), YT kept but partial-cold (n=3 below floor)
    assert result[0]["instagram_n_plays"] == 12
    assert result[0]["youtube_n_plays"] == 3


def test_groups_without_platform_variants_excluded(monkeypatch):
    """A base arm with NO per-platform variants is excluded entirely.

    The card is specifically a divergence view — base-only groups have
    nothing to compare against. Other cards (e.g. LearningLoopCard)
    surface base-only arm posteriors.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    rows = [
        # base only — no IG/YT variants
        {
            "niche_id": "anime",
            "arm_id": "legacy_only_arm",
            "alpha": 10.0,
            "beta": 10.0,
            "n_plays": 20,
        },
        # base + IG variant (no YT) — kept because some variant exists
        {
            "niche_id": "anime",
            "arm_id": "ig_only_arm",
            "alpha": 10.0,
            "beta": 10.0,
            "n_plays": 20,
        },
        {
            "niche_id": "anime",
            "arm_id": "ig_only_arm__instagram",
            "alpha": 15.0,
            "beta": 5.0,
            "n_plays": 20,
        },
    ]
    fake_conn = _mock_pg_with_rows(rows)
    with patch(
        "server.core.bandit_platform_divergence_pg.pg_connect",
        return_value=fake_conn,
    ):
        from server.core.bandit_platform_divergence_pg import (
            fetch_per_platform_divergence,
        )

        result = fetch_per_platform_divergence(min_n_plays=5)

    arm_ids = [r["base_arm_id"] for r in result]
    assert "legacy_only_arm" not in arm_ids
    assert "ig_only_arm" in arm_ids
    # YT slot is None (no variant), IG slot is populated
    ig_only = next(r for r in result if r["base_arm_id"] == "ig_only_arm")
    assert ig_only["youtube_mean"] is None
    assert ig_only["youtube_n_plays"] == 0
    assert ig_only["instagram_mean"] == 0.75  # 15 / (15+5)
    # ig_yt_delta is None when one side missing — frontend renders "—"
    assert ig_only["ig_yt_delta"] is None
    assert ig_only["yt_delta_from_base"] is None


def test_no_dsn_returns_empty(monkeypatch):
    """Fail-OPEN — no DATABASE_URL → empty list, not exception."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from server.core.bandit_platform_divergence_pg import (
        fetch_per_platform_divergence,
    )

    assert fetch_per_platform_divergence() == []


def test_sort_order_by_ig_yt_delta_desc(monkeypatch):
    """Rows sorted by |IG↔YT delta| desc so operator sees most-divergent first."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    # arm_a: delta 0.05; arm_b: delta 0.20; arm_c: delta 0.12
    rows = []
    for base, ig_alpha, yt_alpha in [
        ("arm_a", 50.0, 45.0),  # IG 0.5, YT 0.45 → delta 0.05
        ("arm_b", 60.0, 40.0),  # IG 0.6, YT 0.4 → delta 0.20
        ("arm_c", 56.0, 44.0),  # IG 0.56, YT 0.44 → delta 0.12
    ]:
        rows.append(
            {
                "niche_id": "movies",
                "arm_id": base,
                "alpha": 50.0,
                "beta": 50.0,
                "n_plays": 100,
            }
        )
        rows.append(
            {
                "niche_id": "movies",
                "arm_id": f"{base}__instagram",
                "alpha": ig_alpha,
                "beta": 100.0 - ig_alpha,
                "n_plays": 100,
            }
        )
        rows.append(
            {
                "niche_id": "movies",
                "arm_id": f"{base}__youtube",
                "alpha": yt_alpha,
                "beta": 100.0 - yt_alpha,
                "n_plays": 100,
            }
        )
    fake_conn = _mock_pg_with_rows(rows)
    with patch(
        "server.core.bandit_platform_divergence_pg.pg_connect",
        return_value=fake_conn,
    ):
        from server.core.bandit_platform_divergence_pg import (
            fetch_per_platform_divergence,
        )

        result = fetch_per_platform_divergence(min_n_plays=5)

    assert [r["base_arm_id"] for r in result] == ["arm_b", "arm_c", "arm_a"]


# ── Pin 4: endpoint returns correct shape + status 200 ──


def test_endpoint_returns_shape_and_200(monkeypatch):
    """Endpoint returns {rows, platforms, thresholds, window} + status 200."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = _make_app_with_divergence_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/learning/bandit-platform-divergence")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["rows"] == []
    assert body["platforms"] == ["instagram", "youtube"]
    assert body["thresholds"]["meaningful_delta_pct"] == 5.0
    assert body["thresholds"]["cold_start_below_n"] == 5
    assert body["window"] == "all_time"


def test_endpoint_invalid_min_n_plays_returns_400():
    """Garbage min_n_plays → 400, not 500."""
    app = _make_app_with_divergence_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/learning/bandit-platform-divergence?min_n_plays=not-a-number")
    body = r.get_json() or {}
    assert "min_n_plays" in (body.get("error") or "").lower()


def test_endpoint_unknown_niche_id_returns_404():
    """Unknown niche_id → 404 with helpful message listing valid niches."""
    app = _make_app_with_divergence_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/learning/bandit-platform-divergence?niche_id=fashion")
    assert r.status_code == 404
    body = r.get_json() or {}
    assert "fashion" in (body.get("error") or "")


def test_endpoint_clamps_oversized_min_n_plays():
    """min_n_plays=99999 → clamped to 1000."""
    app = _make_app_with_divergence_bp()
    with patch(
        "server.core.bandit_platform_divergence_pg.fetch_per_platform_divergence",
        return_value=[],
    ) as mock_fetch:
        with app.test_client() as c:
            r = c.get("/api/v1/learning/bandit-platform-divergence?min_n_plays=99999")
    assert r.status_code == 200
    mock_fetch.assert_called_once_with(niche_id=None, min_n_plays=1000)


# ── Pin 5: endpoint respects niche_id query param ──


def test_endpoint_passes_niche_id_to_library():
    """niche_id query param flows through to library call."""
    app = _make_app_with_divergence_bp()
    with patch(
        "server.core.bandit_platform_divergence_pg.fetch_per_platform_divergence",
        return_value=[],
    ) as mock_fetch:
        with app.test_client() as c:
            r = c.get("/api/v1/learning/bandit-platform-divergence?niche_id=sports&min_n_plays=10")
    assert r.status_code == 200
    mock_fetch.assert_called_once_with(niche_id="sports", min_n_plays=10)


def test_endpoint_sr_f_filters_to_allowlist():
    """Restricted operator sees ONLY their allowlist niches' rows."""
    rows = [
        {
            "niche_id": "gaming",
            "base_arm_id": "gameplay_clip",
            "base_mean": 0.4,
            "base_n_plays": 100,
            "instagram_mean": 0.5,
            "instagram_n_plays": 50,
            "youtube_mean": 0.3,
            "youtube_n_plays": 50,
            "ig_yt_delta": 0.2,
            "ig_delta_from_base": 0.1,
            "yt_delta_from_base": -0.1,
        },
        {
            "niche_id": "anime",
            "base_arm_id": "epic_moment",
            "base_mean": 0.5,
            "base_n_plays": 100,
            "instagram_mean": 0.55,
            "instagram_n_plays": 50,
            "youtube_mean": 0.45,
            "youtube_n_plays": 50,
            "ig_yt_delta": 0.1,
            "ig_delta_from_base": 0.05,
            "yt_delta_from_base": -0.05,
        },
    ]
    app = _make_app_with_divergence_bp()
    with (
        patch(
            "server.core.bandit_platform_divergence_pg.fetch_per_platform_divergence",
            return_value=rows,
        ),
        patch(
            "server.auth.niche_allowlist.get_allowed_niches",
            return_value={"gaming"},
        ),
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/learning/bandit-platform-divergence")
    assert r.status_code == 200
    body = r.get_json()["data"]
    niches = {row["niche_id"] for row in body["rows"]}
    assert niches == {"gaming"}


def test_endpoint_handles_import_error_gracefully():
    """If core helper unavailable, endpoint returns shaped empty payload."""
    app = _make_app_with_divergence_bp()
    with patch.dict(
        sys.modules,
        {"server.core.bandit_platform_divergence_pg": None},
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/learning/bandit-platform-divergence")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["rows"] == []
    assert body["platforms"] == ["instagram", "youtube"]
    assert "thresholds" in body
