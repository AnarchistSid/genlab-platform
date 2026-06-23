"""Tests for /api/v1/media-kit/<niche_id> endpoint.

The endpoint is decoration over the existing fetch_progress reader +
the sponsorship_readiness tier-computation helpers. Tests focus on:
  - audience summary shape (headline metrics only, sorted by size)
  - tier passes through from PR #481's logic unchanged
  - 404 on invalid niche_id
  - empty data → valid empty kit (cold-start path)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture(autouse=True)
def _reset_sponsorship_cache(monkeypatch):
    """sponsorship_readiness module caches for 60s; the media kit's
    tier computation calls into those helpers. Reset between tests so
    each test sees its mock data, not the prior test's."""
    from server.api import sponsorship_readiness as mod

    mod._cache.clear()
    mod._cache.update({"data": None, "ts": 0.0})
    yield


@pytest.fixture(autouse=True)
def _stub_top_posts(monkeypatch):
    """PR CC: media_kit calls top_posts_pg.fetch_top_posts which opens
    a real Postgres connection. Stub to [] by default so tests don't
    pay the 5s connect_timeout per test invocation. Individual tests
    can override to assert top_posts behavior by re-patching."""
    monkeypatch.setattr(
        "server.core.top_posts_pg.fetch_top_posts",
        lambda niche_id, **kw: [],
    )
    yield


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


_AI_CREATORS_FULL = [
    {
        "niche_id": "ai_creators",
        "platform": "youtube",
        "metric_name": "subscribers",
        "current_value": 1200,
        "target_value": 1000,
        "pct_complete": 120.0,
        "delta_7d": 30,
        "days_to_threshold_est": None,
        "is_threshold_met": True,
    },
    {
        "niche_id": "ai_creators",
        "platform": "youtube",
        "metric_name": "watch_hours_12mo",
        "current_value": 5000,
        "target_value": 4000,
        "pct_complete": 125.0,
        "delta_7d": 100,
        "days_to_threshold_est": None,
        "is_threshold_met": True,
    },
    {
        "niche_id": "ai_creators",
        "platform": "instagram",
        "metric_name": "followers",
        "current_value": 350,
        "target_value": 10000,
        "pct_complete": 3.5,
        "delta_7d": 15,
        "days_to_threshold_est": 600,
        "is_threshold_met": False,
    },
]


class TestHappyPath:
    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_returns_200_with_niche_payload(self, _mock, client):
        """Pin: valid niche_id + populated data → 200 with kit payload."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["niche_id"] == "ai_creators"

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_audience_surfaces_headline_metric_per_platform(self, _mock, client):
        """Pin: per-platform audience surfaces ONLY the headline metric
        (subscribers / followers / fans). Internal monetisation
        metrics like watch_hours_12mo are intentionally NOT in the
        kit — brands don't pitch against watch-hours."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        data = json.loads(resp.data)["data"]
        audience = data["audience"]
        # YouTube: surfaces "subscribers" (not "watch_hours_12mo")
        yt = next(p for p in audience if p["platform"] == "youtube")
        assert yt["metric_name"] == "subscribers"
        assert yt["current_value"] == 1200
        # Instagram: surfaces "followers"
        ig = next(p for p in audience if p["platform"] == "instagram")
        assert ig["metric_name"] == "followers"
        assert ig["current_value"] == 350
        # ONLY 2 platforms returned despite 3 metric rows (watch_hours
        # collapses into the subscribers headline)
        assert {p["platform"] for p in audience} == {"youtube", "instagram"}

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_audience_sorted_by_descending_follower_count(self, _mock, client):
        """Pin: kit's audience LIST reads 'strongest first'. YouTube
        (1200 subs) before Instagram (350 followers) — operator-friendly
        ordering when copy-pasting into outreach emails. The list shape
        (vs a dict) is deliberate: it survives Flask's JSON_SORT_KEYS
        re-ordering."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        data = json.loads(resp.data)["data"]
        platform_order = [p["platform"] for p in data["audience"]]
        assert platform_order == ["youtube", "instagram"]

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_tier_passes_through_from_sponsorship_logic(self, _mock, client):
        """Pin: tier value comes from sponsorship_readiness._compute_tier
        unchanged. Kit and Mission Control card MUST NEVER disagree on
        tier — they share the computation helper by import."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        data = json.loads(resp.data)["data"]
        # YouTube has all metrics met → eligible_now
        assert data["tier"] == "eligible_now"
        assert data["nearest_threshold_days"] == 0

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_monetised_platforms_list_present(self, _mock, client):
        """Pin: monetised_platforms is a sorted list of platforms with
        every metric met. YouTube qualifies (both subs and watch_hours
        met); Instagram doesn't (one metric, not met)."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        data = json.loads(resp.data)["data"]
        assert data["monetised_platforms"] == ["youtube"]

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_generated_at_iso_8601_utc(self, _mock, client):
        """Pin: generated_at is an ISO-8601 UTC timestamp. Frontend
        renders it as the kit's 'data as-of' line; missing tz suffix
        would localise incorrectly across operator timezones."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        data = json.loads(resp.data)["data"]
        gen = data["generated_at"]
        # Either +00:00 or Z suffix is acceptable; Python's isoformat()
        # produces +00:00 from a UTC datetime.
        assert gen.endswith("+00:00") or gen.endswith("Z")


