"""Tests for the updated score_authority() function.

Verifies that source_priority (from sources.yaml) takes precedence
over domain-based lookup, with proper fallback behavior.
"""

from execution.dedupe_rank_items import score_authority

AUTHORITY_MAP = {
    "reddit.com": 0.85,
    "i.redd.it": 0.85,
    "youtube.com": 0.80,
    "civitai.com": 0.80,
    "huggingface.co": 0.65,
    "x.com": 0.70,
    "default": 0.50,
}


class TestScoreAuthoritySourcePriority:
    """source_priority is the primary scoring signal."""

    def test_source_priority_used_when_present(self):
        item = {"domain": "reddit.com", "source_priority": 0.98}
        assert score_authority(item, AUTHORITY_MAP) == 0.98

    def test_source_priority_overrides_domain_map(self):
        item = {"domain": "youtube.com", "source_priority": 0.92}
        assert score_authority(item, AUTHORITY_MAP) == 0.92

    def test_source_priority_zero_is_used(self):
        """0.0 is valid — must not be treated as falsy."""
        item = {"domain": "reddit.com", "source_priority": 0.0}
        assert score_authority(item, AUTHORITY_MAP) == 0.0

    def test_source_priority_as_string(self):
        item = {"domain": "reddit.com", "source_priority": "0.93"}
        assert score_authority(item, AUTHORITY_MAP) == 0.93


class TestScoreAuthorityDomainFallback:
    """Fallback to domain-based lookup when source_priority is absent."""

    def test_fallback_to_domain_exact_match(self):
        item = {"domain": "reddit.com"}
        assert score_authority(item, AUTHORITY_MAP) == 0.85

    def test_fallback_reddit_media_host(self):
        item = {"domain": "i.redd.it"}
        assert score_authority(item, AUTHORITY_MAP) == 0.85

    def test_subdomain_stripping(self):
        item = {"domain": "api.civitai.com"}
        assert score_authority(item, AUTHORITY_MAP) == 0.80

    def test_unknown_domain_gets_default(self):
        item = {"domain": "unknown-site.com"}
        assert score_authority(item, AUTHORITY_MAP) == 0.50

    def test_empty_domain_gets_default(self):
        item = {"domain": ""}
        assert score_authority(item, AUTHORITY_MAP) == 0.50

    def test_missing_domain_gets_default(self):
        item = {}
        assert score_authority(item, AUTHORITY_MAP) == 0.50
