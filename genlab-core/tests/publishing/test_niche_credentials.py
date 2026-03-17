"""Tests for niche credential resolution — prefix map + cross-channel guard."""

import os
from unittest.mock import patch

import pytest
from genlab_core.publishing.niche_credentials import (
    NICHE_CREDENTIAL_PREFIXES,
    CrossChannelPublishError,
    resolve_meta_credentials,
    resolve_niche_env,
    validate_niche_match,
)

# --- Existing tests (cross-channel guard) ---


def test_matching_niche_passes():
    validate_niche_match(blueprint_niche="gaming", credential_niche="gaming")


def test_mismatched_niche_raises():
    with pytest.raises(CrossChannelPublishError, match="gaming.*ai_creators"):
        validate_niche_match(blueprint_niche="gaming", credential_niche="ai_creators")


def test_empty_credential_niche_raises():
    with pytest.raises(CrossChannelPublishError):
        validate_niche_match(blueprint_niche="gaming", credential_niche="")


# --- WS4: ai_creators + ai_tech prefix map tests ---


def test_all_niches_have_prefix():
    """Every supported niche must have a credential prefix."""
    required = {"sports", "movies", "anime", "gaming", "ai_creators"}
    assert required.issubset(set(NICHE_CREDENTIAL_PREFIXES.keys()))


def test_ai_creators_uses_blackboxbrief():
    assert NICHE_CREDENTIAL_PREFIXES["ai_creators"] == "BLACKBOXBRIEF"


def test_ai_tech_uses_blackboxbrief():
    """ai_tech is an alias for ai_creators — must map to same prefix."""
    assert NICHE_CREDENTIAL_PREFIXES["ai_tech"] == "BLACKBOXBRIEF"


def test_resolve_meta_for_ai_creators():
    with patch.dict(os.environ, {
        "BLACKBOXBRIEF_META_ACCESS_TOKEN": "bb_ig_token",
        "BLACKBOXBRIEF_IG_USER_ID": "bb_ig_user",
        "BLACKBOXBRIEF_FB_PAGE_ACCESS_TOKEN": "bb_fb_token",
        "BLACKBOXBRIEF_FB_PAGE_ID": "bb_fb_page",
    }, clear=False):
        creds = resolve_meta_credentials("ai_creators")
        assert creds["ig_access_token"] == "bb_ig_token"
        assert creds["ig_user_id"] == "bb_ig_user"
        assert creds["fb_access_token"] == "bb_fb_token"
        assert creds["fb_page_id"] == "bb_fb_page"


def test_resolve_meta_for_ai_tech():
    """ai_tech alias should resolve using BLACKBOXBRIEF_ prefix."""
    with patch.dict(os.environ, {
        "BLACKBOXBRIEF_META_ACCESS_TOKEN": "bb_token_via_alias",
        "BLACKBOXBRIEF_IG_USER_ID": "bb_user_via_alias",
        "BLACKBOXBRIEF_FB_PAGE_ACCESS_TOKEN": "bb_fb_via_alias",
        "BLACKBOXBRIEF_FB_PAGE_ID": "bb_page_via_alias",
    }, clear=False):
        creds = resolve_meta_credentials("ai_tech")
        assert creds["ig_access_token"] == "bb_token_via_alias"
        assert creds["fb_page_id"] == "bb_page_via_alias"


def test_resolve_meta_for_gaming():
    with patch.dict(os.environ, {
        "CRITICALRUSH_META_ACCESS_TOKEN": "test_token",
        "CRITICALRUSH_IG_USER_ID": "12345",
        "CRITICALRUSH_FB_PAGE_ACCESS_TOKEN": "fb_token",
        "CRITICALRUSH_FB_PAGE_ID": "67890",
    }, clear=False):
        creds = resolve_meta_credentials("gaming")
        assert creds["ig_access_token"] == "test_token"
        assert creds["fb_page_id"] == "67890"


def test_ai_creators_does_not_fall_through_to_global():
    """With prefix registered, missing niche vars must return '' — not global BB vars."""
    env = {
        "META_ACCESS_TOKEN": "global_token_should_not_use",
    }
    # Clear any existing BLACKBOXBRIEF_ vars to simulate missing
    cleared = {
        "BLACKBOXBRIEF_META_ACCESS_TOKEN": "",
    }
    with patch.dict(os.environ, {**env, **cleared}, clear=False):
        val = resolve_niche_env("ai_creators", "META_ACCESS_TOKEN", "META_ACCESS_TOKEN")
        # Should return "" because BLACKBOXBRIEF_META_ACCESS_TOKEN is empty,
        # NOT fall through to the global META_ACCESS_TOKEN
        assert val == ""
