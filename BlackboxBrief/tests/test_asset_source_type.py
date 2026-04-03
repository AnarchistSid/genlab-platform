"""Tests for BacklogClient asset source_type normalization."""

from genlab_core.http.backlog_client import BacklogClient


def _client_without_init():
    # Bypass network-heavy __init__; we only need pure normalization logic.
    return BacklogClient.__new__(BacklogClient)


def test_normalize_asset_source_type_maps_text_url_video():
    c = _client_without_init()
    assert c._normalize_asset_source_type("text_url", "video") == "video_embed"


def test_normalize_asset_source_type_maps_unknown_image_to_hero():
    c = _client_without_init()
    assert c._normalize_asset_source_type("something_new", "image") == "hero_image"


def test_normalize_asset_source_type_preserves_allowed_value():
    c = _client_without_init()
    assert c._normalize_asset_source_type("schema_org", "image") == "schema_org"


def test_normalize_asset_source_type_stock():
    c = _client_without_init()
    assert c._normalize_asset_source_type("pexels", "image") == "stock_search"


def test_normalize_asset_source_type_generated():
    c = _client_without_init()
    assert c._normalize_asset_source_type("generator", "image") == "generated"


def test_normalize_asset_source_type_empty():
    c = _client_without_init()
    assert c._normalize_asset_source_type("", "image") == "hero_image"
    assert c._normalize_asset_source_type(None, "video") == "video_embed"
