"""Pin: 2026-06-29 (PR AE) — movies' reddit fetcher polls the right
subs and protects against text-discussion / merch / cast-news drift.

Background: today's movies content-fit analysis found that movies'
bottleneck is that trailers ≠ viral content. Viral movie content is
moments, reactions, theories. The pipeline today leans on TMDB
trending + studio YouTube feeds, both of which are
trailer-dominated. Of the 12 missing-media failures Apr-Jun
(DEEP_AUDIT data) every one was a pre-order trailer for an
unreleased movie — no real clip to source. That mirrors the gaming
bottleneck PR #643 (today) fixed for CriticalRush.

The fix is video-first Reddit fetchers — every subreddit below is
one where THE POST IS A CLIP / SHOT, so FetchRedditClips gives
downstream stages real playable content even on a trailer-only day:

  * r/movieclips    — Movieclips-style scene resharing
  * r/MovieDetails  — moments + behind-the-scenes
  * r/MovieReactions — reaction-shot compilations (newly added PR AE)
  * r/Cinemagraphs  — looping movie GIFs/videos (newly added PR AE)
  * r/cinematography — DoP analysis with clips
  * r/marvelstudios / r/DC_Cinematic — franchise-thread subs
  * r/horror — genre sub with high clip density (jump-scares)

The pin guards against:
  1. Removal of either headline rescue sub (movieclips /
     MovieDetails) from the active ``reddit:`` block.
  2. Removal of either PR AE addition (MovieReactions /
     Cinemagraphs) before they've earned their place in metrics.
  3. Anyone wiring the dead ``reddit_sources:`` block expecting it
     to be polled (it has no production callers as of 2026-06-29,
     same audit as gaming PR #643).
  4. Drift of negative_keywords for r/movies / r/MovieReactions
     spillover into spoiler-thread / cast-news / merch content.

Without these pins, a future cleanup could silently dismantle the
rescue path and re-create the trailer-only-day problem.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MOVIES_SOURCES = REPO_ROOT / "SpliceReel" / "config" / "sources.yaml"


def _load_movies_sources() -> dict:
    """Load SpliceReel sources.yaml as a dict for assertion."""
    return yaml.safe_load(MOVIES_SOURCES.read_text())


def _reddit_sub_names() -> list[str]:
    """Return the lower-cased subreddit names from the active reddit block."""
    cfg = _load_movies_sources()
    subs = cfg.get("reddit", {}).get("subreddits", [])
    names = []
    for s in subs:
        if isinstance(s, dict):
            names.append(str(s.get("name", "")).lower())
        else:
            names.append(str(s).lower())
    return names


class TestMoviesRedditVideoFirstSubsWired:
    """The active ``reddit:`` block (consumed by FetchRedditClips) must
    include the curated video-first movie subs."""

    def test_reddit_block_is_enabled(self):
        """The reddit fetcher must be on. If somebody disables it, the
        rescue path silently goes away on trailer-only days."""
        cfg = _load_movies_sources()
        reddit_cfg = cfg.get("reddit", {})
        # ``enabled: false`` would short-circuit FetchRedditClips at line
        # 53-54 of fetch_reddit_clips.py — explicit pin against accidental
        # disable.
        assert reddit_cfg.get("enabled") is True, (
            "Movies' reddit fetcher must be enabled. Disabling it removes "
            "the rescue path for trailer-only-TMDB days (see PR AE "
            "rationale in the reddit: block of sources.yaml)."
        )

    def test_movies_sources_includes_movieclips(self):
        """r/movieclips must be wired. Movieclips-style scene resharing
        is the cleanest video-first sub for film moments."""
        names = _reddit_sub_names()
        assert "movieclips" in names, (
            "r/movieclips must be in movies' reddit.subreddits. It's the "
            f"cleanest video-first scene-resharing sub. Current subs: {names}"
        )

    def test_movies_sources_includes_moviedetails(self):
        """r/MovieDetails must be wired. Moments + behind-the-scenes with
        high upvote density on video posts."""
        names = _reddit_sub_names()
        assert "moviedetails" in names, (
            "r/MovieDetails must be in movies' reddit.subreddits. High "
            "upvote density on video posts is the signal we need for "
            f"moments. Current subs: {names}"
        )

    def test_movies_sources_includes_moviereactions(self):
        """r/MovieReactions (added PR AE, 2026-06-29) — reaction-shot
        compilations, the literal definition of viral video content."""
        names = _reddit_sub_names()
        assert "moviereactions" in names, (
            "r/MovieReactions must be in movies' reddit.subreddits. Added "
            "PR AE for reaction-shot compilations — the viral-content "
            f"category trailers can never satisfy. Current subs: {names}"
        )

    def test_movies_sources_includes_cinemagraphs(self):
        """r/Cinemagraphs (added PR AE, 2026-06-29) — looping movie
        GIFs/videos, ~100% video-only by sub rules."""
        names = _reddit_sub_names()
        assert "cinemagraphs" in names, (
            "r/Cinemagraphs must be in movies' reddit.subreddits. Added "
            "PR AE — low volume but ~100% video-only by mod rules, so "
            f"every fetch is usable content. Current subs: {names}"
        )

    def test_movies_sources_includes_discussion_subs(self):
        """r/movies and r/boxoffice provide volume + discussion signal
        even though they have lower video density. negative_keywords
        guard against text-discussion drift."""
        names = _reddit_sub_names()
        for sub in ("movies", "boxoffice"):
            assert sub in names, (
                f"r/{sub} must be in movies' reddit.subreddits for "
                f"volume + discussion signal. Current subs: {names}"
            )

    def test_reddit_per_sub_limit_is_set(self):
        """per_sub_limit must be explicit. The default in fetch_for_niche
        is 15 — pin it here to make the candidate budget visible."""
        cfg = _load_movies_sources()
        per_sub_limit = cfg.get("reddit", {}).get("per_sub_limit")
        assert isinstance(per_sub_limit, int) and per_sub_limit > 0, (
            "reddit.per_sub_limit must be a positive int. Without it, "
            "fetch_for_niche falls back to its 15 default and the actual "
            "candidate budget is invisible from the config."
        )

    def test_reddit_time_window_is_day_or_hour(self):
        """time_window must be ``day`` or ``hour``. ``week`` would make the
        rescue path stale and ``year`` is plainly wrong for a daily cadence."""
        cfg = _load_movies_sources()
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
        cfg = _load_movies_sources()
        listing = cfg.get("reddit", {}).get("listing")
        assert listing == "top", (
            f"reddit.listing must be 'top' (got {listing!r}). The RSS-first "
            "path in fetch_subreddit only covers 'top'; other values force "
            "the JSON path which 429s from data-center IPs."
        )


class TestMoviesNegativeKeywordsProtectAgainstRedditDrift:
    """r/movies, r/MovieReactions, and r/MovieDetails spillover into
    text-discussion / cast-news / merch content is the dominant
    off-niche failure mode for these subs. The relevance_filter
    hard-rejects on negative_keywords match (``relevance_filter.py:61-63``)."""

    def test_reddit_drift_guards_present(self):
        """PR AE added 5 drift guards for the specific failure modes of
        the new reddit fetchers. Pin each one."""
        cfg = _load_movies_sources()
        negs = [k.lower() for k in cfg.get("content_filter", {}).get("negative_keywords", [])]
        required = [
            "spoiler thread",  # r/movies' #1 text-only category
            "cast announcement",  # non-video Variety/Deadline reshare
            "merchandise",  # off-niche commerce (Funko, etc.)
            "fan interview",  # narrow talk-content drift
            "casting news",  # text-discussion on r/movies/r/marvelstudios
        ]
        missing = [k for k in required if k not in negs]
        assert not missing, (
            f"PR AE drift guards missing from negative_keywords: {missing}. "
            "These guard the specific off-niche failure modes for "
            "r/movies + r/MovieReactions + r/MovieDetails traffic."
        )

    def test_bare_interview_not_added_to_negs(self):
        """Pin the deliberate decision NOT to add bare 'interview' to
        negative_keywords. r/movies has lots of fan-text-interviews
        (drift), but YouTube studio channels also post legitimate
        'director interview about scene X' videos that we want to ingest.
        Use ``fan interview`` / ``casting news`` instead — narrower drift
        targets that don't punish legitimate trailer-companion content."""
        cfg = _load_movies_sources()
        negs = [k.lower() for k in cfg.get("content_filter", {}).get("negative_keywords", [])]
        assert "interview" not in negs, (
            "Bare 'interview' must NOT be in movies' negative_keywords — "
            "it would hard-reject legitimate 'director interview about "
            "scene X' videos. Use the narrower 'fan interview' / "
            "'casting news' drift guards (added PR AE) instead."
        )


class TestDormantRedditSourcesBlockNotWired:
    """The ``reddit_sources:`` block (separate from ``reddit:``) feeds
    ``genlab_core.intel.reddit_fetcher.fetch_reddit_videos`` — which has
    NO production callers as of 2026-06-29 (same audit as gaming PR
    #643). Pin that the dormant block is documented as inactive so
    nobody adds subs there expecting them to be polled."""

    def test_dormant_block_documented_as_inactive(self):
        """The reddit_sources block must carry a header comment that flags
        it as inactive so future readers don't add subs there."""
        text = MOVIES_SOURCES.read_text()
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
