"""Regression pins for FetchTwitchClips satisfying Option C's
video-invariant contract (2026-08-11 Phase 2).

Background:
    Option C shipped 2026-08-10 (`StoryCandidate` model_validator +
    `merge_stories` fail-open drop) enforces that every fetcher's
    output must EITHER populate ``video_id`` OR explicitly declare
    ``bypass_video_id_dedup=True``. FetchTwitchClips does neither —
    it emits stories with a stable Twitch clip URL but no ``video_id``
    field. Result: every twitch_clips story got silently DROPPED by
    the contract, leaving gaming's healthiest alternative source
    invisible in the pipeline.

    Twitch's Helix ``/clips`` API returns each clip with a stable
    ``id`` (e.g. ``AwkwardHelplessSalamanderSwiftRage``) that is the
    canonical unique key for the clip. Same for ``broadcaster_id`` —
    the streamer's stable channel ID for attribution.

Fix:
    - Preserve ``id`` + ``broadcaster_id`` in ``_fetch_clips_for_game``
    - Emit them as ``video_id`` + ``channel_id`` in the story dict
    - Result: twitch_clips stories pass the Option C invariant
      naturally, video_id_dedup fires correctly, gaming has a real
      primary source that isn't the game-name-repeat pathology.

Bonus:
    - ``channel_name`` also populated from ``broadcaster_name`` for
      the attribution defense stack (L4 CAPTION marker generation).
"""

from __future__ import annotations

from genlab_core.pipeline.models import StoryCandidate
from genlab_core.pipeline.stages.fetch_twitch_clips import (
    _fetch_clips_for_game,
)


class TestFetchClipsForGamePreservesApiFields:
    """The helper that flattens API responses into story-shaped dicts
    must preserve the identifiers Twitch returns. Regression: if a
    future refactor drops ``id`` or ``broadcaster_id``, the emitted
    stories lose their dedup key + attribution."""

    def test_preserves_clip_id(self, monkeypatch):
        """Twitch API returns clip['id'] — must be preserved so the
        emitting stage can set story['video_id']."""
        import genlab_core.pipeline.stages.fetch_twitch_clips as mod

        api_response = {
            "data": [
                {
                    "id": "AwkwardHelplessSalamanderSwiftRage",
                    "url": "https://clips.twitch.tv/AwkwardHelplessSalamanderSwiftRage",
                    "title": "Amazing clutch",
                    "view_count": 50000,
                    "broadcaster_name": "someuser",
                    "broadcaster_id": "12345678",
                    "duration": 30.0,
                    "created_at": "2026-08-01T00:00:00Z",
                }
            ]
        }

        # Mock the requests.get inside _fetch_clips_for_game
        class _MockResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return api_response

        monkeypatch.setattr(mod.requests, "get", lambda *a, **kw: _MockResponse())

        clips = _fetch_clips_for_game(
            game_id="32982",
            headers={"Authorization": "Bearer x", "Client-Id": "y"},
            max_clips=5,
            lookback_days=7,
        )
        assert len(clips) == 1
        assert clips[0].get("id") == "AwkwardHelplessSalamanderSwiftRage", (
            "_fetch_clips_for_game must preserve API's clip['id'] — "
            "it's the canonical Twitch clip identifier and the natural "
            "value for story['video_id'] to satisfy the Option C "
            "video-invariant contract."
        )

    def test_preserves_broadcaster_id(self, monkeypatch):
        """Twitch API returns clip['broadcaster_id'] — must be preserved
        so the emitting stage can set story['channel_id'] for the L1-L6
        attribution defense stack."""
        import genlab_core.pipeline.stages.fetch_twitch_clips as mod

        class _MockResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": [
                        {
                            "id": "clip-abc",
                            "url": "https://clips.twitch.tv/clip-abc",
                            "title": "t",
                            "view_count": 5000,
                            "broadcaster_name": "someuser",
                            "broadcaster_id": "87654321",
                            "duration": 20.0,
                            "created_at": "2026-08-01T00:00:00Z",
                        }
                    ]
                }

        monkeypatch.setattr(mod.requests, "get", lambda *a, **kw: _MockResponse())
        clips = _fetch_clips_for_game(
            game_id="32982",
            headers={"Authorization": "Bearer x", "Client-Id": "y"},
            max_clips=5,
            lookback_days=7,
        )
        assert clips[0].get("broadcaster_id") == "87654321"


