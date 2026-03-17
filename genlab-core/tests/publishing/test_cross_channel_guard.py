import pytest
from genlab_core.publishing.niche_credentials import (
    CrossChannelPublishError,
    validate_niche_match,
)


def test_matching_niche_passes():
    validate_niche_match(blueprint_niche="gaming", credential_niche="gaming")


def test_mismatched_niche_raises():
    with pytest.raises(CrossChannelPublishError, match="gaming.*ai_creators"):
        validate_niche_match(blueprint_niche="gaming", credential_niche="ai_creators")


def test_empty_credential_niche_raises():
    with pytest.raises(CrossChannelPublishError):
        validate_niche_match(blueprint_niche="gaming", credential_niche="")
