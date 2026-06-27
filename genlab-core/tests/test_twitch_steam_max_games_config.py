"""Pin: 2026-06-28 — fetch_twitch_clips + fetch_steam_trailers respect
``max_games`` config (default 5, gaming overrides to 8).

Background: today's audit (captured in
[[gaming-zero-blueprints-funnel-analysis-2026-06-28]]) flagged the
hardcoded ``[:5]`` cap in both stages as a hidden bottleneck that
compounded with the filter-stage cap (PR #621) to starve gaming's
daily blueprints. PR raised it via sources.yaml config block
(``twitch_clips.max_games`` + ``steam_trailers.max_games``); default
stays 5 in code for backward compat.

These pins guard against regression to a hardcoded value AND verify
the config plumbing reads from the correct yaml block. Without them,
a future cleanup could re-hardcode the literal.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TWITCH_STAGE = (
    REPO_ROOT
    / "genlab-core"
    / "src"
    / "genlab_core"
    / "pipeline"
    / "stages"
    / "fetch_twitch_clips.py"
)
STEAM_STAGE = (
    REPO_ROOT
    / "genlab-core"
    / "src"
    / "genlab_core"
    / "pipeline"
    / "stages"
    / "fetch_steam_trailers.py"
)
GAMING_SOURCES = REPO_ROOT / "CriticalRush" / "niches" / "gaming" / "config" / "sources.yaml"


class TestTwitchMaxGamesConfig:
    def test_twitch_max_games_is_config_driven(self):
        """fetch_twitch_clips.py must read max_games from twitch_cfg, not
        hardcode the literal."""
        content = TWITCH_STAGE.read_text()
        # The config lookup line must exist.
        assert 'twitch_cfg.get("max_games"' in content, (
            "fetch_twitch_clips.py must read max_games from twitch_cfg. "
            "Hardcoding the cap silently re-creates the 2026-06-28 starvation "
            "bug. Use `twitch_cfg.get('max_games', 5)` so default stays 5 "
            "for backward compat but gaming can override to 8."
        )

    def test_twitch_default_is_5(self):
        """Default must stay 5 for backward compat — other niches consuming
        this stage (if any are added later) get the pre-PR behavior."""
        content = TWITCH_STAGE.read_text()
        # The exact `get("max_games", 5)` call pins the default value.
        assert 'twitch_cfg.get("max_games", 5)' in content, (
            "Default must be 5. Raising the default would silently affect "
            "any future non-gaming consumer of this stage."
        )

    def test_twitch_no_hardcoded_5_in_loop(self):
        """The for-loop slice must NOT be `[:5]` hardcoded — it must use
        `[:max_games]` so the config flows through to the actual loop."""
        content = TWITCH_STAGE.read_text()
        # Search for the loop pattern. The fix replaces `for game_id in game_ids[:5]`
        # with `for game_id in game_ids[:max_games]`.
        assert "game_ids[:max_games]" in content, (
            "Loop slice must use `[:max_games]` not the hardcoded literal. "
            "Otherwise the config override has no effect on the actual fetch."
        )


class TestSteamMaxGamesConfig:
    def test_steam_max_games_is_config_driven(self):
        """fetch_steam_trailers.py must read max_games from steam_cfg."""
        content = STEAM_STAGE.read_text()
        assert 'steam_cfg.get("max_games"' in content, (
            "fetch_steam_trailers.py must read max_games from steam_cfg. "
            "Same rationale as Twitch — hardcoding re-creates the cap-as-"
            "hidden-bottleneck failure mode."
        )

    def test_steam_default_is_5(self):
        """Default must stay 5 for backward compat."""
        content = STEAM_STAGE.read_text()
        assert 'steam_cfg.get("max_games", 5)' in content, "Default must be 5 for backward compat."

    def test_steam_no_hardcoded_5_in_loop(self):
        """Loop slice must use `[:max_games]` not `[:5]`."""
        content = STEAM_STAGE.read_text()
        assert "app_ids[:max_games]" in content, (
            "Loop slice must use `[:max_games]`. The earlier code had a "
            "second `[:5]` slice in the loop that ignored anything in context."
        )


class TestGamingOverride:
    def test_gaming_sources_yaml_sets_twitch_max_games(self):
        """Gaming's sources.yaml must set twitch_clips.max_games: 8."""
        content = GAMING_SOURCES.read_text()
        # Find the twitch_clips block and verify max_games appears with value 8.
        assert "twitch_clips:" in content, "twitch_clips block must exist"
        # The max_games key must be set to 8 in this niche.
        twitch_block_start = content.find("twitch_clips:")
        # Search forward for max_games until the next top-level block.
        next_block_start = content.find("\nsteam_trailers:", twitch_block_start)
        twitch_section = content[twitch_block_start:next_block_start]
        assert "max_games: 8" in twitch_section, (
            "Gaming's twitch_clips block must set max_games: 8 (was hardcoded "
            "5 before today's audit). Without this override, the niche-level "
            "config doesn't flow to the fetcher."
        )

    def test_gaming_sources_yaml_sets_steam_max_games(self):
        """Gaming's sources.yaml must set steam_trailers.max_games: 8."""
        content = GAMING_SOURCES.read_text()
        steam_block_start = content.find("steam_trailers:")
        assert steam_block_start > 0, "steam_trailers block must exist"
        # Search forward until next top-level block (or EOF).
        next_block_match = content.find("\n# ", steam_block_start)
        if next_block_match < 0:
            next_block_match = len(content)
        steam_section = content[steam_block_start:next_block_match]
        assert "max_games: 8" in steam_section, (
            "Gaming's steam_trailers block must set max_games: 8."
        )