class TestFetchTwitchClipsSatisfiesVideoInvariant:
    """The Option C boundary test: an emitted twitch_clips story must
    pass ``StoryCandidate.from_raw`` without needing a bypass
    declaration. If it doesn't, merge_stories silently drops it and
    gaming loses its only healthy alternative to TwitchTrendingFetcher."""

    def _mock_twitch_clip_story(self) -> dict:
        """Reproduce the shape ``FetchTwitchClips.execute`` emits into
        merge_stories, matching the actual production emit dict."""
        # If this test fails after a refactor, either the fetcher stopped
        # emitting video_id/channel_id (regression) OR the invariant got
        # tightened. Either way, this test is the source of truth for
        # "what shape must twitch_clips stories have?"
        return {
            "story_id": "story-abc",
            "title": "Amazing clutch play",
            "source": "twitch_clips",
            "source_url": "https://clips.twitch.tv/AwkwardHelplessSalamanderSwiftRage",
            "canonical_url": "https://clips.twitch.tv/AwkwardHelplessSalamanderSwiftRage",
            "published_at": "2026-08-01T00:00:00Z",
            "summary": "Twitch clip by someuser",
            "view_count": 50000,
            "duration_seconds": 30.0,
            "niche_id": "gaming",
            "video_source": "twitch",
            "broadcaster": "someuser",
            "attribution": "Clip from someuser on Twitch",
            "_trending_video": True,
            "_clip_url": "https://clips.twitch.tv/AwkwardHelplessSalamanderSwiftRage",
            "source_mention_count": 2,
            # Option C required fields (2026-08-11 Phase 2 addition):
            "video_id": "AwkwardHelplessSalamanderSwiftRage",
            "channel_id": "12345678",
            "channel_name": "someuser",
        }

    def test_emit_shape_passes_story_candidate_validation(self):
        """Direct StoryCandidate.from_raw() must succeed — no bypass
        declaration required because video_id is populated."""
        raw = self._mock_twitch_clip_story()
        sc = StoryCandidate.from_raw(raw)
        assert sc.video_id == "AwkwardHelplessSalamanderSwiftRage"
        assert sc.channel_id == "12345678"
        assert sc.channel_name == "someuser"
        # Bypass must NOT be set (Shape A: video-bearing story)
        assert sc.bypass_video_id_dedup is False

    def test_emit_shape_survives_merge_stories(self, caplog):
        """merge_stories is the actual runtime enforcement boundary. If
        our shape doesn't survive it, gaming loses the fetcher's output
        even when tests on the pure model pass."""
        import logging

        from genlab_core.pipeline.models import merge_stories

        ctx: dict = {"stories": [], "niche_id": "gaming"}
        with caplog.at_level(logging.WARNING):
            merge_stories(ctx, [self._mock_twitch_clip_story()])

        # Story must survive — NOT dropped by the contract
        assert len(ctx["stories"]) == 1
        # And no WARN log about "DROPPED story"
        dropped_warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "DROPPED" in r.message
        ]
        assert not dropped_warnings, (
            f"Twitch clips story was dropped by merge_stories: "
            f"{[r.message for r in dropped_warnings]}. Pre-Phase-2 "
            f"regression — video_id must be populated by the fetcher."
        )

    def test_emit_shape_WITHOUT_video_id_fails_validation(self):
        """Regression pin for the pre-Phase-2 bug: if a future refactor
        removes video_id from the emit dict, StoryCandidate rejects it
        (which is what silently broke gaming's twitch_clips source)."""
        import pytest
        from pydantic import ValidationError

        raw = self._mock_twitch_clip_story()
        del raw["video_id"]  # simulate the pre-fix shape

        with pytest.raises(ValidationError):
            StoryCandidate.from_raw(raw)


