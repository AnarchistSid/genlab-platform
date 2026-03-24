"""Tests for genlab_core.monetization.commission_sync."""
from __future__ import annotations

import pytest

from genlab_core.monetization.commission_sync import check_rates, _KNOWN_RATES


# ---------------------------------------------------------------------------
# Helpers to build minimal catalog structures
# ---------------------------------------------------------------------------

def _catalog(niches: dict) -> dict:
    return {"niches": niches}


def _niche(products: list) -> dict:
    return {"products": products}


def _product(name: str, category: str, networks: dict) -> dict:
    return {"name": name, "category": category, "networks": networks}


def _network(url: str, commission_pct: float) -> dict:
    return {"url": url, "commission_pct": commission_pct}


# ---------------------------------------------------------------------------
# test_no_changes_when_rates_match
# ---------------------------------------------------------------------------

def test_no_changes_when_rates_match():
    """Catalog rates matching known rates produce an empty change list."""
    catalog = _catalog({
        "gaming": _niche([
            _product(
                "PS5 Console",
                "hardware",
                {
                    "amazon": _network("https://www.amazon.in/dp/B0CY5QW186?tag=***REMOVED***", 3.0),
                    "amazon_us": _network("https://www.amazon.com/dp/B0DJHG2VVS?tag=***REMOVED***", 3.0),
                },
            )
        ])
    })

    changes = check_rates(catalog)
    assert changes == []


def test_no_changes_cuelinks_default_rate():
    """Cuelinks at 7.0% (the _default rate) produces no mismatch."""
    catalog = _catalog({
        "gaming": _niche([
            _product(
                "PS5 Console",
                "hardware",
                {
                    "cuelinks": _network(
                        "https://linksredirect.com/?cid=***REMOVED***&url=test", 7.0
                    ),
                },
            )
        ])
    })

    changes = check_rates(catalog)
    assert changes == []


# ---------------------------------------------------------------------------
# test_detects_rate_mismatch
# ---------------------------------------------------------------------------

def test_detects_rate_mismatch():
    """A catalog rate that differs from the known rate is flagged."""
    catalog = _catalog({
        "gaming": _niche([
            _product(
                "PS5 Console",
                "hardware",
                {
                    # hardware amazon should be 3.0 — this is wrong
                    "amazon": _network("https://www.amazon.in/dp/B0CY5QW186?tag=***REMOVED***", 5.0),
                },
            )
        ])
    })

    changes = check_rates(catalog)
    assert len(changes) == 1
    c = changes[0]
    assert c["product"] == "PS5 Console"
    assert c["network"] == "amazon"
    assert c["catalog_rate"] == 5.0
    assert c["expected_rate"] == 3.0
    assert c["status"] == "rate_mismatch"
    assert c["niche"] == "gaming"


def test_detects_multiple_mismatches():
    """Multiple wrong rates across products are all reported."""
    catalog = _catalog({
        "gaming": _niche([
            _product(
                "PS5 Console",
                "hardware",
                {"amazon": _network("https://www.amazon.in/dp/X?tag=***REMOVED***", 5.0)},
            ),
            _product(
                "Gaming Headset",
                "peripheral",
                {"amazon": _network("https://www.amazon.in/dp/Y?tag=***REMOVED***", 1.0)},
            ),
        ])
    })

    changes = check_rates(catalog)
    # hardware amazon: 5.0 vs 3.0 — mismatch
    # peripheral amazon: 1.0 vs 4.0 — mismatch
    assert len(changes) == 2
    names = {c["product"] for c in changes}
    assert "PS5 Console" in names
    assert "Gaming Headset" in names


def test_detects_mismatch_across_niches():
    """Rate mismatches are detected across multiple niches."""
    catalog = _catalog({
        "gaming": _niche([
            _product(
                "PS5 Console",
                "hardware",
                {"amazon": _network("https://www.amazon.in/dp/X", 9.9)},
            )
        ]),
        "sports": _niche([
            _product(
                "Sports Gear",
                "gear",
                {"amazon": _network("https://www.amazon.in/dp/Y", 9.9)},
            )
        ]),
    })

    changes = check_rates(catalog)
    niches_with_issues = {c["niche"] for c in changes}
    assert "gaming" in niches_with_issues
    assert "sports" in niches_with_issues


# ---------------------------------------------------------------------------
# test_skips_placeholder_urls
# ---------------------------------------------------------------------------

def test_skips_placeholder_urls():
    """Products with example.com URLs are skipped entirely."""
    catalog = _catalog({
        "gaming": _niche([
            _product(
                "Placeholder Product",
                "hardware",
                {
                    # example.com = placeholder — must be skipped
                    "earnkaro": _network("https://example.com/affiliate/earnkaro/ps5", 99.9),
                },
            )
        ])
    })

    changes = check_rates(catalog)
    assert changes == []


def test_skips_example_com_but_checks_real_urls():
    """Only example.com networks are skipped; real URLs are still checked."""
    catalog = _catalog({
        "gaming": _niche([
            _product(
                "PS5 Console",
                "hardware",
                {
                    # This placeholder is skipped
                    "earnkaro": _network("https://example.com/affiliate/ps5", 99.9),
                    # This real URL is checked — rate is wrong
                    "amazon": _network("https://www.amazon.in/dp/X?tag=***REMOVED***", 9.9),
                },
            )
        ])
    })

    changes = check_rates(catalog)
    # Only amazon should be reported (earnkaro skipped)
    assert len(changes) == 1
    assert changes[0]["network"] == "amazon"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_catalog_returns_no_changes():
    """Empty catalog produces no output."""
    assert check_rates({}) == []
    assert check_rates({"niches": {}}) == []


def test_unknown_network_not_in_known_rates_is_skipped():
    """Networks not in _KNOWN_RATES (e.g. amazon_onelink) produce no mismatch."""
    catalog = _catalog({
        "gaming": _niche([
            _product(
                "PS5 Console",
                "hardware",
                {
                    "amazon_onelink": _network(
                        "https://www.amazon.com/dp/X?tag=***REMOVED***&linkCode=ll1", 99.0
                    )
                },
            )
        ])
    })

    changes = check_rates(catalog)
    assert changes == []


def test_unknown_category_with_default_rate():
    """Network with _default rate catches products with unknown categories."""
    catalog = _catalog({
        "movies": _niche([
            _product(
                "Streaming Service",
                "streaming",  # not in cuelinks category map, but _default=7.0 applies
                {
                    "cuelinks": _network("https://linksredirect.com/?cid=***REMOVED***", 3.0),
                },
            )
        ])
    })

    changes = check_rates(catalog)
    # cuelinks has _default=7.0, catalog has 3.0 — should flag
    assert len(changes) == 1
    assert changes[0]["expected_rate"] == 7.0
    assert changes[0]["catalog_rate"] == 3.0


def test_mismatch_tolerance_is_within_001():
    """Rates within 0.01 of each other are not flagged as mismatches."""
    catalog = _catalog({
        "gaming": _niche([
            _product(
                "PS5 Console",
                "hardware",
                # 3.005 is within 0.01 of 3.0 — should NOT flag
                {"amazon": _network("https://www.amazon.in/dp/X?tag=***REMOVED***", 3.005)},
            )
        ])
    })

    changes = check_rates(catalog)
    assert changes == []
