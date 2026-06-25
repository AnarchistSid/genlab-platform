"""PR #553 (2026-06-25): SR-F wire pass 14 — Mission Control overview.

The /api/v1/cross-niche/overview endpoint has a nested response shape
with 4 niche-scoped surfaces:

  niches[]            — per-niche cards (pending, published, best perf)
  schedule_today[]    — items with niche_id
  niche_daily_reach{} — keyed by niche_id (sparkline data)
  global.*            — aggregate counts (totals, reach, agents)

Pre-PR-553 a restricted operator received the full unfiltered overview
— cross-tenant info disclosure on review queues, published counts,
schedule, reach trends, and the "X awaiting review (oldest: Yh)"
banner pointing to blueprints they can't open.

The carve-out lives in ``_scope_overview_to_allowlist`` and runs AFTER
the shared 120s cache (deepcopy preserves cache integrity for
subsequent unrestricted requests). Globals are recomputed from the
filtered niches list; DB aggregates (reach/likes/comments) take a
scoped fallback: total_reach reconstructed from filtered
niche_daily_reach (14-day window vs global 30-day), total_likes /
total_comments fail-secure to 0.

Why platform_health is NOT trimmed: operators need to know if a
platform is globally degraded even when their niche doesn't publish
to it (cross-platform dependencies; e.g. their gaming niche may
share Meta tokens with the company's other accounts).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ── source pins ────────────────────────────────────────────────────


def test_source_pin_scope_helper_present():
    """The deepcopy + 4-surface carve-out helper exists with the
    expected name + signature. Pin the function name so future
    refactors that rename or inline it break the test (signals
    a wider review is required)."""
    import server.api.overview as mod

    src = Path(mod.__file__).read_text()
    assert "def _scope_overview_to_allowlist(data: dict, allowed: set[str]) -> dict:" in src
    assert "PR #553" in src
    # The deepcopy is the cache-safety guarantee — pin its presence
    assert "copy.deepcopy(data)" in src


def test_source_pin_view_calls_helper_after_cache():
    """The carve-out MUST run AFTER cache read, not inside _build_overview.
    Otherwise the cache stores restricted-shape data and the next admin
    request gets trimmed totals. Pin the call site."""
    import server.api.overview as mod

    src = Path(mod.__file__).read_text()
    assert "_scope_overview_to_allowlist(data, _allowed)" in src
    # Confirm the helper is invoked AFTER cache return path
    cache_idx = src.find('_overview_cache["data"]')
    scope_idx = src.find("_scope_overview_to_allowlist(data, _allowed)")
    assert cache_idx != -1 and scope_idx != -1
    assert cache_idx < scope_idx


def test_source_pin_per_niche_totals_precision():
    """PR #555 (2026-06-25): the v1 fail-secure-to-0 for likes/comments
    is gone — they're now summed from niche_totals_30d (per-niche 30d
    breakdown materialised by the upgraded GROUP BY query). Pin both
    the new sum shape and that the old `= 0` line is gone — guards
    against a future refactor accidentally re-introducing the v1
    compromise."""
    import server.api.overview as mod

    src = Path(mod.__file__).read_text()
    # New shape: sum from filtered niche_totals_30d for all 3 metrics
    assert (
        'int(t.get("reach") or 0) for nid, t in niche_totals_30d.items() if nid in allowed' in src
    )
    assert (
        'int(t.get("likes") or 0) for nid, t in niche_totals_30d.items() if nid in allowed' in src
    )
    assert (
        'int(t.get("comments") or 0) for nid, t in niche_totals_30d.items() if nid in allowed'
        in src
    )
    # The v1 compromise lines must NOT survive (guard against accidental revert)
    assert 'g["total_likes"] = 0' not in src
    assert 'g["total_comments"] = 0' not in src
    # Anchor the precision PR for future archaeology
    assert "PR #555" in src


def test_source_pin_niche_totals_30d_query_group_by():
    """The upgraded SQL must GROUP BY niche_id so per-niche totals
    materialise alongside the globals. Pin both the GROUP BY clause
    and the in-Python sum that produces the global numbers from
    the per-niche rowset."""
    import server.api.overview as mod

    src = Path(mod.__file__).read_text()
    assert "GROUP BY niche_id" in src
    assert "niche_totals_30d.values()" in src
    assert 'sum(v["reach"] for v in niche_totals_30d.values())' in src


# ── behavioral pins ────────────────────────────────────────────────


def _scope_user_to_gaming(monkeypatch):
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )


def _make_app():
    from flask import Flask
    from server.api.overview import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _build_fake_overview() -> dict:
    """Synthetic overview payload with 3 niches — gaming, anime, sports."""
    return {
        "generated_at": "2026-06-25T00:00:00+00:00",
        "prefect_connected": False,
        "global": {
            "total_pending_review": 30,
            "total_render_retry": 12,
            "total_operator_review": 18,
            "oldest_operator_review_age_hours": 24,
            "total_published_today": 9,
            "total_archived": 6,
            "auto_archive_today": {
                "total": 6,
                "by_reason": {"weak_hook": 3, "duplicate": 2, "unknown": 1},
                "pass_rate": 0.75,
                "pass_rate_note": "global window",
            },
            "agents_running": 3,
            "platform_health": {"instagram": "ok", "youtube": "ok"},
            "total_reach": 100000,
            "total_likes": 5000,
            "total_comments": 200,
            "configured_platform_count": 5,
        },
        "niches": [
            {
                "id": "gaming",
                "display_name": "CriticalRush",
                "pending_review": 10,
                "render_retry_count": 4,
                "operator_review_count": 6,
                "published_today": 3,
                "archived_today": 2,
                "pipeline_status": "running",
            },
            {
                "id": "anime",
                "display_name": "FrameDrift",
                "pending_review": 12,
                "render_retry_count": 5,
                "operator_review_count": 7,
                "published_today": 4,
                "archived_today": 3,
                "pipeline_status": "running",
            },
            {
                "id": "sports",
                "display_name": "ClutchWire",
                "pending_review": 8,
                "render_retry_count": 3,
                "operator_review_count": 5,
                "published_today": 2,
                "archived_today": 1,
                "pipeline_status": "idle",
            },
        ],
        "schedule_today": [
            {"id": "bp-1", "niche_id": "gaming", "slot_time_ist": "12:00"},
            {"id": "bp-2", "niche_id": "anime", "slot_time_ist": "12:30"},
            {"id": "bp-3", "niche_id": "sports", "slot_time_ist": "13:00"},
            {"id": "bp-4", "niche_id": "gaming", "slot_time_ist": "14:00"},
        ],
        "niche_daily_reach": {
            "gaming": [
                {"date": "2026-06-24", "reach": 1000},
                {"date": "2026-06-25", "reach": 1500},
            ],
            "anime": [{"date": "2026-06-24", "reach": 5000}, {"date": "2026-06-25", "reach": 7000}],
            "sports": [{"date": "2026-06-25", "reach": 800}],
        },
        # PR #555 (2026-06-25): per-niche 30-day breakdown materialised
        # alongside the daily-reach sparkline. Drives the scope helper's
        # precise total_likes / total_comments computation for scoped
        # operators (replaces the v1 fail-secure-to-0 from PR #553).
        # Values intentionally != global.total_* because in production
        # the global SQL aggregate can include legacy niches not in the
        # current registry — a small but realistic mismatch.
        "niche_totals_30d": {
            "gaming": {"reach": 10000, "likes": 500, "comments": 50},
            "anime": {"reach": 40000, "likes": 2000, "comments": 120},
            "sports": {"reach": 5000, "likes": 300, "comments": 20},
        },
    }


def _reset_cache(monkeypatch):
    import server.api.overview as mod

    monkeypatch.setattr(mod, "_overview_cache", {"data": None, "ts": 0.0})


def test_overview_restricted_filters_niches_and_schedule(monkeypatch):
    """Gaming-scoped operator → niches/schedule/daily_reach trimmed to gaming."""
    _reset_cache(monkeypatch)
    _scope_user_to_gaming(monkeypatch)

    fake = _build_fake_overview()
    app = _make_app()
    with patch("server.api.overview._build_overview", return_value=fake):
        with app.test_client() as c:
            r = c.get("/api/v1/cross-niche/overview")
    assert r.status_code == 200
    body = r.get_json()["data"]
    # niches[] only gaming
    assert [n["id"] for n in body["niches"]] == ["gaming"]
    # schedule_today only gaming items (2 of 4)
    assert {s["niche_id"] for s in body["schedule_today"]} == {"gaming"}
    assert len(body["schedule_today"]) == 2
    # niche_daily_reach only gaming key
    assert set(body["niche_daily_reach"].keys()) == {"gaming"}


def test_overview_restricted_recomputes_globals(monkeypatch):
    """Globals must be recomputed from filtered niches — not the pre-cached
    global aggregates which include all 3 niches."""
    _reset_cache(monkeypatch)
    _scope_user_to_gaming(monkeypatch)

    fake = _build_fake_overview()
    app = _make_app()
    with patch("server.api.overview._build_overview", return_value=fake):
        with app.test_client() as c:
            r = c.get("/api/v1/cross-niche/overview")
    body = r.get_json()["data"]
    g = body["global"]
    # gaming only: render_retry 4, operator_review 6, pending 10, published 3, archived 2
    assert g["total_render_retry"] == 4
    assert g["total_operator_review"] == 6
    assert g["total_pending_review"] == 10
    assert g["total_published_today"] == 3
    assert g["total_archived"] == 2
    # agents_running: gaming has pipeline_status=running → 1 (anime running excluded)
    assert g["agents_running"] == 1
    # PR #555: total_reach / total_likes / total_comments now come from
    # the per-niche 30-day breakdown (was 14d sparkline for reach, was
    # fail-secure-0 for likes/comments). gaming's 30d totals from
    # fixture: reach=10000, likes=500, comments=50.
    assert g["total_reach"] == 10000
    assert g["total_likes"] == 500
    assert g["total_comments"] == 50
    # SR-F scoped flag + note (note no longer carries the 14d/zero caveat)
    assert g["_sr_f_scoped"] is True
    assert "30-day per-niche sums (admin parity)" in g["_sr_f_note"]
    assert "14-day" not in g["_sr_f_note"]
    assert "unavailable in scoped mode" not in g["_sr_f_note"]


def test_overview_restricted_auto_archive_breakdown_zeroed(monkeypatch):
    """The by_reason breakdown would leak cross-tenant archive patterns
    (e.g. anime has many duplicate-rejections this week). Scoped users
    get an empty by_reason + recomputed total + None pass_rate."""
    _reset_cache(monkeypatch)
    _scope_user_to_gaming(monkeypatch)

    fake = _build_fake_overview()
    app = _make_app()
    with patch("server.api.overview._build_overview", return_value=fake):
        with app.test_client() as c:
            r = c.get("/api/v1/cross-niche/overview")
    body = r.get_json()["data"]
    aa = body["global"]["auto_archive_today"]
    assert aa["total"] == 2  # recomputed from gaming
    assert aa["by_reason"] == {}
    assert aa["pass_rate"] is None
    assert "scoped per allowlist" in aa["pass_rate_note"]


def test_overview_unrestricted_admin_unchanged(monkeypatch):
    """Backward compat: admin sees the full overview shape + values."""
    _reset_cache(monkeypatch)
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    fake = _build_fake_overview()
    app = _make_app()
    with patch("server.api.overview._build_overview", return_value=fake):
        with app.test_client() as c:
            r = c.get("/api/v1/cross-niche/overview")
    body = r.get_json()["data"]
    # No trimming — 3 niches all present
    assert len(body["niches"]) == 3
    assert len(body["schedule_today"]) == 4
    assert set(body["niche_daily_reach"].keys()) == {"gaming", "anime", "sports"}
    # Globals unchanged (no _sr_f_scoped key present)
    g = body["global"]
    assert g["total_reach"] == 100000
    assert g["total_likes"] == 5000
    assert "_sr_f_scoped" not in g


def test_overview_cache_unchanged_after_restricted_request(monkeypatch):
    """The shared cache must stay unrestricted-shape so the NEXT
    admin request still sees the full overview. Pin: a restricted
    request followed by an admin request returns the full data to
    the admin (the deepcopy guarantee)."""
    import server.api.overview as mod

    _reset_cache(monkeypatch)

    fake = _build_fake_overview()
    app = _make_app()

    # First: restricted gaming_op request — populates cache
    _scope_user_to_gaming(monkeypatch)
    with patch.object(mod, "_build_overview", return_value=fake):
        with app.test_client() as c:
            r1 = c.get("/api/v1/cross-niche/overview")
    assert r1.status_code == 200
    assert len(r1.get_json()["data"]["niches"]) == 1

    # Second: admin request — should NOT see scoped shape from cache.
    # Patch the username lookup back to admin (sees no allowlist env).
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "admin",
    )
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)
    with app.test_client() as c:
        r2 = c.get("/api/v1/cross-niche/overview")
    assert r2.status_code == 200
    # All 3 niches visible — cache was deepcopy-safe
    assert len(r2.get_json()["data"]["niches"]) == 3


def test_overview_restricted_oldest_age_cleared_when_zero(monkeypatch):
    """oldest_operator_review_age_hours from the global cache reflects
    the oldest across ALL niches. If the operator's allowlist has 0
    operator_review items, the banner should NOT claim "Yh waiting"
    (it would point to blueprints the operator can't open)."""
    _reset_cache(monkeypatch)
    # Scope to a niche with NO operator_review items by zeroing gaming's count
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )

    fake = _build_fake_overview()
    fake["niches"][0]["operator_review_count"] = 0  # gaming → 0 op-review

    app = _make_app()
    with patch("server.api.overview._build_overview", return_value=fake):
        with app.test_client() as c:
            r = c.get("/api/v1/cross-niche/overview")
    body = r.get_json()["data"]
    assert body["global"]["total_operator_review"] == 0
    assert body["global"]["oldest_operator_review_age_hours"] is None


