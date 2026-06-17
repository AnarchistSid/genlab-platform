"""Tests for the dynamic NICHE_PRIMARY_GEO resolver added 2026-06-17.

PR #277 hardcoded all 5 niches → US based on a 90-day snapshot of
``affiliate_clicks.country``. This module's ``_resolve_niche_geo``
computes the same answer from live data and overrides the hardcoded
defaults when the audience shifts.

Pins:
- Dominant country (≥60% share, ≥10 clicks total) overrides hardcoded
- Split audience (no clear winner) falls back to hardcoded
- Insufficient data (<10 clicks) falls back to hardcoded
- Empty result set falls back to hardcoded
- DB outage falls back to hardcoded (fail-OPEN)
- Cache hits within TTL skip the query
- Cache expiry triggers a fresh query
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monetization import geo_link_resolver as glr


def _reset_cache():
    glr._dynamic_geo_cache.clear()


class TestComputeDynamicGeo:
    def _patch_db(self, rows: list[tuple[str, int]] | None = None, raise_exc=False):
        """Patch psycopg.connect to return the given country/clicks rows."""

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def execute(self, *a, **kw):
                return None

            def fetchall(self):
                return rows or []

        class _Conn:
            def __enter__(self):
                if raise_exc:
                    raise RuntimeError("simulated DB outage")
                return self

            def __exit__(self, *a):
                return None

            def cursor(self):
                return _Cur()

        def _connect(*a, **kw):
            if raise_exc:
                raise RuntimeError("simulated DB outage")
            return _Conn()

        return patch("psycopg.connect", _connect)

    def test_returns_dominant_country(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        with self._patch_db(rows=[("US", 27), ("IN", 0)]):
            assert glr._compute_dynamic_geo("movies") == "US"

    def test_60pct_threshold_required(self, monkeypatch):
        """50/50 split → no clear winner → returns None → caller falls
        back to hardcoded. Prevents flapping when the audience is
        genuinely split."""
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        with self._patch_db(rows=[("US", 10), ("IN", 10)]):
            assert glr._compute_dynamic_geo("gaming") is None

    def test_min_clicks_threshold_required(self, monkeypatch):
        """<10 clicks total → not enough signal → returns None.
        Prevents a single click from flipping the geo."""
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        with self._patch_db(rows=[("IN", 5)]):
            assert glr._compute_dynamic_geo("anime") is None

    def test_empty_results_returns_none(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        with self._patch_db(rows=[]):
            assert glr._compute_dynamic_geo("sports") is None

    def test_db_outage_returns_none(self, monkeypatch):
        """Fail-open: a transient DB outage must NEVER break link
        resolution. Returns None → caller uses hardcoded default."""
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        with self._patch_db(raise_exc=True):
            assert glr._compute_dynamic_geo("movies") is None

    def test_missing_database_url_returns_none(self, monkeypatch):
        """No DATABASE_URL set → can't query → returns None gracefully."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        # No psycopg call attempted; just early return
        assert glr._compute_dynamic_geo("movies") is None

    def test_country_normalized_uppercase(self, monkeypatch):
        """Cloudflare CF-IPCountry returns 'US'/'IN' — but defensive
        upper() guards against any future writer that stores 'us'/'in'."""
        monkeypatch.setenv("DATABASE_URL", "dummy://")
        with self._patch_db(rows=[("us", 100)]):
            assert glr._compute_dynamic_geo("anime") == "US"


class TestResolveNicheGeo:
    def test_dynamic_wins_when_present(self, monkeypatch):
        _reset_cache()
        # Pretend dynamic resolver returned 'IN' for a niche hardcoded as 'US'
        with patch.object(glr, "_compute_dynamic_geo", return_value="IN"):
            assert glr._resolve_niche_geo("gaming") == "IN"

    def test_hardcoded_used_when_dynamic_returns_none(self, monkeypatch):
        _reset_cache()
        # Pretend dynamic resolver couldn't decide
        with patch.object(glr, "_compute_dynamic_geo", return_value=None):
            assert glr._resolve_niche_geo("gaming") == glr.NICHE_PRIMARY_GEO["gaming"]

    def test_unknown_niche_defaults_us(self, monkeypatch):
        _reset_cache()
        with patch.object(glr, "_compute_dynamic_geo", return_value=None):
            # Unknown niche not in NICHE_PRIMARY_GEO dict → fallback "US"
            assert glr._resolve_niche_geo("brand-new-niche") == "US"

    def test_cache_hit_skips_query(self, monkeypatch):
        _reset_cache()
        mock_compute = MagicMock(return_value="IN")
        with patch.object(glr, "_compute_dynamic_geo", mock_compute):
            # First call → query fires
            assert glr._resolve_niche_geo("gaming") == "IN"
            assert mock_compute.call_count == 1
            # Second call within TTL → cache hit, no query
            assert glr._resolve_niche_geo("gaming") == "IN"
            assert mock_compute.call_count == 1

    def test_cache_expiry_triggers_fresh_query(self, monkeypatch):
        _reset_cache()
        mock_compute = MagicMock(return_value="US")
        with patch.object(glr, "_compute_dynamic_geo", mock_compute):
            glr._resolve_niche_geo("movies")
            assert mock_compute.call_count == 1
            # Fast-forward past TTL
            old_ts = glr._dynamic_geo_cache["movies"][1]
            glr._dynamic_geo_cache["movies"] = (
                "US",
                old_ts - (glr._DYNAMIC_GEO_TTL_SECONDS + 60),
            )
            glr._resolve_niche_geo("movies")
            assert mock_compute.call_count == 2

    def test_cache_isolated_per_niche(self, monkeypatch):
        _reset_cache()
        mock_compute = MagicMock(side_effect=lambda n: "IN" if n == "gaming" else "US")
        with patch.object(glr, "_compute_dynamic_geo", mock_compute):
            assert glr._resolve_niche_geo("gaming") == "IN"
            assert glr._resolve_niche_geo("movies") == "US"
            # Both cached independently
            assert glr._resolve_niche_geo("gaming") == "IN"
            assert glr._resolve_niche_geo("movies") == "US"
            assert mock_compute.call_count == 2


class TestIntegrationWithCallSite:
    """The resolver is used in resolve_affiliate_link_with_network.
    Pin that the dynamic override changes URL selection per niche."""

    def test_dynamic_in_geo_picks_amazon_in_url(self, monkeypatch):
        _reset_cache()
        # Simulate a niche where audience is heavily Indian today
        with (
            patch.object(glr, "_compute_dynamic_geo", return_value="IN"),
            patch.object(glr, "_is_url_healthy", return_value=True),
        ):
            product = {
                "name": "Gaming Mouse",
                "networks": {
                    "amazon": {"url": "https://www.amazon.in/dp/B0XYZ?tag=aspirehub-21"},
                    "amazon_us": {"url": "https://www.amazon.com/dp/B0XYZ?tag=aspirehub06-20"},
                },
            }
            url, network = glr.resolve_affiliate_link_with_network(
                product, "gaming", "instagram", blueprint_id="bp1"
            )
            # Dynamic IN → should prefer the amazon.in URL
            assert "amazon.in" in url, f"expected amazon.in in URL, got {url}"
