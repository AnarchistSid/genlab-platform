"""Regression test for the non-gameplay Twitch clip title filter.

A streamer playing a real game (e.g. Overwatch) can have a clip
captured during a non-gameplay moment (IRL talking, reacts, ASMR,
rants, etc.). The clip is tagged with the game's category but the
captured moment isn't actual gameplay — the LLM downstream then
hallucinates game-themed content around it.

Caught in prod 2026-06-20: a streamer ``zullysk`` clip titled with
a tic-related question shipped as a gaming "Overwatch" blueprint
with fully-fabricated patch-notes caption text.

The filter (added 2026-06-20) drops clips whose title contains a
non-gameplay marker BEFORE the story enters the pipeline.
"""

from __future__ import annotations

from genlab_core.pipeline.stages.fetch_twitch_clips import (
    _NON_GAMEPLAY_TITLE_MARKERS,
    _is_non_gameplay_clip,
)


class TestIsNonGameplayClip:
    """Pin which titles the filter drops vs keeps."""

    def test_empty_title_kept(self):
        assert _is_non_gameplay_clip("") is False
        assert _is_non_gameplay_clip(None) is False  # type: ignore[arg-type]

    def test_genuine_gameplay_titles_kept(self):
        """The classic real-gameplay clip patterns must pass through."""
        for title in (
            "1v5 clutch with the last bullet",
            "Insane 360 no-scope montage",
            "Pulling off the impossible play in OW2",
            "Faker's 1v1 outplay vs Chovy",
            "Speedrun WR attempt — 14:23 PB",
            "Tactical TF2 sentry placement saves the round",  # 'tactical' must NOT match ' tic '
        ):
            assert not _is_non_gameplay_clip(title), f"genuine gameplay flagged: {title!r}"

    def test_caught_zullysk_real_world_title(self):
        """The actual production title that triggered this fix."""
        title = "Does it irritate you if people find your tics humorous?"
        assert _is_non_gameplay_clip(title), "the original prod-bug title must be flagged"

    def test_irl_chat_react_rant_caught(self):
        """The main 4 non-gameplay categories all get flagged."""
        for title in (
            "Streamer goes IRL with viewer questions",
            "Reacts to fan art live on stream",
            "Massive rant about subscribers",
            "Just chatting with the homies for an hour",
            "Storytime — how I got into streaming",
            "ASMR session for tired viewers",
            "Q&A with chat — answering everything",
        ):
            assert _is_non_gameplay_clip(title), f"non-gameplay missed: {title!r}"

    def test_substring_false_positive_guard(self):
        """The space-padded markers must not match fragment-inside-word
        cases that would have flagged real gameplay clips.
        """
        for title in (
            # 'tactical' contains 'tic' but space-padded ' tic ' won't match
            "Tactical defense at point B holds the round",
            # 'irlbgames' (fake streamer name) — no leading space, won't match
            "irlbgames pulls off the wallbang",
            # 'rampage' starts with 'ramp' but no ' ramble' fragment
            "Rampage achievement unlocked",
            # 'react' inside 'reactor' — but ' react ' requires leading space
            "Reactor sabotage outplay",
        ):
            assert not _is_non_gameplay_clip(title), (
                f"false-positive on legitimate gameplay title: {title!r}"
            )

    def test_marker_list_is_lowercase(self):
        """All markers must be lowercase since the matcher lowercases the title.
        A missing lowercase marker would silently never match.
        """
        for marker in _NON_GAMEPLAY_TITLE_MARKERS:
            assert marker == marker.lower(), f"marker {marker!r} has uppercase chars"
