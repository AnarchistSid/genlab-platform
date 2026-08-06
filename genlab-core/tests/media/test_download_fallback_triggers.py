"""Regression tests for the download-error → fallback-source trigger.

When ``_download_video`` fails on the primary URL, ``_process_one_story``
consults a pattern list to decide whether the failure is worth retrying
via ``VideoSourcer.source_alternative()`` (e.g. YouTube search) vs
giving up. Pre-2026-08-06 the list only covered YouTube bot-detection
patterns; Reddit's "Account authentication is required" error slipped
past → sports pipeline shipped zero blueprints when Reddit blocked all
5 clips.

This module pins the trigger list against the production error strings
we've observed, so the "URL blocked → give up silently" class of bug
can't recur when a new platform-side auth change lands.
"""

from __future__ import annotations

import pytest

from genlab_core.media.download_top_videos import _should_try_alternative


class TestRedditAuthErrors:
    """Reddit yt-dlp errors observed 2026-08-06 that MUST trigger fallback."""

    def test_reddit_auth_required_full_error(self):
        # Verbatim from clutchwire_20260806_050001.log, line 115
        err = (
            "yt-dlp failed for https://v.redd.it/3rl905lxkmhh1: "
            "ERROR: [Reddit] 1vgksal: Account authentication is required. "
            "Use --cookies, --cookies-from-browser, --username and "
            "--password, --netrc-cmd, or --netrc (reddit) to provide "
            "account credentials."
        )
        assert _should_try_alternative(err) is True

    def test_reddit_auth_short_variant(self):
        # Match on the substring regardless of leading context
        assert _should_try_alternative("authentication is required") is True

    def test_reddit_403_blocked_error(self):
        # 403 Blocked is what the Reddit JSON API returns for the
        # same-class problem. Trigger fallback here too.
        err = "403 Client Error: Blocked for url: https://www.reddit.com/r/soccerhighlights/top.json"
        assert _should_try_alternative(err) is True


class TestExistingBotBlockPatternsStillFire:
    """Regression: pre-2026-08-06 patterns must still trigger fallback."""

    @pytest.mark.parametrize(
        "err",
        [
            "ERROR: [youtube] abc123: Sign in to confirm you're not a bot",
            "Please sign in and prove you're not a bot to continue",
            "ERROR: Private video. Sign in if you've been granted access",
            "ERROR: Video unavailable. This video is no longer available",
            "This video is not available in your country",
        ],
    )
    def test_youtube_pattern_still_triggers(self, err):
        assert _should_try_alternative(err) is True


class TestUnrelatedErrorsDoNotTrigger:
    """Genuine one-off failures should not spend budget on alternatives."""

    @pytest.mark.parametrize(
        "err",
        [
            "",
            "ffmpeg: Invalid data found when processing input",
            "OSError: No space left on device",
            "ConnectionResetError: [Errno 104]",
            "ffprobe failed: unrecognized codec",
        ],
    )
    def test_unrelated_errors_do_not_trigger(self, err):
        assert _should_try_alternative(err) is False


class TestNoneAndEmpty:
    """Belt-and-suspenders — the helper must not raise on None/empty."""

    def test_empty_string(self):
        assert _should_try_alternative("") is False

    def test_none(self):
        assert _should_try_alternative(None) is False
