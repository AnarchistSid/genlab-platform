"""Tests for the lazy platform client registry."""

from __future__ import annotations

import pytest


def test_list_platforms_returns_known_ids():
    from genlab_core.platforms.registry import list_platforms

    platforms = list_platforms()
    assert "instagram" in platforms
    assert "youtube" in platforms
    assert "x_twitter" in platforms
    assert "facebook" in platforms
    assert "threads" in platforms
    assert "tiktok" in platforms


def test_get_client_unknown_platform_raises():
    from genlab_core.platforms.registry import get_client

    with pytest.raises(ValueError, match="Unknown platform"):
        get_client("myspace")


def test_get_client_deferred_import_error():
    """If a platform module doesn't exist yet, get_client raises ImportError."""
    from genlab_core.platforms import registry

    # Temporarily register a platform that points to a non-existent module
    registry._REGISTRY["_test_missing"] = "genlab_core.platforms._does_not_exist:FakeClient"
    registry._CLASS_CACHE.pop("_test_missing", None)

    try:
        with pytest.raises((ImportError, ModuleNotFoundError)):
            registry.get_client("_test_missing")
    finally:
        # Clean up injected test entry
        registry._REGISTRY.pop("_test_missing", None)
        registry._CLASS_CACHE.pop("_test_missing", None)
