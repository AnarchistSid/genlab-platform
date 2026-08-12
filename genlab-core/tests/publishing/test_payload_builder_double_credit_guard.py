"""2026-08-12 (F-QB-0708): pin the marker-only idempotence guard in
payload_builder.build_payload for source-attribution append.

Motivating audit finding: QB-2026-08 Phase 7 identified 173/173
captions following an identical 7-block template, matching YouTube's
"inauthentic content" signature. Re-verification tonight found the
template signature was partially stale (only ~36% of captions in the
last 14d, not 100%). But the ~36% still had the exact
double-🎬-Original: shape from the audit's example.

Root cause: `payload_builder.py:287` used exact-string idempotence
check `_src_attr not in caption`. The LLM often writes a partial
form ("🎬 Original: @X — " with no URL, or "🎬 Original creator:
@X") while `_src_attr` contains the full URL form. Exact match
missed → guard didn't fire → both variants appear in the caption.

Fix: check for the MARKER prefix `🎬 Original:` (or `Original
creator:`) — not the exact string. Catches every variant the LLM
produces.
"""

from __future__ import annotations

from unittest.mock import patch


class TestDoubleCreditGuard:
    """Every combination of LLM-attribution shape must be recognised
    by the idempotence guard so the pipeline doesn't double-append."""

    def _fields(self, caption: str) -> dict:
        """Minimal fields dict — the guard code only reads a subset."""
        return {
            "caption": caption,
            "hook": "Test hook",
            "hashtags": [],
            "visual_paths": ["/tmp/test.mp4"],
            "video_id": "vid123",
            "source": "youtube_trending",
            "source_channel_title": "TestChannel",
            "niche_id": "gaming",
        }

    def _build_ig(self, caption: str) -> str:
        """Call build_payload for Instagram and return the resulting
        caption. Test focuses on the credit-append behaviour only."""
        from genlab_core.publishing.payload_builder import build_payload

        # build_payload calls format_source_attribution which composes
        # "🎬 Original: @TestChannel — https://youtube.com/watch?v=vid123"
        payload = build_payload(
            self._fields(caption),
            platform="instagram",
        )
        return payload.caption

    def test_no_llm_credit_appends_pipeline_credit(self):
        """LLM wrote nothing about attribution → pipeline appends it."""
        result = self._build_ig("Test hook\n\n#Gaming #Reels")
        assert result.count("🎬 Original:") == 1, (
            f"expected exactly 1 credit line: {result!r}"
        )

    def test_llm_wrote_full_original_skips_append(self):
        """LLM already wrote the full credit line — pipeline must
        NOT double-append."""
        result = self._build_ig(
            "Test hook\n\n#Gaming\n\n🎬 Original: @TestChannel — https://youtube.com/watch?v=vid123"
        )
        assert result.count("🎬 Original:") == 1, (
            f"double-credit detected: {result!r}"
        )

    def test_llm_wrote_partial_original_no_url_skips_append(self):
        """This is THE F-QB-0708 shape: LLM wrote
        `🎬 Original: @X — ` (bare, no URL). Exact-match guard
        missed this. Marker-based guard must catch it."""
        result = self._build_ig(
            "Test hook\n\n#Gaming\n\n🎬 Original: @TestChannel —"
        )
        assert result.count("🎬 Original:") == 1, (
            f"double-credit from partial LLM form: {result!r}"
        )

    def test_llm_wrote_original_creator_variant_skips_append(self):
        """LLM sometimes writes the `Original creator:` variant.
        Second recognised marker."""
        result = self._build_ig(
            "Test hook\n\n#Gaming\n\n🎬 Original creator: @TestChannel"
        )
        assert result.count("🎬 Original") == 1, (
            f"double 'Original' block detected: {result!r}"
        )

    def test_facebook_content_gets_credit_same_shape(self):
        """FB and Threads share the same append path. Regression pin."""
        from genlab_core.publishing.payload_builder import build_payload

        fields = self._fields("Test hook — check this out")
        fields["facebook_content"] = fields["caption"]
        payload = build_payload(fields, platform="facebook")
        # Facebook uses `caption` for its post text
        assert payload.caption.count("🎬 Original:") == 1

    def test_twitter_unchanged(self):
        """Twitter is excluded from the credit-append path (280-char
        budget). Confirm we haven't accidentally roped it in."""
        from genlab_core.publishing.payload_builder import build_payload

        fields = self._fields("Test hook — check this out")
        payload = build_payload(fields, platform="twitter")
        assert "🎬 Original:" not in payload.caption, (
            f"twitter must not receive credit-append: {payload.caption!r}"
        )
