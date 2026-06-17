"""Pin the W3.2 post_id normalization in feedback_registration.

Why
---
Pre-fix prod data showed mixed post_id formats in pending_feedback:
- ``18067901951361934`` (IG, bare numeric — wrong)
- ``youtube:BlZaT02z0lw`` (YT, prefixed — correct)
- ``facebook:2183623629126716`` (FB, prefixed — correct)

publishing_analytics consistently uses the prefixed shape, so a JOIN
on post_id silently failed for IG. That broke the bandit's reward
attribution for IG-only publishes — a Week 3 autonomy plan gap.

The fix is a write-time normalization in
``feedback_registration._normalize_post_id``. These pins lock down
the contract.
"""

from __future__ import annotations

import pytest
from genlab_core.publishing.feedback_registration import _normalize_post_id


class TestNormalizePostId:
    def test_bare_ig_id_gets_prefix(self):
        """The headline IG bug: bare numeric → ``instagram:NNN``."""
        assert _normalize_post_id("instagram", "18067901951361934") == (
            "instagram:18067901951361934"
        )

    def test_already_prefixed_unchanged(self):
        """A correctly-prefixed id passes through unchanged. Don't
        accidentally double-prefix."""
        assert _normalize_post_id("youtube", "youtube:BlZaT02z0lw") == "youtube:BlZaT02z0lw"
        assert _normalize_post_id("facebook", "facebook:2183623629126716") == (
            "facebook:2183623629126716"
        )

    def test_empty_passes_through(self):
        """Empty string is the canonical 'no post_id' value — preserve
        it so the caller's existing fallback handling still works."""
        assert _normalize_post_id("instagram", "") == ""

    def test_none_passes_through(self):
        """Defensive — caller might pass None even though type hint says
        str. Don't crash."""
        assert _normalize_post_id("instagram", None) == None  # type: ignore[arg-type]  # noqa: E711

    def test_mismatched_existing_prefix_preserved(self):
        """If somehow the id has a different prefix than ``platform``,
        DON'T re-prefix. Trust the explicit prefix the caller set —
        a future caller might intentionally cross-tag (e.g. for
        cross-platform reposting).

        This is documented in _normalize_post_id's docstring as the
        explicit decision; pin so a future refactor doesn't silently
        change it."""
        # An id explicitly tagged 'crosspost:abc' for an 'instagram'
        # publish should NOT be rewritten to 'instagram:crosspost:abc'.
        assert _normalize_post_id("instagram", "crosspost:abc") == "crosspost:abc"

    @pytest.mark.parametrize(
        "platform,bare_id,expected",
        [
            ("instagram", "DZoxDy2igok", "instagram:DZoxDy2igok"),
            ("youtube", "BlZaT02z0lw", "youtube:BlZaT02z0lw"),
            ("facebook", "2183623629126716", "facebook:2183623629126716"),
            ("threads", "26051610377827590", "threads:26051610377827590"),
            ("twitter", "1234567890", "twitter:1234567890"),
        ],
    )
    def test_all_platforms_normalize_consistently(self, platform, bare_id, expected):
        """Pin per-platform — accidentally treating one differently
        (e.g. dropping prefix only for Twitter) would re-introduce the
        join-mismatch bug for that platform."""
        assert _normalize_post_id(platform, bare_id) == expected