class TestNicheValidation:
    def test_unknown_niche_id_404(self, client):
        """Pin: niche_id not in the closed 5 → 404. Invalid input
        deserves a clear error (vs the cold-start empty-data case
        which is valid state and returns 200)."""
        resp = client.get("/api/v1/media-kit/not_a_real_niche")
        assert resp.status_code == 404
        body = json.loads(resp.data)
        # Error body lists the valid options to help the caller debug
        assert "not_a_real_niche" in body.get("error", "")

    @pytest.mark.parametrize(
        "niche_id",
        ["ai_creators", "gaming", "sports", "movies", "anime"],
    )
    def test_each_known_niche_accepted(self, niche_id, client):
        """Pin: all 5 canonical niche_ids are accepted (no typos)."""
        with patch(
            "server.api.media_kit._pg_fetch_progress",
            return_value=[],
        ):
            resp = client.get(f"/api/v1/media-kit/{niche_id}")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["niche_id"] == niche_id


class TestColdStart:
    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=[],
    )
    def test_empty_data_returns_valid_shell(self, _mock, client):
        """Pin: zero rows for a valid niche → 200 with shell payload.
        Frontend page renders 'data pending' placeholders rather than
        a 404 (which would imply the niche is invalid)."""
        resp = client.get("/api/v1/media-kit/gaming")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["niche_id"] == "gaming"
        assert data["audience"] == []
        assert data["monetised_platforms"] == []
        assert data["tier"] == "tracking"
        assert data["nearest_threshold_days"] is None


