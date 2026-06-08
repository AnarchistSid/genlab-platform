"""Tests for :mod:`genlab_core.platforms.metrics.legacy_aliases`.

Refactor-#10 wrap-up: the per-platform alias logic that used to be
duplicated 8 times across ``pipeline/stages/fetch_insights.py`` and
``scripts/run_fetch_insights.py`` now lives in one place. These tests
pin the alias contract so a future canonical-key rename can't quietly
break the downstream SharePoint readers + analytics composite-score
code that still consume the legacy key names.
"""

from __future__ import annotations

import pytest
from genlab_core.platforms.metrics.legacy_aliases import add_legacy_aliases


class TestInstagramAliases:
    def test_saves_to_saved(self) -> None:
        """The SharePoint column was named ``saved`` (past-tense verb);
        canonical is ``saves`` (noun). Both must coexist."""
        out = add_legacy_aliases({"saves": 5, "likes": 10}, "instagram")
        assert out["saved"] == 5
        assert out["saves"] == 5  # canonical preserved
        assert out["likes"] == 10

    def test_watch_time_ms_to_minutes(self) -> None:
        """Legacy column stored watch time in minutes-as-float; canonical
        is milliseconds-as-int. Conversion: round(ms/60000, 1)."""
        out = add_legacy_aliases({"watch_time_ms": 90_000}, "instagram")
        assert out["watch_time_minutes"] == 1.5
        assert out["watch_time_ms"] == 90_000

    def test_no_aliases_when_canonical_keys_missing(self) -> None:
        """Aliases only fire when the canonical key is present — the
        helper doesn't fabricate zero values."""
        out = add_legacy_aliases({"likes": 5}, "instagram")
        assert "saved" not in out
        assert "watch_time_minutes" not in out


class TestTwitterAliases:
    def test_shares_to_retweets_and_comments_to_replies(self) -> None:
        out = add_legacy_aliases({"shares": 3, "comments": 7}, "twitter")
        assert out["retweets"] == 3
        assert out["replies"] == 7
        # Canonical keys preserved.
        assert out["shares"] == 3
        assert out["comments"] == 7

    @pytest.mark.parametrize("platform_name", ["twitter", "x", "x_twitter"])
    def test_alias_set_identical_for_all_x_platform_names(self, platform_name: str) -> None:
        """Both legacy ("twitter"), short ("x"), and canonical ("x_twitter")
        platform names trigger the same alias set. A new alias entry under
        ``twitter`` would otherwise silently miss the registry-id callers."""
        out = add_legacy_aliases({"shares": 1, "comments": 2}, platform_name)
        assert out["retweets"] == 1
        assert out["replies"] == 2


class TestYoutubePassthrough:
    def test_no_aliases_added(self) -> None:
        """YouTube canonical shape matches the legacy SharePoint columns
        directly — no aliases needed."""
        out = add_legacy_aliases({"views": 100, "likes": 5}, "youtube")
        assert out == {"views": 100, "likes": 5}


class TestFacebookPassthrough:
    def test_no_aliases_added(self) -> None:
        """Facebook canonical shape matches the legacy SharePoint columns
        directly — no aliases needed (the pipeline-stage path uses a
        DIFFERENT FB endpoint; that divergence is handled at the call
        site, not by aliasing)."""
        out = add_legacy_aliases({"shares": 2, "reactions": 4}, "facebook")
        assert out == {"shares": 2, "reactions": 4}


class TestEdgeCases:
    def test_none_input_returns_none(self) -> None:
        """``None`` passes through — callers branch on ``None`` to mark
        SKIPPED rather than write zeros to SharePoint."""
        assert add_legacy_aliases(None, "instagram") is None

    def test_empty_dict_returns_empty_dict(self) -> None:
        """Empty canonical metrics → empty output, no synthesized aliases."""
        assert add_legacy_aliases({}, "instagram") == {}

    def test_unknown_platform_passthrough(self) -> None:
        """Unknown platform names return the metrics unchanged (as a copy).
        Lets a new platform be wired in incrementally before its alias
        table entry is declared."""
        result = add_legacy_aliases({"x": 1}, "myspace")
        assert result == {"x": 1}

    def test_returns_a_copy_not_the_input(self) -> None:
        """The helper must NOT mutate the caller's dict — back-compat
        callers pass through dict references and a hidden mutation would
        propagate."""
        src = {"saves": 5}
        out = add_legacy_aliases(src, "instagram")
        assert out is not src
        assert "saved" not in src  # source dict untouched
