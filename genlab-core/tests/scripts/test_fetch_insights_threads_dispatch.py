"""Pin the 2026-07-22 Threads dispatch wire fix.

History: `run_fetch_insights._fetch_platform_insights` at
line 171 handled instagram / youtube / facebook / twitter but had NO
`elif platform == "threads":` branch. Every Threads post fell through
to `else: return None`, which the caller at line 392 interprets as
"skip" — status stays SUCCESS forever.

Prod state at 2026-07-22 (before fix): 0/5 Threads SUCCESS rows in the
7-day window had ever transitioned to INSIGHTS_48H / INSIGHTS_168H;
all had `views: 0`, `metrics_fetched: NULL`. Learning loop denied
Threads signal on ~40% of publishes.

Class-of-bug: "wire gaps in split adoption" — the P5a metric-collector
split (2026-06-19) moved per-platform fetchers into
`learning/metrics/{fb,ig,threads,...}.py`. The dispatcher migration in
`platforms/metrics/__init__.py` re-exported 4 of them (fb/ig/twitter/yt)
but forgot the 5th (threads). Downstream, `run_fetch_insights` grew
delegate functions for each — but only for the 4 re-exported ones. So
even though `_fetch_threads` existed in the learning package, the
script-path dispatch table couldn't reach it.

These pins lock the platform-dispatch contract so the same split-adoption
gap can't recur silently.
"""

from __future__ import annotations

from unittest.mock import patch

from genlab_core.scripts.run_fetch_insights import _fetch_platform_insights


class TestFetchPlatformInsightsDispatchesThreads:
    def test_threads_dispatch_calls_canonical_fetcher(self) -> None:
        """The dispatcher MUST route platform='threads' to the canonical
        fetcher — not fall through to `else: return None`."""
        expected = {"views": 42, "replies": 3, "reposts": 1}
        with patch(
            "genlab_core.scripts.run_fetch_insights._fetch_threads",
            return_value=expected,
        ) as mock_fetch:
            result = _fetch_platform_insights(
                platform="threads",
                post_id="threads:18106592525026823",
                niche_id="ai_creators",
            )
        assert mock_fetch.called, (
            "Threads dispatch NOT wired — _fetch_threads was never called."
        )
        assert result == expected

    def test_threads_dispatch_strips_platform_prefix(self) -> None:
        """DB stores 'threads:ABC' but the API needs 'ABC'. The dispatcher
        must pass the stripped ID through to _fetch_threads."""
        with patch(
            "genlab_core.scripts.run_fetch_insights._fetch_threads",
            return_value={"views": 1},
        ) as mock_fetch:
            _fetch_platform_insights(
                platform="threads",
                post_id="threads:18106592525026823",
                niche_id="ai_creators",
            )
        call_args = mock_fetch.call_args
        assert call_args.args[0] == "18106592525026823", (
            f"Expected raw post_id passed through; got {call_args.args[0]!r}"
        )

    def test_threads_dispatch_propagates_niche_id(self) -> None:
        """Threads uses per-niche credentials via `resolve_threads_credentials`
        — the dispatcher MUST forward niche_id so the right token is picked."""
        with patch(
            "genlab_core.scripts.run_fetch_insights._fetch_threads",
            return_value={"views": 1},
        ) as mock_fetch:
            _fetch_platform_insights(
                platform="threads",
                post_id="threads:X",
                niche_id="gaming",
            )
        assert mock_fetch.call_args.kwargs.get("niche_id") == "gaming"

    def test_platforms_metrics_reexports_fetch_threads(self) -> None:
        """The peer-fetcher shape must include fetch_threads alongside
        fetch_facebook / fetch_instagram / fetch_youtube / fetch_twitter.
        Missing it is what caused the wire gap in the first place."""
        from genlab_core.platforms import metrics as m

        assert hasattr(m, "fetch_threads"), (
            "platforms.metrics package MUST re-export fetch_threads — "
            "this is the split-adoption tripwire."
        )
        assert "fetch_threads" in m.__all__


class TestFetchPlatformInsightsAllFourFocusPlatformsWired:
    """Rule #23 4-platform focus (YT/FB/IG/Threads) — the dispatcher MUST
    handle every one of these WITHOUT falling through to the else branch."""

    def test_all_four_focus_platforms_have_dispatch_branches(self) -> None:
        """Each of the 4 focus platforms must route to a real fetcher,
        not `None`. If a future refactor moves the dispatch table and
        drops one, this test catches it. YouTube is included as a
        sanity peer (already wired for months)."""
        with patch(
            "genlab_core.scripts.run_fetch_insights._fetch_facebook",
            return_value={"views": 1},
        ), patch(
            "genlab_core.scripts.run_fetch_insights._fetch_instagram",
            return_value={"views": 1},
        ), patch(
            "genlab_core.scripts.run_fetch_insights._fetch_threads",
            return_value={"views": 1},
        ), patch(
            "genlab_core.scripts.run_fetch_insights._fetch_youtube",
            return_value={"views": 1},
        ):
            for platform in ("facebook", "instagram", "threads", "youtube"):
                result = _fetch_platform_insights(
                    platform=platform,
                    post_id=f"{platform}:test-post-id",
                    niche_id="ai_creators",
                )
                assert result is not None, (
                    f"Focus platform {platform!r} fell through to else "
                    f"branch — same class-of-bug as the pre-fix Threads gap."
                )