class TestFailure:
    @patch(
        "server.api.media_kit._pg_fetch_progress",
        side_effect=RuntimeError("pg connection refused"),
    )
    def test_fetch_exception_returns_500(self, _mock, client):
        """Pin: infra-layer exception → 500 with error message.
        The page can render an error state; silent-200-with-empty-data
        would hide the bug."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        assert resp.status_code == 500


# ───────────────────────────────────────────────────────────
# Portfolio endpoint (PR V, 2026-06-23) — GET /api/v1/media-kit/_all
# ───────────────────────────────────────────────────────────

# Cross-niche mock — covers different tiers so the portfolio summary
# logic can compute eligible_now_count properly.
_PORTFOLIO_MIXED = [
    # ai_creators — eligible_now (YouTube fully met)
    {
        "niche_id": "ai_creators",
        "platform": "youtube",
        "metric_name": "subscribers",
        "current_value": 1500,
        "target_value": 1000,
        "pct_complete": 150.0,
        "delta_7d": 30,
        "days_to_threshold_est": None,
        "is_threshold_met": True,
    },
    {
        "niche_id": "ai_creators",
        "platform": "youtube",
        "metric_name": "watch_hours_12mo",
        "current_value": 4500,
        "target_value": 4000,
        "pct_complete": 112.5,
        "delta_7d": 100,
        "days_to_threshold_est": None,
        "is_threshold_met": True,
    },
    # gaming — within_2_months
    {
        "niche_id": "gaming",
        "platform": "instagram",
        "metric_name": "followers",
        "current_value": 9500,
        "target_value": 10000,
        "pct_complete": 95.0,
        "delta_7d": 50,
        "days_to_threshold_est": 21,
        "is_threshold_met": False,
    },
    # sports — tracking (no positive velocity → tracking)
    {
        "niche_id": "sports",
        "platform": "instagram",
        "metric_name": "followers",
        "current_value": 200,
        "target_value": 10000,
        "pct_complete": 2.0,
        "delta_7d": 0,
        "days_to_threshold_est": 600,
        "is_threshold_met": False,
    },
    # movies + anime intentionally absent — exercise cold-start path
]


class TestPortfolioEndpoint:
    """Pins for GET /api/v1/media-kit/_all (multi-niche aggregate)."""

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_PORTFOLIO_MIXED,
    )
    def test_returns_200_with_all_5_niches(self, _mock, client):
        """Pin: portfolio response ALWAYS carries all 5 niches, even
        the ones with zero monetisationprogress rows. Operator never
        has to apologise to a brand for missing entries."""
        resp = client.get("/api/v1/media-kit/_all")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        niche_ids = [n["niche_id"] for n in data["niches"]]
        assert set(niche_ids) == {
            "ai_creators",
            "gaming",
            "sports",
            "movies",
            "anime",
        }

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_PORTFOLIO_MIXED,
    )
    def test_niche_order_is_stable(self, _mock, client):
        """Pin: niches appear in the stable order
        (ai_creators, gaming, sports, movies, anime) so the printed
        portfolio document layout is deterministic — same A4 layout
        across requests, no reflow when refreshing for re-print."""
        resp = client.get("/api/v1/media-kit/_all")
        data = json.loads(resp.data)["data"]
        order = [n["niche_id"] for n in data["niches"]]
        assert order == ["ai_creators", "gaming", "sports", "movies", "anime"]

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_PORTFOLIO_MIXED,
    )
    def test_per_niche_tier_reuses_single_niche_logic(self, _mock, client):
        """Pin: each portfolio entry's tier matches what the per-niche
        endpoint would return for the same data. Confirms the shared
        ``_build_niche_kit`` helper has not drifted from the
        per-niche route. The kit and portfolio must NEVER disagree on
        a single niche's tier."""
        resp = client.get("/api/v1/media-kit/_all")
        data = json.loads(resp.data)["data"]
        by_id = {n["niche_id"]: n for n in data["niches"]}
        assert by_id["ai_creators"]["tier"] == "eligible_now"
        assert by_id["gaming"]["tier"] == "within_2_months"
        assert by_id["sports"]["tier"] == "tracking"
        # Cold-start niches → tracking with empty audience
        assert by_id["movies"]["tier"] == "tracking"
        assert by_id["movies"]["audience"] == []
        assert by_id["anime"]["tier"] == "tracking"

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_PORTFOLIO_MIXED,
    )
    def test_summary_counts(self, _mock, client):
        """Pin: portfolio summary carries eligible_now_count +
        monetised_platforms_total — these are the headline numbers
        the printed first-page summary table reads from."""
        resp = client.get("/api/v1/media-kit/_all")
        data = json.loads(resp.data)["data"]
        # Only ai_creators is eligible
        assert data["summary"]["eligible_now_count"] == 1
        # Only ai_creators/youtube is fully met
        assert data["summary"]["monetised_platforms_total"] == 1

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_PORTFOLIO_MIXED,
    )
    def test_single_fetch_call_for_all_niches(self, mock_fetch, client):
        """Pin: portfolio uses ONE fetch_progress() call (no niche
        filter) rather than 5 sequential calls. This is the perf
        invariant — operator clicking 'Refresh' on the portfolio kit
        must not multiply DB load by 5x."""
        resp = client.get("/api/v1/media-kit/_all")
        assert resp.status_code == 200
        # Exactly one call, with no niche_id filter
        assert mock_fetch.call_count == 1
        call_kwargs = mock_fetch.call_args.kwargs
        assert "niche_id" not in call_kwargs or call_kwargs.get("niche_id") is None

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=[],
    )
    def test_empty_data_returns_all_cold_start_niches(self, _mock, client):
        """Pin: zero rows in monetisationprogress → 200 + all 5
        niches as cold-start (tracking tier, empty audience). The
        printable document still has its full 5-section shape;
        brand sees ‘growing audience’ tone rather than ‘no data’."""
        resp = client.get("/api/v1/media-kit/_all")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert len(data["niches"]) == 5
        for n in data["niches"]:
            assert n["tier"] == "tracking"
            assert n["audience"] == []
            assert n["monetised_platforms"] == []
        assert data["summary"]["eligible_now_count"] == 0
        assert data["summary"]["monetised_platforms_total"] == 0

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        side_effect=RuntimeError("pg connection refused"),
    )
    def test_portfolio_fetch_exception_returns_500(self, _mock, client):
        """Pin: same fail-loud semantics as the per-niche route —
        infra error returns 500, NOT a silent-200 with empty data."""
        resp = client.get("/api/v1/media-kit/_all")
        assert resp.status_code == 500


