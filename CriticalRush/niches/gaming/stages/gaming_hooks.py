"""Gaming hook strategy — shared BaseHookStrategy subclass.

Migrated from inline hook generation in WriteGamingContent (Sprint 69).
Uses LLM-first hook generation with gaming-specific category classification
and placeholder substitution from config/templates.yaml.
"""
from __future__ import annotations

import re
from pathlib import Path

from genlab_core.strategies import BaseHookStrategy

_NICHE_ROOT = Path(__file__).resolve().parent.parent

# Keywords for classifying gaming stories
_UPDATE_KEYWORDS = {"update", "patch", "drop", "release", "launch", "added", "nerf", "buff", "rework"}
_CLUTCH_KEYWORDS = {"clutch", "play", "insane", "unreal", "highlight", "clip", "moment", "crazy"}
_STREAMER_KEYWORDS = {"streamer", "twitch", "just chatting", "trending", "live", "stream"}
_META_KEYWORDS = {"meta", "ranked", "competitive", "tier", "balance", "broken", "op", "season"}


class GamingHookStrategy(BaseHookStrategy):
    """Generate hooks for gaming content.

    Categories: clutch_play, update_drop, streamer_moment, meta_shift, default.
    """

    _title_fallback_label = "Gaming moment"

    def __init__(self) -> None:
        super().__init__(niche_id="gaming", niche_root=_NICHE_ROOT)

    def _classify_story(self, story: dict) -> str:
        """Classify gaming story into a hook category."""
        title = (story.get("title") or "").lower()
        summary = (story.get("summary") or "").lower()
        text = f"{title} {summary}"

        if any(kw in text for kw in _UPDATE_KEYWORDS):
            return "update_drop"
        if any(kw in text for kw in _CLUTCH_KEYWORDS):
            return "clutch_play"
        if any(kw in text for kw in _STREAMER_KEYWORDS):
            return "streamer_moment"
        if any(kw in text for kw in _META_KEYWORDS):
            return "meta_shift"
        return "default"

    def _substitute_placeholders(self, formula: str, story: dict) -> str:
        """Replace {placeholders} in hook formulas with story data."""
        title = story.get("title", "")

        game = _extract_game_name(title)
        player = _extract_player(title)
        streamer = _extract_streamer(title)

        replacements = {
            "game": game or _shorten_title(title),
            "player": player or "this player",
            "streamer": streamer or "this streamer",
            "detail": _extract_detail(title),
        }

        result = formula
        for key, value in replacements.items():
            result = result.replace(f"{{{key}}}", value)

        # If any placeholders remain unfilled, skip this formula
        if re.search(r"\{[a-z_]+\}", result):
            return ""
        return result


# ── Extraction helpers ──────────────────────────────────────────

_KNOWN_GAMES = {
    "valorant", "cs2", "counter-strike", "fortnite", "overwatch",
    "league of legends", "apex legends", "elden ring", "minecraft",
    "gta", "call of duty", "cod", "destiny", "diablo", "dota",
    "rocket league", "rainbow six", "r6", "pubg", "warzone",
    "crimson desert", "marvel rivals", "deadlock", "the finals",
    "palworld", "helldivers", "baldur's gate", "cyberpunk",
    "starfield", "zelda", "pokemon", "smash", "street fighter",
}

_KNOWN_STREAMERS = {
    "shroud", "ninja", "pokimane", "xqc", "summit1g", "timthetatman",
    "dr disrespect", "tfue", "s1mple", "tarik", "adin", "kai cenat",
    "speed", "hasanabi", "ludwig", "valkyrae", "sykkuno",
}


def _extract_game_name(title: str) -> str:
    title_lower = title.lower()
    for game in _KNOWN_GAMES:
        if game in title_lower:
            idx = title_lower.find(game)
            return title[idx : idx + len(game)]
    return ""


def _extract_player(title: str) -> str:
    """Extract a player/character name — first capitalized word not a game name."""
    words = title.split()
    for word in words:
        clean = word.strip("'\".,!?()[]")
        if clean and clean[0].isupper() and clean.lower() not in _KNOWN_GAMES and len(clean) > 2:
            return clean
    return ""


def _extract_streamer(title: str) -> str:
    title_lower = title.lower()
    for streamer in _KNOWN_STREAMERS:
        if streamer in title_lower:
            idx = title_lower.find(streamer)
            return title[idx : idx + len(streamer)]
    return ""


def _extract_detail(title: str) -> str:
    """Extract a short detail from the title."""
    # Take the last phrase after a dash or colon
    for sep in (" — ", " - ", ": "):
        if sep in title:
            return title.split(sep)[-1].strip()[:40]
    return " ".join(title.split()[-3:]) if len(title.split()) > 3 else title


def _shorten_title(title: str) -> str:
    """Shorten title to 4 words max."""
    return " ".join(title.split()[:4])
