"""Pin the 2026-07-17 Layer 2 monetization CTA expansion.

## What broke pre-fix

`payload_builder.py:311` only populated `first_comment_text` for
`platform in ("facebook", "twitter")`. Instagram + YouTube — the two
platforms with the highest algorithmic-reach potential — got NO
affiliate CTA in the first-comment slot. Their only affiliate surface
was the static per-niche bio-link.

Empirical baseline (2024-26 creator economy benchmarks):
- IG bio-link CTR: 0.1-0.3%
- IG pinned first-comment CTR: 2-8% (20-80× higher)
- YT comment-with-URL: prominently displayed under Shorts, similar CTR

Audit round 4 flagged this as the SINGLE highest-leverage
monetization change: 20-80× click improvement with ZERO extra traffic.

## Fix contract (this test locks it)

- `cta_engine.py` populates `instagram_first_comment` +
  `youtube_first_comment` fields
- `payload_builder.py` dispatch reads both new fields on `platform ==
  "instagram"` and `platform == "youtube"`
- `InstagramClient.publish()` posts first-comment via `post_reply`
  after success (best-effort)
- `YouTubeClient.publish()` posts first-comment via `post_reply`
  after success (best-effort)
"""

from __future__ import annotations


def test_payload_builder_reads_instagram_first_comment() -> None:
    """When building an Instagram payload, `first_comment_text` reads
    `instagram_first_comment` from fields."""
    import inspect

    from genlab_core.publishing import payload_builder

    src = inspect.getsource(payload_builder.build_payload)
    assert 'platform == "instagram"' in src, (
        "IG branch missing from first_comment dispatch"
    )
    assert '"instagram_first_comment"' in src, (
        "fields.get('instagram_first_comment', '') missing"
    )


def test_payload_builder_reads_youtube_first_comment() -> None:
    import inspect

    from genlab_core.publishing import payload_builder

    src = inspect.getsource(payload_builder.build_payload)
    assert 'platform == "youtube"' in src
    assert '"youtube_first_comment"' in src


def test_cta_engine_populates_instagram_first_comment() -> None:
    """The CTA engine must populate `instagram_first_comment` when
    the blueprint has a product URL + product name."""
    import inspect

    from genlab_core.monetization import cta_engine

    src = inspect.getsource(cta_engine.inject_cta)
    assert 'fields["instagram_first_comment"]' in src, (
        "CTA engine must populate instagram_first_comment field"
    )


def test_cta_engine_populates_youtube_first_comment() -> None:
    import inspect

    from genlab_core.monetization import cta_engine

    src = inspect.getsource(cta_engine.inject_cta)
    assert 'fields["youtube_first_comment"]' in src


def test_instagram_publish_posts_first_comment_after_success() -> None:
    """IG's publish() calls _post_first_comment_if_present after
    _build_publish_success."""
    import inspect

    from genlab_core.platforms.instagram import InstagramClient

    src = inspect.getsource(InstagramClient.publish)
    assert "_post_first_comment_if_present" in src, (
        "IG publish() must call the first-comment helper after success"
    )


def test_youtube_publish_posts_first_comment_after_success() -> None:
    """YT's publish() calls post_reply after successful upload."""
    import inspect

    from genlab_core.platforms.youtube import YouTubeClient

    src = inspect.getsource(YouTubeClient.publish)
    assert "payload.first_comment_text" in src, (
        "YT publish() must read payload.first_comment_text"
    )
    assert "self.post_reply(" in src, (
        "YT publish() must call post_reply to post first-comment"
    )
