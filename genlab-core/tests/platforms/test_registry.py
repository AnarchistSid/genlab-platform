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

    # Clear cache to ensure a fresh import attempt
    registry._CLASS_CACHE.clear()

    # instagram.py doesn't exist yet — this should raise
    with pytest.raises((ImportError, ModuleNotFoundError)):
        registry.get_client("instagram")