# ── PR #555 (per-niche 30d totals) behavioral pins ───────────────


def test_overview_restricted_niche_totals_30d_trimmed(monkeypatch):
    """The niche_totals_30d map itself must be trimmed by allowlist —
    otherwise a restricted operator could enumerate cross-tenant
    niche IDs by reading the response payload keys (info disclosure
    even without revealing the values)."""
    _reset_cache(monkeypatch)
    _scope_user_to_gaming(monkeypatch)

    fake = _build_fake_overview()
    app = _make_app()
    with patch("server.api.overview._build_overview", return_value=fake):
        with app.test_client() as c:
            r = c.get("/api/v1/cross-niche/overview")
    body = r.get_json()["data"]
    # Only gaming key in niche_totals_30d
    assert set(body["niche_totals_30d"].keys()) == {"gaming"}
    # Value structure intact
    assert body["niche_totals_30d"]["gaming"] == {"reach": 10000, "likes": 500, "comments": 50}


def test_overview_admin_sees_full_niche_totals_30d(monkeypatch):
    """Backward compat: admin sees the full niche_totals_30d map
    with all niches (drives any future per-niche analytics surface
    that wants 30-day rollups)."""
    _reset_cache(monkeypatch)
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    fake = _build_fake_overview()
    app = _make_app()
    with patch("server.api.overview._build_overview", return_value=fake):
        with app.test_client() as c:
            r = c.get("/api/v1/cross-niche/overview")
    body = r.get_json()["data"]
    assert set(body["niche_totals_30d"].keys()) == {"gaming", "anime", "sports"}


def test_overview_scoped_totals_match_unrestricted_sum(monkeypatch):
    """If an admin's allowlist were the universe of all 3 niches, the
    scoped totals would equal the per-niche sum. Pin the math by
    summing the fixture's niche_totals_30d directly."""
    _reset_cache(monkeypatch)
    # Bespoke username with allowlist covering all 3 niches
    monkeypatch.setenv("REVIEW_AUTH_USER", "all_niche_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_ALL_NICHE_OP", "gaming,anime,sports")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "all_niche_op",
    )

    fake = _build_fake_overview()
    expected_reach = sum(t["reach"] for t in fake["niche_totals_30d"].values())
    expected_likes = sum(t["likes"] for t in fake["niche_totals_30d"].values())
    expected_comments = sum(t["comments"] for t in fake["niche_totals_30d"].values())

    app = _make_app()
    with patch("server.api.overview._build_overview", return_value=fake):
        with app.test_client() as c:
            r = c.get("/api/v1/cross-niche/overview")
    body = r.get_json()["data"]
    g = body["global"]
    assert g["total_reach"] == expected_reach
    assert g["total_likes"] == expected_likes
    assert g["total_comments"] == expected_comments
    assert g["_sr_f_scoped"] is True