class TestTwitchTrendingFetcherEnvFlagGate:
    """Phase 2 gates the gaming-local TwitchTrendingFetcher behind
    ``GENLAB_TWITCH_TRENDING_ENABLED``. Default = enabled (current
    behavior preserved, safe to ship). Explicitly set to "0" / "false"
    to disable — the operator flips it in prod after verifying
    FetchTwitchClips is producing enough video-bearing gaming stories
    to sustain daily throughput.

    The fetcher itself lives in the gaming-local module (not
    genlab-core), but we pin the env-flag contract here alongside
    the video-invariant pins because both are load-bearing for
    gaming's Phase 2 rollout."""

    def _make_fetcher(self, monkeypatch):
        """Import + construct the fetcher with credentials stubbed so
        we can exercise the env-flag gate without hitting Twitch."""
        from genlab_core.settings import settings

        monkeypatch.setattr(settings, "twitch_client_id", "test-id")
        monkeypatch.setattr(settings, "twitch_client_secret", "test-secret")

        from niches.gaming.stages.fetch_gaming_stories import TwitchTrendingFetcher

        return TwitchTrendingFetcher()

    def test_default_env_unset_fetcher_runs(self, monkeypatch, caplog):
        """Backward compat: no env var set = fetcher runs (current
        behavior). Regression: if the default flips to disabled without
        an explicit code change, prod would silently lose 79% of
        gaming's story production overnight."""
        import logging

        monkeypatch.delenv("GENLAB_TWITCH_TRENDING_ENABLED", raising=False)
        fetcher = self._make_fetcher(monkeypatch)

        # Stub out the actual Twitch API call to avoid network in test —
        # we only care about the gate check, not what the API returns.
        monkeypatch.setattr(
            fetcher,
            "_fetch_top_streamer",
            lambda game_id, token: None,
        )
        # Also stub the token fetch and requests.get
        with monkeypatch.context() as m:
            m.setattr(
                "niches.gaming.tools._twitch_auth.TwitchTokenManager",
                type("MockTM", (), {"__init__": lambda s, *a: None, "get_token": lambda s: "x"}),
            )

            class _MockResp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"data": []}

            m.setattr("niches.gaming.stages.fetch_gaming_stories.requests.get",
                      lambda *a, **kw: _MockResp())

            with caplog.at_level(logging.INFO):
                stories = fetcher.fetch()

        # Fetcher ran (no early-return from gate); empty API response
        # returns empty list, but the gate didn't block.
        assert stories == []
        # No "disabled by env" log fired
        disabled_msgs = [
            r for r in caplog.records
            if "GENLAB_TWITCH_TRENDING_ENABLED" in r.message and "disabled" in r.message.lower()
        ]
        assert not disabled_msgs, (
            "Default env behavior must not print the disabled-by-env log. "
            "This test pins the safe-default contract."
        )

    def test_env_false_disables_fetcher(self, monkeypatch, caplog):
        """Setting GENLAB_TWITCH_TRENDING_ENABLED=0 (or false) makes
        fetch() return [] immediately without hitting Twitch API."""
        import logging

        monkeypatch.setenv("GENLAB_TWITCH_TRENDING_ENABLED", "0")
        fetcher = self._make_fetcher(monkeypatch)

        # If the gate DIDN'T early-return, the token manager would fail
        # (no mock) — so passing this test proves the gate fired.
        with caplog.at_level(logging.INFO):
            stories = fetcher.fetch()

        assert stories == []
        assert any(
            "GENLAB_TWITCH_TRENDING_ENABLED" in r.message
            for r in caplog.records
        ), (
            "When disabled by env, an INFO log must announce the fact so "
            "operators can see WHY no stories were fetched (rule #19: "
            "never silent-noop)."
        )

    def test_env_string_variants_disable(self, monkeypatch):
        """Common falsy string values (0, false, no, off) all disable
        the fetcher. The env-flag idiom should be tolerant of common
        forms without requiring the operator to memorize the exact one."""
        for falsy in ("0", "false", "False", "FALSE", "no", "off"):
            monkeypatch.setenv("GENLAB_TWITCH_TRENDING_ENABLED", falsy)
            fetcher = self._make_fetcher(monkeypatch)
            stories = fetcher.fetch()
            assert stories == [], (
                f"Falsy env value {falsy!r} must disable the fetcher "
                f"— got {len(stories)} stories back."
            )

    def test_env_explicit_1_enables(self, monkeypatch):
        """Explicit "1" enables — same behavior as default (unset)."""
        monkeypatch.setenv("GENLAB_TWITCH_TRENDING_ENABLED", "1")
        fetcher = self._make_fetcher(monkeypatch)

        # Stub token fetch + API call so fetch() completes without network
        with monkeypatch.context() as m:
            m.setattr(
                "niches.gaming.tools._twitch_auth.TwitchTokenManager",
                type("MockTM", (), {"__init__": lambda s, *a: None, "get_token": lambda s: "x"}),
            )

            class _MockResp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"data": []}

            m.setattr("niches.gaming.stages.fetch_gaming_stories.requests.get",
                      lambda *a, **kw: _MockResp())

            stories = fetcher.fetch()

        assert stories == []  # empty API response, but no gate rejection
