"""Pin: 2026-06-29 (PR AD) — gaming's reddit fetcher polls the right
subs and protects against r/livestreamfail IRL/drama drift.

Background: today's gaming-content-fit analysis found that when
Twitch + Steam trending = pre-order trailers (Stellar Blade™,
Avatar Legends, CAPTAIN TSUBASA), the clip sourcer correctly refuses
Pexels stock-footage fallback (``clip_sourcer.py:932-937`` —
"Stock footage hurts channel credibility"). Result: gaming hits
0-blueprint days when trending doesn't include actively-played
games with real clips.

The fix is video-first Reddit fetchers — every subreddit below is
one where THE POST IS A CLIP, so clip_sourcer always has real
playable content even on a pre-order-trailer day:

  * r/GamingClips   — pure clip submissions
  * r/LivestreamFail — Twitch fails/highlights, 600K+ subs
  * r/twitchclips   — pure Twitch clip resharing (newly added PR AD)

The pin guards against:
  1. Removal of either headline rescue sub (LivestreamFail /
     GamingClips) from the active ``reddit:`` block.
  2. Anyone wiring the dead ``reddit_sources:`` block expecting it
     to be polled (it has no production callers as of 2026-06-29).
  3. Drift of negative_keywords for LivestreamFail/twitchclips
     spillover into IRL / streamer-drama / cam-show content.

Without these pins, a future cleanup could silently dismantle the
rescue path and re-create the 0-blueprint problem.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GAMING_SOURCES = REPO_ROOT / "CriticalRush" / "niches" / "gaming" / "config" / "sources.yaml"


def _load_gaming_sources() -> dict:
    """Load gaming sources.yaml as a dict for assertion."""
    return yaml.safe_load(GAMING_SOURCES.read_text())


def _reddit_sub_names() -> list[str]:
    """Return the lower-cased subreddit names from the active reddit block."""
    cfg = _load_gaming_sources()
    subs = cfg.get("reddit", {}).get("subreddits", [])
    names = []
    for s in subs:
        if isinstance(s, dict):
            names.append(str(s.get("name", "")).lower())
        else:
            names.append(str(s).lower())
    return names


class TestGamingRedditVideoFirstSubsWired:
    """The active ``reddit:`` block (consumed by FetchRedditClips) must
    include the curated video-first gaming subs."""

    def test_reddit_block_is_enabled(self):
        """The reddit fetcher must be on. If somebody disables it, the
        rescue path silently goes away."""
        cfg = _load_gaming_sources()
        reddit_cfg = cfg.get("reddit", {})
        # ``enabled: false`` would short-circuit FetchRedditClips at line
        # 53-54 of fetch_reddit_clips.py — explicit pin against accidental
        # disable.
        assert reddit_cfg.get("enabled") is True, (
            "Gaming's reddit fetcher must be enabled. Disabling it removes "
            "the rescue path for pre-order-trailer-only days (see PR AD "
            "rationale in the reddit: block of sources.yaml)."
        )

    def test_gaming_sources_includes_livestreamfail(self):
        """r/LivestreamFail must be wired. This is the highest-volume
        video-first gaming sub on Reddit."""
        names = _reddit_sub_names()
        assert "livestreamfail" in names, (
            "r/LivestreamFail must be in gaming's reddit.subreddits. It's "
            "the rescue path when Twitch trending is pre-order trailers. "
            f"Current subs: {names}"
        )

    def test_gaming_sources_includes_gamingclips(self):
        """r/GamingClips must be wired. Pure clip-submission sub (rules
        enforce video)."""
        names = _reddit_sub_names()
        assert "gamingclips" in names, (
            "r/GamingClips must be in gaming's reddit.subreddits. It's the "
            "purest video-first sub (no text discussion allowed by mod "
            f"rules). Current subs: {names}"
        )

    def test_gaming_sources_includes_twitchclips(self):
        """r/twitchclips (added PR AD, 2026-06-29) — pure Twitch clip
        resharing, ~100% video, all gaming."""
        names = _reddit_sub_names()
        assert "twitchclips" in names, (
            "r/twitchclips must be in gaming's reddit.subreddits. Added "
            "PR AD as the highest signal-to-noise video-first gaming sub. "
            f"Current subs: {names}"
        )

    def test_reddit_per_sub_limit_is_set(self):
        """per_sub_limit must be explicit. The default in fetch_for_niche
        is 15 — pin it here to make the candidate budget visible."""
        cfg = _load_gaming_sources()
        per_sub_limit = cfg.get("reddit", {}).get("per_sub_limit")
        assert isinstance(per_sub_limit, int) and per_sub_limit > 0, (
            "reddit.per_sub_limit must be a positive int. Without it, "
            "fetch_for_niche falls back to its 15 default and the actual "
            "candidate budget is invisible from the config."
        )

    def test_reddit_time_window_is_day_or_hour(self):
        """time_window must be ``day`` or ``hour``. ``week`` would make the
        rescue path stale and ``year`` is plainly wrong for a daily cadence."""
        cfg = _load_gaming_sources()
        tw = cfg.get("reddit", {}).get("time_window")
        assert tw in ("day", "hour"), (
            f"reddit.time_window must be 'day' or 'hour' (got {tw!r}). "
            "Anything wider stales the rescue path past usefulness for a "
            "daily-cadence pipeline."
        )

    def test_reddit_listing_is_top(self):
        """listing must be ``top``. The RSS-first path in
        fetch_subreddit only handles ``top`` (RSS doesn't expose other
        listings); ``hot``/``new`` would force the unreliable JSON path
        which 429s from Hetzner."""
        cfg = _load_gaming_sources()
        listing = cfg.get("reddit", {}).get("listing")
        assert listing == "top", (
            f"reddit.listing must be 'top' (got {listing!r}). The RSS-first "
            "path in fetch_subreddit only covers 'top'; other values force "
            "the JSON path which 429s from data-center IPs."
        )


class TestGamingNegativeKeywordsProtectAgainstIRLDrift:
    """r/LivestreamFail + r/twitchclips spillover into streamer drama /
    IRL / cam-show content is the dominant off-niche failure mode. The
    relevance_filter hard-rejects on negative_keywords match (
    ``relevance_filter.py:61-63``)."""

    def test_just_chatting_rejected(self):
        """Twitch's #1 non-gaming category. Must hard-reject."""
        cfg = _load_gaming_sources()
        negs = [k.lower() for k in cfg.get("content_filter", {}).get("negative_keywords", [])]
        assert "just chatting" in negs, (
            "'just chatting' must be in gaming's negative_keywords. It's "
            "Twitch's #1 non-gaming category; without this rejection, "
            "LivestreamFail/twitchclips spillover bypasses the relevance "
            "gate."
        )

    def test_irl_stream_rejected(self):
        """IRL streams have huge LSF spillover. Must hard-reject."""
        cfg = _load_gaming_sources()
        negs = [k.lower() for k in cfg.get("content_filter", {}).get("negative_keywords", [])]
        assert "irl stream" in negs, (
            "'irl stream' must be in gaming's negative_keywords. Heavy LSF spillover otherwise."
        )

    def test_livestreamfail_drift_guards_present(self):
        """PR AD added 5 drift guards for the specific failure mode of
        high-upvote LSF posts that aren't gaming. Pin each one."""
        cfg = _load_gaming_sources()
        negs = [k.lower() for k in cfg.get("content_filter", {}).get("negative_keywords", [])]
        required = [
            "streamer drama",  # LSF's #1 viral category that isn't gameplay
            "streamer banned",  # banned-streamer compilations
            "cam show",  # sexual content drift
            "hot tub stream",  # specific LSF category that surges periodically
        ]
        missing = [k for k in required if k not in negs]
        assert not missing, (
            f"PR AD drift guards missing from negative_keywords: {missing}. "
            "These guard the specific off-niche failure modes for "
            "r/LivestreamFail + r/twitchclips traffic."
        )


class TestDormantRedditSourcesBlockNotWired:
    """The ``reddit_sources:`` block (separate from ``reddit:``) feeds
    ``genlab_core.intel.reddit_fetcher.fetch_reddit_videos`` — which has
    NO production callers as of 2026-06-29. Pin that the dormant block
    is documented as inactive so nobody adds subs there expecting them
    to be polled."""

    def test_dormant_block_documented_as_inactive(self):
        """The reddit_sources block must carry a header comment that flags
        it as inactive so future readers don't add subs there."""
        text = GAMING_SOURCES.read_text()
        # The comment block immediately preceding reddit_sources: must
        # mention "INACTIVE" or "do NOT add" so readers find the warning
        # at the same scroll position as the block they're tempted to
        # edit.
        idx = text.find("reddit_sources:")
        assert idx > 0, "reddit_sources block must exist (kept for documentation)"
        # Look at the 800 chars BEFORE the block for the inactive marker.
        preamble = text[max(0, idx - 800) : idx]
        assert "INACTIVE" in preamble, (
            "The reddit_sources: block must be preceded by an INACTIVE "
            "comment so anyone editing it sees the warning. Otherwise "
            "someone adds a sub here expecting it to be polled and silently "
            "wastes effort."
        )
