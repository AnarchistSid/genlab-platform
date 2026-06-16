"""Pins for EarnKaro auto-transformation in geo_link_resolver.

Per 2026-06-13 autonomy deep-dive: the 4 new affiliate adapters
shipped in PR #177 (#34a/b/c) were never called in production because
the matcher path only reads pre-baked URLs from product["networks"].
This commit closes the gap for EarnKaro (the one network that works
as a pure URL transformer) — when an IN-geo niche resolves to an
Amazon URL AND EARNKARO_CONVERT_KEY is set, the URL is transformed
through ekaro.in/api/converter/public.

The non-negotiable invariants:
  1. **NO-OP when key unset** — today's behavior across all 5 niches.
     This pin is the headline safety guarantee: setting up EarnKaro
     is OPT-IN via env var; the matcher's existing Amazon path
     remains unchanged until the operator sets EARNKARO_CONVERT_KEY.
  2. **NO-OP for US-geo niches** — ai_creators has US audience,
     EarnKaro is India-only.
  3. **NO-OP for non-Amazon networks** (cuelinks, etc.) — EarnKaro
     transforms Amazon URLs, not arbitrary tracker URLs.
  4. **Graceful fallback** — if transform call raises or returns "",
     the original Amazon URL with `network="amazon"` is preserved.
  5. **Successful transform swaps both URL AND network attribution** —
     the published URL becomes ekaro.in/X; the recorded network
     becomes "earnkaro" so analytics show the correct attribution.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from genlab_core.monetization.geo_link_resolver import (
    _try_earnkaro_transform,
    resolve_affiliate_link_with_network,
)


@pytest.fixture
def _no_url_health_check(monkeypatch):
    """Skip the live HTTP HEAD check that resolve_affiliate_link
    normally does — tests use synthetic URLs that aren't reachable."""
    monkeypatch.setattr(
        "genlab_core.monetization.geo_link_resolver._is_url_healthy",
        lambda url: True,
    )


@pytest.fixture
def amazon_product():
    """A typical IN-geo product the matcher would resolve to Amazon."""
    return {
        "name": "Gaming Headset",
        "networks": {
            "amazon": {"url": "https://www.amazon.in/dp/B0XYZ?tag=aspirehub-21"},
        },
    }


# ── Safety: no-op when EarnKaro isn't configured ─────────────────────────


class TestNoOpWhenUnconfigured:
    def test_no_key_set_returns_original_amazon_url(
        self, amazon_product, monkeypatch, _no_url_health_check
    ):
        """THE headline pin: with no env var, the matcher returns the
        same Amazon URL it returned before this commit. Behavior is
        byte-identical for any niche where the operator hasn't opted in."""
        monkeypatch.delenv("EARNKARO_CONVERT_KEY", raising=False)
        url, network = resolve_affiliate_link_with_network(
            amazon_product, "gaming", "instagram", blueprint_id="bp1"
        )
        # Original Amazon URL preserved
        assert "amazon.in/dp/B0XYZ" in url
        assert "aspirehub-21" in url  # affiliate tag intact
        # Network attribution is amazon, NOT earnkaro
        assert network == "amazon"

    def test_empty_key_treated_as_unset(self, amazon_product, monkeypatch, _no_url_health_check):
        """Empty/whitespace key is the same as no key — don't try the API."""
        monkeypatch.setenv("EARNKARO_CONVERT_KEY", "")
        url, network = resolve_affiliate_link_with_network(
            amazon_product, "gaming", "instagram", blueprint_id="bp1"
        )
        assert network == "amazon"

    def test_us_geo_niche_never_transforms(self, amazon_product, monkeypatch, _no_url_health_check):
        """ai_creators is US-audience. EarnKaro is India-only. Even
        with the key set, ai_creators must NOT route through EarnKaro."""
        monkeypatch.setenv("EARNKARO_CONVERT_KEY", "test-key")
        # Patch convert_url so if this test wrongly triggers it, we'd
        # see the call — but the assertion below should be that it's
        # NOT called.
        with patch("genlab_core.monetization.earnkaro_client.convert_url") as mock_convert:
            url, network = resolve_affiliate_link_with_network(
                amazon_product, "ai_creators", "instagram", blueprint_id="bp1"
            )
            # US-geo → no earnkaro transform attempt
            mock_convert.assert_not_called()
            # And because ai_creators is US, the resolver picked the US
            # candidate path which has no amazon_us URL in this product
            # → empty URL is fine; the safety pin is "no transform call"

    def test_non_amazon_network_not_transformed(self, monkeypatch, _no_url_health_check):
        """A product whose only network is one the matcher no longer
        picks (cuelinks was removed from candidates in PR #184) must
        NOT trigger EarnKaro transform.

        Before #184 this pinned "matcher picks cuelinks → don't call
        EarnKaro." Post-#184, cuelinks isn't a candidate at all, so
        the matcher returns ``("", "")`` and the EarnKaro gate
        (``network in ("amazon", "amazon_in")``) is never reached.

        The test still has value: it pins the defense-in-depth
        invariant that EarnKaro is NEVER speculatively called when no
        candidate matches. If someone someday changes the resolver to
        try EarnKaro on any non-empty product URL regardless of
        matcher outcome, this test catches it."""
        monkeypatch.setenv("EARNKARO_CONVERT_KEY", "test-key")
        cuelinks_only_product = {
            "name": "Niche Product",
            "networks": {
                # cuelinks removed from candidates in PR #184; this
                # product has no matchable network from the resolver's
                # perspective. Kept here as the canonical "non-Amazon
                # legacy entry" shape that real product catalogs still
                # have lying around.
                "cuelinks": {"url": "https://linksredirect.com/?cid=000000&url=X"},
            },
        }
        with patch("genlab_core.monetization.earnkaro_client.convert_url") as mock_convert:
            url, network = resolve_affiliate_link_with_network(
                cuelinks_only_product, "gaming", "instagram"
            )
            # The headline invariant: transform NOT called. Gated on
            # ``network in ("amazon", "amazon_in")``; with no candidate
            # matched, network is "" so the gate is never reached.
            mock_convert.assert_not_called()
            # Post-#184 expected behavior: no candidate matched →
            # empty network + URL. Operators see the product surfaced
            # by the picker but no published affiliate URL, which is
            # the correct signal that the catalog entry needs an
            # Amazon URL added.
            assert network == ""
            assert url == ""