# ───────────────────────────────────────────────────────────────
# PR CC (2026-06-23) — top-post embed in media kit
# ───────────────────────────────────────────────────────────────

_TOP_POSTS_FIXTURE = [
    {
        "platform": "youtube",
        "post_id": "abc123",
        "post_url": "https://youtube.com/shorts/abc123",
        "published_at": "2026-06-20T14:00:00+00:00",
        "views": 12500,
        "likes": 850,
        "comments": 42,
        "score": 12500 + 850 * 5 + 42 * 10,
    },
    {
        "platform": "facebook",
        "post_id": "fb_456",
        "post_url": "https://www.facebook.com/fb_456",
        "published_at": "2026-06-18T09:00:00+00:00",
        "views": 6000,
        "likes": 400,
        "comments": 20,
        "score": 6000 + 400 * 5 + 20 * 10,
    },
]


class TestTopPostsEmbed:
    """Pins for PR CC — top-posts embed in media kit response."""

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_top_posts_present_in_response(self, _mock, client, monkeypatch):
        """Pin: per-niche kit response carries a ``top_posts`` array.
        Default-fixture returns [] (empty) — that's a valid shape
        the frontend renders as 'no data yet' rather than missing
        section entirely."""
        resp = client.get("/api/v1/media-kit/ai_creators")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert "top_posts" in data
        assert isinstance(data["top_posts"], list)

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_top_posts_populated_when_fetcher_returns_data(self, _mock, client, monkeypatch):
        """Pin: when fetch_top_posts returns rows, those rows appear in
        the response's top_posts array — full passthrough with all
        fields preserved (platform, post_url, score, etc.)."""
        monkeypatch.setattr(
            "server.core.top_posts_pg.fetch_top_posts",
            lambda niche_id, **kw: list(_TOP_POSTS_FIXTURE),
        )
        resp = client.get("/api/v1/media-kit/ai_creators")
        data = json.loads(resp.data)["data"]
        assert len(data["top_posts"]) == 2
        # First entry — full field passthrough
        first = data["top_posts"][0]
        assert first["platform"] == "youtube"
        assert first["post_url"] == "https://youtube.com/shorts/abc123"
        assert first["views"] == 12500
        assert "score" in first

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=_AI_CREATORS_FULL,
    )
    def test_top_posts_fetch_exception_is_non_blocking(self, _mock, client, monkeypatch):
        """Pin: fetch_top_posts raising mid-call DOES NOT block the
        rest of the kit. top_posts becomes [] but tier + audience +
        monetised_platforms all still render. Top-posts is a kit-
        quality enhancement, not a kit-blocker."""

        def boom(niche_id, **kw):
            raise RuntimeError("simulated pg outage")

        monkeypatch.setattr("server.core.top_posts_pg.fetch_top_posts", boom)
        resp = client.get("/api/v1/media-kit/ai_creators")
        # Still 200 — kit renders the rest of the data
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["top_posts"] == []
        # Tier + audience still present (non-blocking failure)
        assert data["tier"] == "eligible_now"
        assert len(data["audience"]) > 0


