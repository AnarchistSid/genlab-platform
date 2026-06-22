"""Pin: _best_network never picks cuelinks + defaults to IN-geo (2026-06-22).

After the 2026-06-22 affiliate audit revealed two compounding bugs in
``dashboard/server/api/links.py::_best_network``:

  1. cuelinks was getting picked for IN-geo visitors because it ranks
     higher on commission_pct (7% > amazon 3%) — but the 2026-06-14
     audit had ALREADY determined cuelinks earns ₹0/click because it
     doesn't inject Amazon tags. The pipeline-side resolver was fixed
     that day; the dashboard's selector was a separate code path that
     kept silently picking cuelinks.

  2. Default ``country=""`` was treated as "no geo info" and fell
     through to amazon_us. When the operator's Cloudflare header
     wasn't reaching the Flask handler (Caddy passthrough gap), this
     silently routed Indian visitors to amazon.com with US tag —
     amazon.in then geo-redirected them and stripped the US tag → ₹0
     commission on the entire path.

These pins lock in the fix.
"""

from __future__ import annotations

from server.api.links import _best_network


# Synthetic networks dict — covers the 3 networks the audit found in catalog
def _fake_networks(*, with_cuelinks: bool = True) -> dict:
    networks = {
        "amazon": {
            "url": "https://www.amazon.in/dp/B0XYZ?tag=aspirehub-21",
            "commission_pct": 3.0,
        },
        "amazon_us": {
            "url": "https://www.amazon.com/dp/B0XYZ?tag=aspirehub06-20",
            "commission_pct": 3.0,
        },
    }
    if with_cuelinks:
        networks["cuelinks"] = {
            "url": "https://linksredirect.com/?cid=272705&source=linkkit&url=https%3A%2F%2Fwww.amazon.in%2Fdp%2FB0XYZ",
            "commission_pct": 7.0,
        }
    return networks


class TestCuelinksPermanentlySkipped:
    def test_in_geo_never_picks_cuelinks(self):
        """Pin: even when cuelinks has higher commission_pct (7% > 3%),
        IN-geo visitors get amazon (not cuelinks). Pre-fix this returned
        cuelinks because of the commission_pct ranking."""
        name, data = _best_network(_fake_networks(with_cuelinks=True), country="IN")
        assert name == "amazon", (
            f"_best_network picked '{name}' for IN-geo — should be 'amazon'. "
            f"cuelinks earns ₹0/click per 2026-06-14 audit; should never be picked."
        )

    def test_no_geo_never_picks_cuelinks(self):
        """Pin: same guard for default-no-geo path."""
        name, _ = _best_network(_fake_networks(with_cuelinks=True), country="")
        assert name != "cuelinks"

    def test_us_geo_never_picks_cuelinks(self):
        """Pin: US-geo also never picks cuelinks even if it's the only
        network in dict (cuelinks earns ₹0 regardless of geo)."""
        cuelinks_only = {
            "cuelinks": {
                "url": "https://linksredirect.com/?cid=X&url=Y",
                "commission_pct": 7.0,
            },
            "amazon_us": {
                "url": "https://www.amazon.com/dp/B0XYZ?tag=aspirehub06-20",
                "commission_pct": 3.0,
            },
        }
        name, _ = _best_network(cuelinks_only, country="US")
        assert name == "amazon_us"

    def test_fallback_path_also_skips_cuelinks(self):
        """Pin: the fallback branch (when geo-filtering produces nothing)
        also skips cuelinks. Catches the regression where someone
        re-enables cuelinks in the fallback only."""
        # Synthetic: only cuelinks exists + no geo match for IN.
        # The fallback path should return ("", {}) rather than cuelinks.
        cuelinks_only = {
            "cuelinks": {
                "url": "https://linksredirect.com/?cid=X&url=Y",
                "commission_pct": 7.0,
            },
        }
        name, data = _best_network(cuelinks_only, country="IN")
        assert name == ""
        assert data == {}


class TestDefaultGeoFallback:
    def test_empty_country_defaults_to_in(self, monkeypatch):
        """Pin: when CF-IPCountry isn't passed (Caddy stripping etc.),
        default to IN — matches the operator's primary audience.
        Pre-fix this defaulted to amazon_us (US tag → IN amazon drops it
        → ₹0)."""
        monkeypatch.delenv("GENLAB_DEFAULT_AFFILIATE_GEO", raising=False)
        name, _ = _best_network(_fake_networks(with_cuelinks=False), country="")
        assert name == "amazon", f"Default geo should be IN → 'amazon', got '{name}'"

    def test_env_override_to_us_works(self, monkeypatch):
        """Pin: operators with US-skewed audience can override via
        env. Catches the regression where someone hardcodes IN."""
        monkeypatch.setenv("GENLAB_DEFAULT_AFFILIATE_GEO", "US")
        name, _ = _best_network(_fake_networks(with_cuelinks=False), country="")
        assert name == "amazon_us"

    def test_explicit_country_overrides_env(self, monkeypatch):
        """Pin: when a real CF-IPCountry header IS present, it wins
        over the env default. Important: a US visitor on an IN-default
        deployment still gets amazon_us."""
        monkeypatch.setenv("GENLAB_DEFAULT_AFFILIATE_GEO", "IN")
        name, _ = _best_network(_fake_networks(with_cuelinks=False), country="US")
        assert name == "amazon_us"

    def test_lowercase_country_handled(self, monkeypatch):
        """Pin: country values are normalized to upper-case before
        comparison. Some Cloudflare configs send lowercase."""
        monkeypatch.delenv("GENLAB_DEFAULT_AFFILIATE_GEO", raising=False)
        name, _ = _best_network(_fake_networks(with_cuelinks=False), country="in")
        assert name == "amazon"
