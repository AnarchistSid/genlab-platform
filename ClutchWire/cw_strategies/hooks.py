"""ClutchWire hook generation strategy.

Inherits shared hook generation from BaseHookStrategy. Provides sports-specific:
- ``_classify_story()`` — injury/trade/clutch/rivalry/upset/record categorization
- ``_substitute_placeholders()`` — {team}, {player}, {sport}, {play_type} resolution
- ``_extract_teams_from_title()`` — RSS headline team-name extraction
"""

from __future__ import annotations

import re
from pathlib import Path

from genlab_core.strategies import BaseHookStrategy

NICHE_ROOT = Path(__file__).resolve().parent.parent


class SportHookStrategy(BaseHookStrategy):
    """Generate attention-grabbing hooks for sports content."""

    _title_fallback_label = "Sports moment"

    def __init__(self) -> None:
        super().__init__(niche_id="sports", niche_root=NICHE_ROOT)

    def _classify_story(self, story: dict) -> str:
        """Determine story category for hook selection."""
        title = (story.get("title", "") + " " + story.get("summary", "")).lower()

        if story.get("is_upset"):
            return "upset"
        if story.get("is_record"):
            return "record"
        if any(w in title for w in ("injur", "out for", "torn", "broken", "sidelined")):
            return "injury"
        if any(w in title for w in ("trade", "sign", "deal", "acquire", "swap")):
            return "trade"
        if any(w in title for w in ("clutch", "buzzer", "game-winner", "last second", "overtime")):
            return "clutch"
        if any(w in title for w in ("rivalry", "derby", "classic", "rematch")):
            return "rivalry"
        return "default"

    @staticmethod
    def _extract_teams_from_title(title: str) -> list[str]:
        """Try to extract team names from RSS headline patterns like 'X vs Y'."""
        verbs = (
            r"(?:vs\.?|beat|beats|top|tops|stun|stuns|defeat|defeats|rout|routs|over|downs|edges)"
        )
        match = re.search(
            rf"(\S+(?:\s+\S+){{0,2}})\s+{verbs}\s+(\S+(?:\s+\S+){{0,2}})(?:\s|[,;:\-]|$)",
            title,
            re.IGNORECASE,
        )
        if match:
            a = match.group(1).strip().rsplit(": ", 1)[-1]
            b = match.group(2).strip().rstrip(".,;:")
            if 2 <= len(a) <= 25 and 2 <= len(b) <= 25:
                return [a, b]
        return []

    def _substitute_placeholders(self, formula: str, story: dict) -> str:
        """Replace {team}, {player}, {sport}, {play_type} with story data."""
        teams = story.get("teams", [])
        players = story.get("players", [])
        sport = story.get("sport", "sports")
        league = story.get("league", "")

        # If no teams from structured data, try extracting from title
        if not teams:
            teams = self._extract_teams_from_title(story.get("title", ""))

        # Skip formulas that need teams/players we don't have
        has_team_placeholder = "{team" in formula
        has_player_placeholder = "{player}" in formula
        if has_team_placeholder and not teams:
            return ""
        if has_player_placeholder and not players:
            return ""

        subs = {
            "team": teams[0] if teams else "them",
            "team_a": teams[0] if len(teams) > 0 else "them",
            "team_b": teams[1] if len(teams) > 1 else "the opposition",
            "player": players[0] if players else "this player",
            "sport": sport or league or "sports",
            "play_type": "play",
        }

        result = formula
        for key, value in subs.items():
            result = result.replace(f"{{{key}}}", value)
        return result