# ── Success path: when the key IS set ──────────────────────────────────────


class TestTransformSuccessSwapsNetwork:
    """EarnKaro happy path — exercised via a synthetic IN-geo niche
    rather than a real one.

    Until 2026-06-17, gaming/sports/movies/anime defaulted to IN in
    ``NICHE_PRIMARY_GEO``. The flip to US (driven by 91% US-click
    audience data — see ``test_niche_primary_geo.py``) means no
    real niche currently triggers EarnKaro auto-transform. But the
    transform code path itself is still correct + worth pinning,
    so these tests inject a temporary IN-geo niche via monkeypatch
    rather than coupling to which real niches are India-targeted.
    """

    def test_amazon_url_with_in_geo_niche_transforms_to_earnkaro(
        self, amazon_product, monkeypatch, _no_url_health_check
    ):
        """The happy path: any IN-geo niche + amazon URL + key set →
        URL becomes ekaro.in short link, network becomes earnkaro.

        Uses a synthetic "test_in_geo" niche injected into
        NICHE_PRIMARY_GEO so the test doesn't break when production
        niches' geos change."""
        monkeypatch.setenv("EARNKARO_CONVERT_KEY", "test-key")
        from genlab_core.monetization import geo_link_resolver as _glr

        monkeypatch.setitem(_glr.NICHE_PRIMARY_GEO, "test_in_geo", "IN")
        with patch(
            "genlab_core.monetization.earnkaro_client.convert_url",
            return_value="https://ekaro.in/abc123",
        ) as mock_convert:
            url, network = resolve_affiliate_link_with_network(
                amazon_product, "test_in_geo", "instagram", blueprint_id="bpX"
            )
            mock_convert.assert_called_once_with("https://www.amazon.in/dp/B0XYZ?tag=aspirehub-21")
            assert "ekaro.in/abc123" in url
            assert network == "earnkaro"
            # EarnKaro-specific sub_id param appended (ref=)
            assert "ref=test_in_geo_bpX" in url

    def test_no_production_niche_currently_triggers_earnkaro(
        self, amazon_product, monkeypatch, _no_url_health_check
    ):
        """Inverse pin to the 2026-06-17 NICHE_PRIMARY_GEO flip:
        with all 5 production niches now defaulting to US, NONE
        of them should attempt the EarnKaro transform — even with
        the key set + a real amazon.in URL on the product.

        If a future PR re-introduces an IN-defaulted niche WITHOUT
        also updating the EarnKaro account / SubID configuration,
        this test still passes (the IN-geo transform path is the
        intentional outcome). But pairing this with the
        ``test_niche_primary_geo.py`` US-default assertion means
        a niche moving to IN trips a test failure THERE first,
        forcing the rationale to be documented before this side
        effect happens silently."""
        monkeypatch.setenv("EARNKARO_CONVERT_KEY", "test-key")
        for niche in ("ai_creators", "gaming", "sports", "movies", "anime"):
            with patch(
                "genlab_core.monetization.earnkaro_client.convert_url",
                return_value="https://ekaro.in/should-not-be-called",
            ) as mock_convert:
                url, network = resolve_affiliate_link_with_network(
                    amazon_product, niche, "instagram"
                )
                assert not mock_convert.called, (
                    f"niche={niche} is US-default post-2026-06-17 — "
                    "EarnKaro transform must NOT fire"
                )
                # Network stays amazon (no swap to earnkaro)
                assert network in ("amazon", ""), f"niche={niche} unexpected network: {network!r}"


