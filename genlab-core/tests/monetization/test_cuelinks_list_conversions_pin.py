"""Pin the 2026-07-17 Layer 2 batch 2 Cuelinks conversion polling.

## What broke pre-fix

Zero conversions ever tracked from any network from 2026-03 → 2026-07-17:
- `affiliate_revenue.conversions = 0` on every historical row
- `affiliate_revenue.revenue_amount = clicks × 0.02` (estimation from
  `affiliate_economics.py:40` `conversion_rate = 0.02`)
- 108-arm product bandit had 0 observations
- Cuelinks V3 exposes `/conversions/list` endpoint — we ignored it
  entirely (zero grep hits for that path before this commit)

Effect: revenue optimization was blind. The strategic $1M/mo target
was structurally unmeasurable — the agent couldn't optimize for
revenue because revenue signal never flowed back.

## Fix contract (this test locks it)

1. `cuelinks_client.list_conversions(from_date, to_date)` exists +
   returns normalized dicts
2. Empty API key → fail-open (returns []) so importer graceful no-ops
   until operator provisions the key
3. Response parsing handles multiple V3 shapes: bare list,
   `{"data": [...]}`, `{"conversions": [...]}`
4. Normalized fields include the canonical set expected by the
   importer: conversion_id, subid, sale_amount, commission_amount,
   currency, status, conversion_time
5. Fail-open on unknown shape (returns [])
"""

from __future__ import annotations

from unittest.mock import patch


def test_list_conversions_returns_empty_when_key_unset() -> None:
    """Missing API key must fail-open — importer graceful no-ops."""
    from genlab_core.monetization import cuelinks_client

    with patch.dict("os.environ", {"CUELINKS_V3_API_KEY": ""}, clear=False):
        result = cuelinks_client.list_conversions("2026-07-15", "2026-07-17")
    assert result == [], (
        "Missing API key must return [] fail-open. Do not raise — "
        "the importer must survive un-provisioned prod state."
    )


def test_list_conversions_parses_data_wrapper() -> None:
    """V3 may return {'data': [...]}. Must parse."""
    from genlab_core.monetization import cuelinks_client

    fake_response = {
        "data": [
            {
                "conversion_id": "conv_abc",
                "order_id": "ord_123",
                "subid": "gaming:83016a45",
                "campaign_name": "Flipkart",
                "sale_amount": 1200.0,
                "commission_amount": 60.0,
                "currency": "INR",
                "status": "confirmed",
                "conversion_time": "2026-07-15T10:30:00Z",
            }
        ]
    }
    with patch.dict("os.environ", {"CUELINKS_V3_API_KEY": "test-key"}, clear=False), \
         patch("genlab_core.monetization.cuelinks_client._get", return_value=fake_response):
        result = cuelinks_client.list_conversions("2026-07-15", "2026-07-17")

    assert len(result) == 1
    assert result[0]["conversion_id"] == "conv_abc"
    assert result[0]["subid"] == "gaming:83016a45"
    assert result[0]["commission_amount"] == 60.0
    assert result[0]["currency"] == "INR"


def test_list_conversions_parses_bare_list() -> None:
    from genlab_core.monetization import cuelinks_client

    fake_response = [
        {"conversion_id": "c1", "commission_amount": 10, "currency": "INR"},
        {"conversion_id": "c2", "commission_amount": 20, "currency": "INR"},
    ]
    with patch.dict("os.environ", {"CUELINKS_V3_API_KEY": "test-key"}, clear=False), \
         patch("genlab_core.monetization.cuelinks_client._get", return_value=fake_response):
        result = cuelinks_client.list_conversions("2026-07-15", "2026-07-17")

    assert len(result) == 2
    assert result[0]["conversion_id"] == "c1"


def test_list_conversions_parses_conversions_wrapper() -> None:
    """V3 may return {'conversions': [...]}. Must parse."""
    from genlab_core.monetization import cuelinks_client

    fake_response = {"conversions": [{"conversion_id": "c1"}]}
    with patch.dict("os.environ", {"CUELINKS_V3_API_KEY": "test-key"}, clear=False), \
         patch("genlab_core.monetization.cuelinks_client._get", return_value=fake_response):
        result = cuelinks_client.list_conversions("2026-07-15", "2026-07-17")

    assert len(result) == 1


def test_list_conversions_normalizes_alternative_field_names() -> None:
    """Some V3 responses use `id` / `commission` / `sub_id` /
    `created_at` — must normalize to the canonical keys."""
    from genlab_core.monetization import cuelinks_client

    fake_response = [
        {
            "id": "alt_c1",
            "sub_id": "movies:abc123",
            "merchant": "Myntra",
            "order_amount": 500,
            "commission": 25,
            "created_at": "2026-07-16T08:00:00Z",
        }
    ]
    with patch.dict("os.environ", {"CUELINKS_V3_API_KEY": "test-key"}, clear=False), \
         patch("genlab_core.monetization.cuelinks_client._get", return_value=fake_response):
        result = cuelinks_client.list_conversions("2026-07-15", "2026-07-17")

    assert result[0]["conversion_id"] == "alt_c1"
    assert result[0]["subid"] == "movies:abc123"
    assert result[0]["campaign_name"] == "Myntra"
    assert result[0]["sale_amount"] == 500.0
    assert result[0]["commission_amount"] == 25.0
    assert result[0]["conversion_time"] == "2026-07-16T08:00:00Z"


def test_list_conversions_returns_empty_on_unknown_shape() -> None:
    """Unknown API shape must fail-open (return []) with a warning
    log — never raise, never insert garbage."""
    from genlab_core.monetization import cuelinks_client

    fake_response = {"unexpected_key": "value"}
    with patch.dict("os.environ", {"CUELINKS_V3_API_KEY": "test-key"}, clear=False), \
         patch("genlab_core.monetization.cuelinks_client._get", return_value=fake_response):
        result = cuelinks_client.list_conversions("2026-07-15", "2026-07-17")

    assert result == []


def test_list_conversions_returns_empty_on_api_failure() -> None:
    """Network failure / HTTP error → fail-open."""
    from genlab_core.monetization import cuelinks_client

    with patch.dict("os.environ", {"CUELINKS_V3_API_KEY": "test-key"}, clear=False), \
         patch("genlab_core.monetization.cuelinks_client._get", return_value=None):
        result = cuelinks_client.list_conversions("2026-07-15", "2026-07-17")

    assert result == []