class TestTopPostsURLDerivation:
    """Pins for the per-platform URL-derivation helper. Critical
    invariant — wrong URL shape would publish dead links in the kit."""

    def test_youtube_url_shape(self):
        from server.core.top_posts_pg import _derive_post_url

        assert _derive_post_url("youtube", "abc123") == "https://youtube.com/shorts/abc123"

    def test_facebook_url_shape(self):
        from server.core.top_posts_pg import _derive_post_url

        assert _derive_post_url("facebook", "fb_post_123") == "https://www.facebook.com/fb_post_123"

    def test_twitter_url_shape(self):
        from server.core.top_posts_pg import _derive_post_url

        assert _derive_post_url("x_twitter", "1234") == "https://twitter.com/i/web/status/1234"

    def test_instagram_returns_none_v1(self):
        """Pin: IG requires shortcode (not post_id) — return None at v1
        so the kit doesn't render dead links. Deferred to v2."""
        from server.core.top_posts_pg import _derive_post_url

        assert _derive_post_url("instagram", "1234567890") is None

    def test_threads_returns_none_v1(self):
        from server.core.top_posts_pg import _derive_post_url

        assert _derive_post_url("threads", "anything") is None

    def test_tiktok_returns_none_v1(self):
        from server.core.top_posts_pg import _derive_post_url

        assert _derive_post_url("tiktok", "anything") is None

    def test_prefixed_post_id_strips_platform_prefix(self):
        """Pin: PR #486 W3.2 normalised some post_ids to
        ``{platform}:{id}`` shape. _derive_post_url strips the prefix
        before building the URL — otherwise we'd produce
        ``https://youtube.com/shorts/youtube:abc123`` (broken)."""
        from server.core.top_posts_pg import _derive_post_url

        assert _derive_post_url("youtube", "youtube:abc123") == "https://youtube.com/shorts/abc123"

    def test_empty_post_id_returns_none(self):
        from server.core.top_posts_pg import _derive_post_url

        assert _derive_post_url("youtube", "") is None
        assert _derive_post_url("youtube", "youtube:") is None


class TestRouteCollision:
    """The portfolio route is ``/_all``. Flask routes static segments
    BEFORE dynamic ones, so ``/_all`` should match the portfolio
    handler rather than being interpreted as ``niche_id="_all"``.
    These pins guard that priority."""

    @patch(
        "server.api.media_kit._pg_fetch_progress",
        return_value=[],
    )
    def test_underscore_all_hits_portfolio_not_per_niche(self, _mock, client):
        """Pin: GET /_all returns the portfolio shape (with ``niches``
        + ``summary`` keys), NOT a 404 from the per-niche validator
        treating ``_all`` as an unknown niche_id."""
        resp = client.get("/api/v1/media-kit/_all")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        # Portfolio shape carries ``niches`` (list); per-niche shape
        # would carry ``niche_id`` (string) instead.
        assert "niches" in data
        assert isinstance(data["niches"], list)
        assert "niche_id" not in data
