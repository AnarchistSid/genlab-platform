"""Pin the 2026-07-22 Threads first_comment 3-layer wire.

History: `cta_engine.py` populated `facebook_first_comment`,
`instagram_first_comment`, `youtube_first_comment`, `twitter_first_comment`
— but NOT `threads_first_comment`. Downstream, `payload_builder.py:319-329`
had elif branches for FB/IG/YT/Twitter but not Threads. Further
downstream, `platforms/threads.py:publish()` returned directly without
ever calling `post_reply` even though the method exists at line 241.

Three separate wire gaps stacking on the same intent: Threads posts
should ship with an affiliate reply pinned as the first-comment (same
20-80× CTR pattern as FB/IG/YT per 2026-07-17 Layer 2 monetization).

Class-of-bug pattern: "wire gaps in split adoption" — this is the 3rd
Threads dispatch gap fixed in the 2026-07-22 arc (after
run_fetch_insights.py `f9f186c2` + backfill_insights.py `2898cc1e`).

These pins lock all 3 layers of the wire so a future refactor cant
silently drop one and re-open the gap.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.publishing.payload_builder import build_payload


class TestPayloadBuilderThreadsFirstComment:
    def test_threads_platform_reads_threads_first_comment_field(self) -> None:
        """Layer 2: payload_builder MUST route platform='threads' to
        `fields.get("threads_first_comment", "")`. Missing branch would
        leave payload.first_comment_text = "" (silent monetization loss)."""
        fields = {
            "threads_first_comment": "🔗 Get ProductX: https://example.com/aff/x",
            "threads_content": "some caption",
        }
        payload = build_payload(fields={**fields, "niche_id": "ai_creators"}, platform="threads")
        assert payload.first_comment_text == "🔗 Get ProductX: https://example.com/aff/x", (
            "payload_builder Threads elif branch missing — first_comment_text "
            "silently empty (Layer 2 wire gap)"
        )

    def test_threads_platform_strips_whitespace(self) -> None:
        """Mirrors FB/IG/YT `.strip()` behavior — leading/trailing WS from
        the CTA engine or DB round-trip must not break the reply."""
        fields = {"threads_first_comment": "  hello  \n"}
        payload = build_payload(fields={**fields, "niche_id": "ai_creators"}, platform="threads")
        assert payload.first_comment_text == "hello"

    def test_threads_missing_field_yields_empty(self) -> None:
        """Absent `threads_first_comment` (e.g. no affiliate match) → empty
        string, NOT None. Downstream `if payload.first_comment_text:` guard
        must evaluate False for the empty case."""
        fields = {"threads_content": "some caption"}
        payload = build_payload(fields={**fields, "niche_id": "ai_creators"}, platform="threads")
        assert payload.first_comment_text == ""


class TestCTAEngineWritesThreadsFirstComment:
    def test_inject_cta_sets_threads_first_comment_when_affiliate_present(
        self,
    ) -> None:
        """Layer 1: cta_engine.inject_cta MUST write
        `fields["threads_first_comment"]` when product+url present. Mirrors
        the existing FB/IG/YT/Twitter branches so first-comment monetization
        works uniformly across the 4-platform focus."""
        from genlab_core.monetization.cta_engine import inject_cta

        fields = {
            "threads_content": "Just tested this new AI tool — the results are wild",
            "niche_id": "ai_creators",
        }
        story = {
            "niche_id": "ai_creators",
            "affiliate_product": "ProductX",
            "affiliate_url": "https://example.com/aff/x",
        }
        inject_cta(fields, story)
        assert "threads_first_comment" in fields, (
            "cta_engine Threads first-comment branch missing — Layer 1 wire gap"
        )
        assert fields["threads_first_comment"], "Reply text empty"
        assert "ProductX" in fields["threads_first_comment"]
        assert "example.com" in fields["threads_first_comment"]

    def test_threads_first_comment_respects_500_char_cap(self) -> None:
        """Threads reply cap is 500 chars (same as parent). A very long
        product name + URL must be truncated with ellipsis, not crash the
        publish."""
        from genlab_core.monetization.cta_engine import inject_cta

        fields = {"threads_content": "test", "niche_id": "ai_creators"}
        story = {
            "niche_id": "ai_creators",
            "affiliate_product": "X" * 400,
            "affiliate_url": "https://example.com/" + "a" * 200,
        }
        inject_cta(fields, story)
        assert len(fields.get("threads_first_comment", "")) <= 500


class TestThreadsClientCallsPostReplyAfterPublish:
    def test_publish_calls_post_reply_when_first_comment_present(self) -> None:
        """Layer 3: threads.py `publish()` MUST call `post_reply` after a
        successful parent publish when `payload.first_comment_text` is set.
        This is the reply that actually carries the affiliate URL."""
        from genlab_core.platforms.threads import ThreadsClient

        client = ThreadsClient.__new__(ThreadsClient)
        client._log = MagicMock()
        client.refresh_token_if_needed = MagicMock()

        # Stub the video publish to return a success result
        from genlab_core.platforms.models import PublishResult

        success_result = PublishResult(
            platform="threads",
            success=True,
            post_id="threads:parent-123",
        )
        client._publish_video = MagicMock(return_value=success_result)
        client._publish_image = MagicMock()
        client._publish_text = MagicMock()
        client.post_reply = MagicMock(return_value=True)
        client.platform_id = "threads"

        from genlab_core.platforms.models import PublishPayload

        payload = PublishPayload(
            caption="parent caption",
            media_paths=["/tmp/reel.mp4"],
            media_type="video",
            hashtags=[],
            hook="",
            niche_id="ai_creators",
            first_comment_text="🔗 Get ProductX: https://example.com/aff/x",
        )
        result = client.publish(payload)
        assert result.success
        # THE PIN: post_reply MUST have been called with parent post_id +
        # the payload's first_comment_text.
        assert client.post_reply.called, (
            "Threads publish did not call post_reply — Layer 3 wire gap. "
            "The affiliate reply never gets posted."
        )
        call_kwargs = client.post_reply.call_args.kwargs
        assert call_kwargs.get("parent_id") == "threads:parent-123"
        assert call_kwargs.get("text") == "🔗 Get ProductX: https://example.com/aff/x"

    def test_publish_does_not_call_post_reply_when_first_comment_empty(self) -> None:
        """Symmetric: if no first_comment_text, no reply is attempted.
        Prevents an accidental unconditional post_reply that would spam
        the parent with an empty reply."""
        from genlab_core.platforms.threads import ThreadsClient
        from genlab_core.platforms.models import PublishPayload, PublishResult

        client = ThreadsClient.__new__(ThreadsClient)
        client._log = MagicMock()
        client.refresh_token_if_needed = MagicMock()
        client._publish_video = MagicMock(
            return_value=PublishResult(platform="threads", success=True, post_id="p-1")
        )
        client.post_reply = MagicMock(return_value=True)
        client.platform_id = "threads"

        payload = PublishPayload(
            caption="parent",
            media_paths=["/tmp/reel.mp4"],
            media_type="video",
            hashtags=[],
            hook="",
            niche_id="ai_creators",
            first_comment_text="",
        )
        client.publish(payload)
        assert not client.post_reply.called

    def test_publish_does_not_call_post_reply_on_parent_failure(self) -> None:
        """If the parent publish failed, there's no valid post_id to reply
        under — must not attempt the reply."""
        from genlab_core.platforms.threads import ThreadsClient
        from genlab_core.platforms.models import PublishPayload, PublishResult

        client = ThreadsClient.__new__(ThreadsClient)
        client._log = MagicMock()
        client.refresh_token_if_needed = MagicMock()
        client._publish_video = MagicMock(
            return_value=PublishResult(platform="threads", success=False, error="oops")
        )
        client.post_reply = MagicMock()
        client.platform_id = "threads"

        payload = PublishPayload(
            caption="parent",
            media_paths=["/tmp/reel.mp4"],
            media_type="video",
            hashtags=[],
            hook="",
            niche_id="ai_creators",
            first_comment_text="🔗 link",
        )
        client.publish(payload)
        assert not client.post_reply.called
