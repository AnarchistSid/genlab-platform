"""Pin the top-creator watchlist config loader (A.1, 2026-07-07).

Contract:
  1. Missing config file → empty dict (fail-open)
  2. Malformed YAML → empty dict (fail-open, warn)
  3. Version mismatch → empty dict (fail-loud in log, safe in code)
  4. Unknown niche entries → dropped with warning
  5. Invalid channel_id (not 24 chars, not UC-prefixed) → dropped
  6. Valid entries → returned as TopCreator dataclass frozen instances
  7. Real repo config file loads without error (smoke)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from genlab_core.intel.top_creators_config import (
    TopCreator,
    load_top_creators,
    total_creator_count,
)


class TestTopCreatorValidation:
    """The 24-char UC-prefix rule is a YouTube API invariant. Reject
    at load time so the runner never issues a bogus API call."""

    def test_valid_channel_id_passes(self):
        c = TopCreator(
            channel_id="UCBJycsmduvYEL83R_U4JriQ",
            label="MKBHD",
            notes="",
        )
        assert c.is_valid()

    def test_wrong_length_rejected(self):
        c = TopCreator(channel_id="UCtooshort", label="x", notes="")
        assert not c.is_valid()

    def test_missing_uc_prefix_rejected(self):
        c = TopCreator(
            channel_id="AB1234567890123456789012",
            label="x",
            notes="",
        )
        assert not c.is_valid()

    def test_empty_channel_id_rejected(self):
        c = TopCreator(channel_id="", label="x", notes="")
        assert not c.is_valid()

    def test_frozen_dataclass_immutable(self):
        """Config is held in memory across daily polls — mutation
        would drift config from YAML."""
        c = TopCreator(
            channel_id="UCBJycsmduvYEL83R_U4JriQ",
            label="MKBHD",
            notes="",
        )
        with pytest.raises((AttributeError, TypeError)):
            c.channel_id = "UCother1234567890123456"  # type: ignore[misc]


class TestLoadTopCreators:
    def test_missing_file_returns_empty(self, tmp_path):
        result = load_top_creators(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_malformed_yaml_returns_empty(self, tmp_path):
        p = tmp_path / "top.yaml"
        p.write_text("this: is: not: valid: yaml: {[")
        result = load_top_creators(p)
        assert result == {}

    def test_version_mismatch_returns_empty(self, tmp_path):
        p = tmp_path / "top.yaml"
        p.write_text(
            "version: 999\n"
            "niches:\n"
            "  gaming:\n"
            "    channels:\n"
            "      - channel_id: UCBJycsmduvYEL83R_U4JriQ\n"
            "        label: x\n"
            "        notes: y\n"
        )
        result = load_top_creators(p)
        assert result == {}

    def test_unknown_niche_dropped(self, tmp_path):
        p = tmp_path / "top.yaml"
        p.write_text(
            "version: 1\n"
            "niches:\n"
            "  gaming:\n"
            "    channels:\n"
            "      - channel_id: UCBJycsmduvYEL83R_U4JriQ\n"
            "        label: MKBHD\n"
            "        notes: notes\n"
            "  hypothetical_new_niche:\n"
            "    channels:\n"
            "      - channel_id: UCTkXRDQl0luXxVQrRQvWS6w\n"
            "        label: x\n"
            "        notes: y\n"
        )
        result = load_top_creators(p)
        assert "gaming" in result
        assert "hypothetical_new_niche" not in result

    def test_invalid_channel_id_dropped(self, tmp_path):
        p = tmp_path / "top.yaml"
        p.write_text(
            "version: 1\n"
            "niches:\n"
            "  gaming:\n"
            "    channels:\n"
            "      - channel_id: not-a-uc-id\n"
            "        label: bogus\n"
            "        notes: x\n"
            "      - channel_id: UCBJycsmduvYEL83R_U4JriQ\n"
            "        label: MKBHD\n"
            "        notes: y\n"
        )
        result = load_top_creators(p)
        assert len(result["gaming"]) == 1
        assert result["gaming"][0].label == "MKBHD"

    def test_valid_entries_returned_as_dataclass(self, tmp_path):
        p = tmp_path / "top.yaml"
        p.write_text(
            "version: 1\n"
            "niches:\n"
            "  gaming:\n"
            "    channels:\n"
            "      - channel_id: UCBJycsmduvYEL83R_U4JriQ\n"
            "        label: MKBHD\n"
            "        notes: reactive on AI-tool releases\n"
        )
        result = load_top_creators(p)
        creator = result["gaming"][0]
        assert isinstance(creator, TopCreator)
        assert creator.channel_id == "UCBJycsmduvYEL83R_U4JriQ"
        assert creator.label == "MKBHD"
        assert "reactive" in creator.notes

    def test_empty_niche_yields_empty_list(self, tmp_path):
        """A niche entry with no channels → empty list (not absent).
        The runner uses this to detect 'watchlist not populated yet'
        vs 'niche not in config'."""
        p = tmp_path / "top.yaml"
        p.write_text("version: 1\nniches:\n  gaming:\n    channels: []\n")
        result = load_top_creators(p)
        assert result["gaming"] == []


class TestTotalCreatorCount:
    def test_empty_watchlist_zero(self):
        assert total_creator_count({}) == 0

    def test_counts_across_niches(self):
        w = {
            "gaming": [
                TopCreator("UCBJycsmduvYEL83R_U4JriQ", "a", ""),
                TopCreator("UCTkXRDQl0luXxVQrRQvWS6w", "b", ""),
            ],
            "sports": [TopCreator("UC1234567890123456789012", "c", "")],
            "movies": [],
        }
        assert total_creator_count(w) == 3


class TestRealConfigSmoke:
    """The shipped YAML at genlab-core/config/top_creators.yaml
    should load without error + return non-empty for at least one
    niche.  Guards against a future edit accidentally invalidating
    every entry."""

    def test_real_config_loads_and_has_at_least_one_creator(self):
        real_path = Path(__file__).resolve().parents[2] / "config" / "top_creators.yaml"
        if not real_path.is_file():
            pytest.skip(f"real config not present at {real_path}")
        result = load_top_creators(real_path)
        # At least one niche should have at least one valid creator.
        # Not asserting per-niche because operator may be mid-curation.
        total = sum(len(v) for v in result.values())
        assert total > 0, "shipped config has no valid creators"