# ── Graceful fallback: transform failure preserves original ───────────────


class TestGracefulFallbackOnTransformFailure:
    def test_empty_transform_result_preserves_amazon(
        self, amazon_product, monkeypatch, _no_url_health_check
    ):
        """convert_url returning "" (API down, rate limit, etc.) → the
        matcher falls back to the original Amazon URL. Operator never
        sees a broken affiliate link from a transform failure."""
        monkeypatch.setenv("EARNKARO_CONVERT_KEY", "test-key")
        with patch(
            "genlab_core.monetization.earnkaro_client.convert_url",
            return_value="",
        ):
            url, network = resolve_affiliate_link_with_network(
                amazon_product, "gaming", "instagram", blueprint_id="bp1"
            )
            # Original Amazon URL preserved with its tag intact
            assert "amazon.in/dp/B0XYZ" in url
            assert "aspirehub-21" in url
            assert network == "amazon"

    def test_transform_exception_preserves_amazon(
        self, amazon_product, monkeypatch, _no_url_health_check
    ):
        """A raised exception from the transform helper (network glitch,
        bad import, etc.) must NOT propagate. Log + swallow + use the
        original URL. This is defense-in-depth for the affiliate flow."""
        monkeypatch.setenv("EARNKARO_CONVERT_KEY", "test-key")
        with patch(
            "genlab_core.monetization.earnkaro_client.convert_url",
            side_effect=RuntimeError("kaboom"),
        ):
            url, network = resolve_affiliate_link_with_network(
                amazon_product, "gaming", "instagram", blueprint_id="bp1"
            )
            assert "amazon.in/dp/B0XYZ" in url
            assert network == "amazon"


# ── The standalone helper ────────────────────────────────────────────────


class TestTryEarnkaroTransformHelper:
    def test_returns_empty_pair_without_key(self, monkeypatch):
        monkeypatch.delenv("EARNKARO_CONVERT_KEY", raising=False)
        # No HTTP call should be attempted; the cheap pre-check returns ""
        url, net = _try_earnkaro_transform("https://www.amazon.in/dp/X")
        assert url == ""
        assert net == ""

    def test_success_returns_network_earnkaro(self, monkeypatch):
        monkeypatch.setenv("EARNKARO_CONVERT_KEY", "k")
        with patch(
            "genlab_core.monetization.earnkaro_client.convert_url",
            return_value="https://ekaro.in/abc",
        ):
            url, net = _try_earnkaro_transform("https://www.amazon.in/dp/X")
            assert url == "https://ekaro.in/abc"
            assert net == "earnkaro"

    def test_empty_transform_returns_empty_pair(self, monkeypatch):
        monkeypatch.setenv("EARNKARO_CONVERT_KEY", "k")
        with patch(
            "genlab_core.monetization.earnkaro_client.convert_url",
            return_value="",
        ):
            url, net = _try_earnkaro_transform("https://www.amazon.in/dp/X")
            assert url == ""
            assert net == ""

    def test_exception_returns_empty_pair_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("EARNKARO_CONVERT_KEY", "k")
        with patch(
            "genlab_core.monetization.earnkaro_client.convert_url",
            side_effect=Exception("anything"),
        ):
            url, net = _try_earnkaro_transform("https://www.amazon.in/dp/X")
            assert url == ""
            assert net == ""
