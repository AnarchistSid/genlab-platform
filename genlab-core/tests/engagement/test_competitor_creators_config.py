"""Pin tests for competitor_creators_config — Layer 4 audit round 4 policy.

Operator-curated per-niche channel blocklist for outbound engagement.
Empty default = filter is no-op. Config version check + niche validation
+ channel_id well-formed check all fail-safe (return empty, log WARNING).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from genlab_core.engagement.competitor_creators_config import (
    CompetitorCreator,
    is_competitor,
    load_competitor_creators,
)


class TestCompetitorCreatorValidation:
    def test_valid_uc_prefix_24_chars(self) -> None:
        c = CompetitorCreator(
            channel_id="UC" + "x" * 22,
            label="Test",
            notes="",
        )
        assert c.is_valid() is True

    def test_invalid_no_uc_prefix(self) -> None:
        c = CompetitorCreator(channel_id="xxx" + "y" * 21, label="", notes="")
        assert c.is_valid() is False

    def test_invalid_wrong_length(self) -> None:
        c = CompetitorCreator(channel_id="UC" + "x" * 10, label="", notes="")
        assert c.is_valid() is False

    def test_invalid_empty(self) -> None:
        c = CompetitorCreator(channel_id="", label="", notes="")
        assert c.is_valid() is False


class TestLoadCompetitorCreators:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """Fail-open: missing config → empty dict → filter is no-op.
        This is the DEFAULT state (config exists in repo but starts
        with empty niche lists)."""
        result = load_competitor_creators(tmp_path / "missing.yaml")
        assert result == {}

    def test_empty_niches_load_cleanly(self, tmp_path: Path) -> None:
        """The shipped-empty config state — every niche present with
        empty channels list."""
        cfg = tmp_path / "competitor_creators.yaml"
        cfg.write_text(
            textwrap.dedent(
                """
                version: 1
                niches:
                  gaming:
                    channels: []
                  sports:
                    channels: []
                """
            ).lstrip()
        )
        result = load_competitor_creators(cfg)
        assert result == {"gaming": set(), "sports": set()}

    def test_valid_channels_loaded(self, tmp_path: Path) -> None:
        cfg = tmp_path / "competitor_creators.yaml"
        cfg.write_text(
            textwrap.dedent(
                """
                version: 1
                niches:
                  gaming:
                    channels:
                      - channel_id: UCAAAAAAAAAAAAAAAAAAAAAA
                        label: CompetitorGaming
                        notes: Directly overlapping audience
                      - channel_id: UCBBBBBBBBBBBBBBBBBBBBBB
                        label: Another
                        notes: Similar format
                """
            ).lstrip()
        )
        result = load_competitor_creators(cfg)
        assert result == {
            "gaming": {
                "UCAAAAAAAAAAAAAAAAAAAAAA",
                "UCBBBBBBBBBBBBBBBBBBBBBB",
            }
        }

    def test_invalid_channel_id_dropped(self, tmp_path: Path) -> None:
        """Typo / bad format channel ID should be silently dropped rather
        than added to blocklist (where it would never match). Blocklist
        with junk entries is worse than no blocklist — silent-fail-open."""
        cfg = tmp_path / "competitor_creators.yaml"
        cfg.write_text(
            textwrap.dedent(
                """
                version: 1
                niches:
                  gaming:
                    channels:
                      - channel_id: BAD_ID_TOO_SHORT
                        label: Should be dropped
                        notes: ""
                      - channel_id: UCVVVVVVVVVVVVVVVVVVVVVV
                        label: Valid
                        notes: ""
                """
            ).lstrip()
        )
        result = load_competitor_creators(cfg)
        assert result == {"gaming": {"UCVVVVVVVVVVVVVVVVVVVVVV"}}

    def test_unknown_niche_dropped(self, tmp_path: Path) -> None:
        cfg = tmp_path / "competitor_creators.yaml"
        cfg.write_text(
            textwrap.dedent(
                """
                version: 1
                niches:
                  not_a_real_niche:
                    channels:
                      - channel_id: UCVVVVVVVVVVVVVVVVVVVVVV
                        label: Junk
                        notes: ""
                  gaming:
                    channels: []
                """
            ).lstrip()
        )
        result = load_competitor_creators(cfg)
        assert "not_a_real_niche" not in result
        assert "gaming" in result

    def test_version_mismatch_returns_empty(self, tmp_path: Path) -> None:
        cfg = tmp_path / "competitor_creators.yaml"
        cfg.write_text(
            textwrap.dedent(
                """
                version: 99
                niches:
                  gaming:
                    channels:
                      - channel_id: UCVVVVVVVVVVVVVVVVVVVVVV
                        label: Should not load
                        notes: ""
                """
            ).lstrip()
        )
        assert load_competitor_creators(cfg) == {}

    def test_yaml_parse_error_returns_empty(self, tmp_path: Path) -> None:
        cfg = tmp_path / "competitor_creators.yaml"
        cfg.write_text("this is: not: valid: yaml: [")
        assert load_competitor_creators(cfg) == {}


class TestIsCompetitor:
    def test_no_blocklist_returns_false(self) -> None:
        assert is_competitor("gaming", "UC_anything") is False
        assert is_competitor("gaming", "UC_anything", None) is False
        assert is_competitor("gaming", "UC_anything", {}) is False

    def test_blocked_channel_true(self) -> None:
        blocklist = {"gaming": {"UC_blocked"}}
        assert is_competitor("gaming", "UC_blocked", blocklist) is True

    def test_unblocked_channel_false(self) -> None:
        blocklist = {"gaming": {"UC_blocked"}}
        assert is_competitor("gaming", "UC_other", blocklist) is False

    def test_different_niche_isolated(self) -> None:
        """Blocklists are niche-scoped — a channel blocked in gaming
        can still be replied to in sports if it (weirdly) appears there."""
        blocklist = {"gaming": {"UC_id"}, "sports": set()}
        assert is_competitor("gaming", "UC_id", blocklist) is True
        assert is_competitor("sports", "UC_id", blocklist) is False
