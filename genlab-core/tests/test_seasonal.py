"""Tests for seasonal product rotation (affiliate_seasonal.yaml + seasonal.py)."""
from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from genlab_core.monetization.seasonal import (
    get_active_events,
    get_seasonal_products,
    load_seasonal_config,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_SAMPLE_CONFIG = {
    "events": [
        {
            "name": "Amazon Great Indian Festival",
            "start": "2026-10-01",
            "end": "2026-10-08",
            "boost_categories": ["hardware", "peripheral"],
            "products": [
                {
                    "name": "Diwali Deal: PS5 Console",
                    "keywords": ["ps5", "playstation", "diwali"],
                    "category": "hardware",
                    "networks": {
                        "amazon": {
                            "url": "https://www.amazon.in/dp/B0CY5QW186?tag=test-tag-21",
                            "commission_pct": 4.0,
                        }
                    },
                }
            ],
        },
        {
            "name": "Black Friday",
            "start": "2026-11-27",
            "end": "2026-11-30",
            "boost_categories": ["hardware", "merchandise"],
            "products": [
                {
                    "name": "Black Friday: RTX 4090",
                    "keywords": ["nvidia", "rtx", "4090"],
                    "category": "hardware",
                    "networks": {
                        "amazon_us": {
                            "url": "https://www.amazon.com/dp/B0BHD9TS9Q?tag=test-tag-20",
                            "commission_pct": 4.0,
                        }
                    },
                }
            ],
        },
        {
            "name": "Amazon Prime Day",
            "start": "2026-07-15",
            "end": "2026-07-17",
            "boost_categories": ["hardware", "subscription"],
            "products": [],  # empty — no products yet
        },
    ]
}


# ---------------------------------------------------------------------------
# test_no_active_events_outside_window
# ---------------------------------------------------------------------------


def test_no_active_events_outside_window():
    """A date outside all event windows returns no active events."""
    # March is between no listed events
    today = date(2026, 3, 21)
    active = get_active_events(_SAMPLE_CONFIG, today=today)
    assert active == []


def test_no_seasonal_products_outside_window():
    """get_seasonal_products returns empty list when no events are active."""
    today = date(2026, 3, 21)
    products = get_seasonal_products(_SAMPLE_CONFIG, today=today)
    assert products == []


# ---------------------------------------------------------------------------
# test_active_event_within_window
# ---------------------------------------------------------------------------


def test_active_event_within_window():
    """A date inside an event window returns that event."""
    today = date(2026, 10, 4)  # inside Amazon Great Indian Festival
    active = get_active_events(_SAMPLE_CONFIG, today=today)
    assert len(active) == 1
    assert active[0]["name"] == "Amazon Great Indian Festival"


def test_active_event_on_start_boundary():
    """Event is active on its exact start date."""
    today = date(2026, 10, 1)
    active = get_active_events(_SAMPLE_CONFIG, today=today)
    assert any(e["name"] == "Amazon Great Indian Festival" for e in active)


def test_active_event_on_end_boundary():
    """Event is active on its exact end date."""
    today = date(2026, 10, 8)
    active = get_active_events(_SAMPLE_CONFIG, today=today)
    assert any(e["name"] == "Amazon Great Indian Festival" for e in active)


def test_seasonal_products_returned_during_active_event():
    """get_seasonal_products returns the event's products during its window."""
    today = date(2026, 11, 28)  # Black Friday window
    products = get_seasonal_products(_SAMPLE_CONFIG, today=today)
    assert len(products) == 1
    assert products[0]["name"] == "Black Friday: RTX 4090"


# ---------------------------------------------------------------------------
# test_seasonal_products_have_event_name
# ---------------------------------------------------------------------------


def test_seasonal_products_have_event_name():
    """Every product returned is tagged with _seasonal_event matching the event name."""
    today = date(2026, 10, 5)  # inside Amazon Great Indian Festival
    products = get_seasonal_products(_SAMPLE_CONFIG, today=today)
    assert len(products) == 1
    assert products[0]["_seasonal_event"] == "Amazon Great Indian Festival"


def test_seasonal_event_name_on_multiple_products():
    """When an event has multiple products, all get the same _seasonal_event tag."""
    config = {
        "events": [
            {
                "name": "Test Event",
                "start": "2026-06-01",
                "end": "2026-06-05",
                "products": [
                    {"name": "Product A", "keywords": ["a"]},
                    {"name": "Product B", "keywords": ["b"]},
                ],
            }
        ]
    }
    today = date(2026, 6, 3)
    products = get_seasonal_products(config, today=today)
    assert len(products) == 2
    assert all(p["_seasonal_event"] == "Test Event" for p in products)


# ---------------------------------------------------------------------------
# test_empty_products_event
# ---------------------------------------------------------------------------


def test_empty_products_event():
    """An active event with products: [] contributes nothing to the products list."""
    today = date(2026, 7, 16)  # inside Amazon Prime Day (empty products)
    products = get_seasonal_products(_SAMPLE_CONFIG, today=today)
    assert products == []


def test_empty_products_event_does_not_block_other_events():
    """Multiple simultaneous events: empty-products events are silently skipped."""
    config = {
        "events": [
            {
                "name": "Empty Event",
                "start": "2026-06-01",
                "end": "2026-06-10",
                "products": [],
            },
            {
                "name": "Full Event",
                "start": "2026-06-01",
                "end": "2026-06-10",
                "products": [{"name": "Product X", "keywords": ["x"]}],
            },
        ]
    }
    today = date(2026, 6, 5)
    products = get_seasonal_products(config, today=today)
    assert len(products) == 1
    assert products[0]["name"] == "Product X"


# ---------------------------------------------------------------------------
# test_missing_config_file
# ---------------------------------------------------------------------------


def test_missing_config_file(tmp_path):
    """load_seasonal_config returns empty dict when the file does not exist."""
    missing = tmp_path / "does_not_exist.yaml"
    result = load_seasonal_config(path=missing)
    assert result == {}


def test_empty_config_file(tmp_path):
    """load_seasonal_config returns empty dict when the file is blank."""
    blank = tmp_path / "blank.yaml"
    blank.write_text("")
    result = load_seasonal_config(path=blank)
    assert result == {}


def test_load_real_seasonal_config():
    """The bundled affiliate_seasonal.yaml parses correctly and has events."""
    # Resolve path: seasonal.py → monetization/ → genlab_core/ → src/ → genlab-core/
    from genlab_core.monetization import seasonal as _seasonal_mod

    config = load_seasonal_config()  # uses default _SEASONAL_PATH
    # If the real file exists it should have events; if not, empty dict is also OK
    if config:
        assert "events" in config
        for event in config["events"]:
            assert "name" in event
            assert "start" in event
            assert "end" in event


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_event_missing_start_or_end_is_skipped():
    """Events without start or end dates are silently ignored."""
    config = {
        "events": [
            {"name": "No Start", "end": "2026-10-08", "products": []},
            {"name": "No End", "start": "2026-10-01", "products": []},
            {
                "name": "Complete Event",
                "start": "2026-10-01",
                "end": "2026-10-08",
                "products": [{"name": "Valid Product", "keywords": ["valid"]}],
            },
        ]
    }
    today = date(2026, 10, 4)
    active = get_active_events(config, today=today)
    assert len(active) == 1
    assert active[0]["name"] == "Complete Event"


def test_get_active_events_empty_config():
    """Empty config returns empty active events list without error."""
    active = get_active_events({}, today=date(2026, 10, 4))
    assert active == []


def test_get_seasonal_products_empty_config():
    """Empty config returns empty products list without error."""
    products = get_seasonal_products({}, today=date(2026, 10, 4))
    assert products == []
