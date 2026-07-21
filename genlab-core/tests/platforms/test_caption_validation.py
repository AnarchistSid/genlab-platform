"""PR #Layer4 (2026-07-11) — publisher-side attribution validation.

Layer 4 sits at the API-POST boundary of the 4 platform clients
(facebook, instagram, youtube, threads) as the last line of defense
in the attribution-safety stack. If every upstream layer fails to
attach a credit line to the caption, this backstop catches it.

Env flag: ``GENLAB_ATTRIBUTION_LAYER4_BLOCK=1`` escalates warn → block.
Default off — shipping is a no-op until deliberately flipped.

Tests here pin:

  1. ``validate_caption_has_attribution`` correctness on the 2 recognised
     caption markers (🎬 Original, Footage) — post-2026-07-11 audit
     the source_url escape hatch was REMOVED because Twitch directory
     URLs were satisfying the check while shipping empty-of-credit
     captions to real audiences
  2. Rejection of captions lacking a marker
  3. ``layer4_block_enabled`` env-flag semantics (default off)
  4. Source-pin on the 4 platform clients — each wires the validator
     before its API call, in warn mode by default
"""

from __future__ import annotations

import re
from pathlib import Path


def _normalise(src: str) -> str:
    return re.sub(r"\s+", " ", src)


# ── validate_caption_has_attribution ───────────────────────────────


def test_validate_accepts_original_marker():
    from genlab_core.platforms.caption_validation import (
        validate_caption_has_attribution,
    )

    cap = (
        "The Popipo trend just hit anime and it's chaos.\n\n"
        "\U0001f3ac Original: @MAKI — https://youtube.com/watch?v=abc"
    )
    ok, reason = validate_caption_has_attribution(cap)
    assert ok is True
    assert reason is None


def test_validate_accepts_footage_marker():
    from genlab_core.platforms.caption_validation import (
        validate_caption_has_attribution,
    )

    cap = "Some caption body.\n\nFootage: https://youtube.com/watch?v=abc"
    ok, reason = validate_caption_has_attribution(cap)
    assert ok is True
    assert reason is None


def test_validate_rejects_source_url_alone_after_2026_07_11_tightening():
    """Post-2026-07-11 audit: source_url no longer satisfies the
    validation on its own. The gaming case demonstrated the failure
    mode — video_url populated, caption empty of credit line, users
    saw nothing. The escape hatch is gone; caption must carry the
    marker."""
    from genlab_core.platforms.caption_validation import (
        validate_caption_has_attribution,
    )

    cap = "Just a plain caption without any credit line"
    ok, reason = validate_caption_has_attribution(
        cap,
        source_url="https://youtube.com/watch?v=custom",
    )
    assert ok is False
    assert reason == "missing_attribution_line"


def test_validate_rejects_missing_all_signals():
    from genlab_core.platforms.caption_validation import (
        validate_caption_has_attribution,
    )

    cap = "Just a plain caption without any credit line"
    ok, reason = validate_caption_has_attribution(cap)
    assert ok is False
    assert reason == "missing_attribution_line"


def test_validate_marker_match_is_case_insensitive():
    """Operators may format captions differently — the substring match
    should tolerate case variation on the marker text."""
    from genlab_core.platforms.caption_validation import (
        validate_caption_has_attribution,
    )

    cap = "Body.\n\n\U0001f3ac ORIGINAL: @X — url"
    ok, _ = validate_caption_has_attribution(cap)
    assert ok is True


def test_validate_ignores_source_url_after_2026_07_11_tightening():
    """Sibling to the tightening pin — even a well-formed source_url
    is now insufficient without a caption marker. Pin the contract
    strictly."""
    from genlab_core.platforms.caption_validation import (
        validate_caption_has_attribution,
    )

    cap = "No credit line"
    ok, _ = validate_caption_has_attribution(
        cap,
        source_url="https://twitch.tv/directory/game/xyz",
    )
    assert ok is False


def test_validate_returns_missing_on_empty_caption_no_url():
    from genlab_core.platforms.caption_validation import (
        validate_caption_has_attribution,
    )

    ok, reason = validate_caption_has_attribution("")
    assert ok is False
    assert reason == "missing_attribution_line"


# ── Audience-facing invariant pin (post-2026-07-11) ────────────────


def test_source_url_no_longer_bypasses_validation():
    """Load-bearing pin for the whole attribution stack. This
    behaviour changed on 2026-07-11 after today's gaming case
    demonstrated the previous escape hatch shipping empty-of-credit
    captions to real audiences.

    If a future refactor re-introduces the source_url short-circuit,
    this test fires. Do NOT delete this test to make it pass — the
    old behaviour is what let the failure through in the first
    place."""
    from genlab_core.platforms.caption_validation import (
        validate_caption_has_attribution,
    )

    # Every realistic source_url form should NOT bypass an empty
    # caption. Belt-and-suspenders on the tightening.
    for url in (
        "https://youtube.com/watch?v=abc",
        "https://clips.twitch.tv/abc",
        "https://www.twitch.tv/directory/game/xyz",  # today's gaming case
        "https://www.facebook.com/share/r/abc/",
        "http://example.com/",
    ):
        ok, _ = validate_caption_has_attribution("empty caption", source_url=url)
        assert ok is False, (
            f"source_url {url!r} must not bypass validation — audience would see an uncredited post"
        )


# ── layer4_block_enabled ───────────────────────────────────────────


def test_layer4_block_defaults_off(monkeypatch):
    from genlab_core.platforms.caption_validation import layer4_block_enabled

    monkeypatch.delenv("GENLAB_ATTRIBUTION_LAYER4_BLOCK", raising=False)
    assert layer4_block_enabled() is False


def test_layer4_block_reads_env_flag_at_call_time(monkeypatch):
    """Operators can toggle without a process restart — the flag is
    read via os.environ.get at call time, not cached at import."""
    from genlab_core.platforms.caption_validation import layer4_block_enabled

    monkeypatch.setenv("GENLAB_ATTRIBUTION_LAYER4_BLOCK", "1")
    assert layer4_block_enabled() is True
    monkeypatch.setenv("GENLAB_ATTRIBUTION_LAYER4_BLOCK", "0")
    assert layer4_block_enabled() is False


# ── Platform-client wire pins ──────────────────────────────────────
#
# Source pins on the 4 platform clients. If a refactor drops the
# import or the validator call, these fire at import time. Full
# behavioural tests would need each client's fixture machinery — the
# source pin is the pragmatic backstop.


def test_facebook_client_wires_layer4_validator():
    import genlab_core.platforms.facebook as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "from genlab_core.platforms.caption_validation import" in src
    assert "validate_caption_has_attribution" in src
    assert "layer4_block_enabled" in src
    assert "[layer4] Facebook" in src


def test_instagram_client_wires_layer4_validator():
    import genlab_core.platforms.instagram as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "from genlab_core.platforms.caption_validation import" in src
    assert "validate_caption_has_attribution" in src
    assert "layer4_block_enabled" in src
    assert "[layer4] Instagram" in src


def test_youtube_client_wires_layer4_validator():
    import genlab_core.platforms.youtube as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "from genlab_core.platforms.caption_validation import" in src
    assert "validate_caption_has_attribution" in src
    assert "layer4_block_enabled" in src
    assert "[layer4] YouTube" in src


def test_threads_client_wires_layer4_validator():
    import genlab_core.platforms.threads as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "from genlab_core.platforms.caption_validation import" in src
    assert "validate_caption_has_attribution" in src
    assert "layer4_block_enabled" in src
    assert "[layer4] Threads" in src


def test_twitter_client_wires_layer4_validator():
    """Post-2026-07-13 audit follow-up (G1). X/Twitter was the last
    ships-real-content client without Layer 4 — reference wire pattern
    mirrors the 4 pre-existing clients."""
    import genlab_core.platforms.x_twitter as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "from genlab_core.platforms.caption_validation import" in src
    assert "validate_caption_has_attribution" in src
    assert "layer4_block_enabled" in src
    assert "[layer4] Twitter" in src


def test_tiktok_client_wires_layer4_validator():
    """Post-2026-07-13 audit follow-up (G2). TikTok's real publisher
    lives in ``genlab_core.publishing.tiktok_client`` (the
    ``platforms/tiktok.py`` module is a stub that returns error until
    ``TIKTOK_AUDIT_APPROVED=true``). Layer 4 wire lives in the REAL
    publisher so that when the audit lands and audiences see content,
    the backstop is already in place."""
    import genlab_core.publishing.tiktok_client as mod

    src = _normalise(Path(mod.__file__).read_text())
    assert "from genlab_core.platforms.caption_validation import" in src
    assert "validate_caption_has_attribution" in src
    assert "layer4_block_enabled" in src
    assert "[layer4] TikTok" in src


# ── Behavioural pin: block-branch must actually short-circuit ──────
#
# Post-2026-07-13 audit follow-up (G8). Source pins above catch
# refactors that remove the import or the validator call, but they do
# NOT catch a refactor that deletes the ``if not _l4_ok:`` guard
# block itself. The adversarial-audit call-out was concrete: someone
# could keep the validator invocation for optics + delete the return-
# on-failure branch, and every source pin still passes. These
# behavioural tests exercise the actual short-circuit — with the
# block flag ON + a caption lacking any marker, publish() must return
# ``success=False`` with a "Layer 4" error string, WITHOUT calling
# the platform's HTTP layer.


class _NoHTTPRaise(AssertionError):
    """Raised when a Layer 4 block-behavioural test's platform client
    reaches the HTTP layer despite the block being on. If you see
    this, the ``if not _l4_ok:`` guard was silently deleted."""


def _make_no_marker_payload():
    """Standard test payload with no attribution marker in caption."""
    from pathlib import Path as _P

    from genlab_core.platforms.models import PublishPayload

    return PublishPayload(
        caption="Just a plain caption. No credit line at all.",
        media_paths=[_P("/tmp/test-l4-block.mp4")],
        media_type="video",
        hashtags=["#test"],
        hook="Watch this",
        niche_id="gaming",
    )


def test_facebook_block_branch_short_circuits(monkeypatch):
    from genlab_core.platforms.facebook import FacebookClient

    monkeypatch.setenv("GENLAB_ATTRIBUTION_LAYER4_BLOCK", "1")
    client = FacebookClient(page_id="0", access_token="test_token", api_version="v21.0")

    # Facebook does a token pre-flight BEFORE Layer 4; force it to
    # pass so we exercise the L4 branch. Any HTTP call after that is
    # a bug — L4 block must fire BEFORE the API.
    from unittest.mock import patch as _patch

    with (
        _patch.object(client, "_validate_token_preflight", return_value=True),
        _patch(
            "genlab_core.platforms.facebook._META_SESSION.post",
            side_effect=_NoHTTPRaise("Layer 4 block failed to short-circuit"),
        ),
    ):
        result = client.publish(_make_no_marker_payload())
    assert result.success is False
    assert "Layer 4" in result.error


def test_instagram_block_branch_short_circuits(monkeypatch):
    from genlab_core.platforms.instagram import InstagramClient

    monkeypatch.setenv("GENLAB_ATTRIBUTION_LAYER4_BLOCK", "1")
    client = InstagramClient(access_token="test_token", ig_user_id="0", api_version="v21.0")
    from unittest.mock import patch as _patch

    with (
        _patch.object(client, "_validate_token_preflight", return_value=True, create=True),
        _patch(
            "genlab_core.platforms.cdn_upload.upload_to_cdn",
            side_effect=_NoHTTPRaise("L4 block failed"),
        ),
        _patch(
            "genlab_core.platforms.instagram._META_SESSION.post",
            side_effect=_NoHTTPRaise("L4 block failed"),
        ),
    ):
        result = client.publish(_make_no_marker_payload())
    assert result.success is False
    assert "Layer 4" in result.error


def test_youtube_block_branch_short_circuits(monkeypatch):
    from genlab_core.platforms.youtube import YouTubeClient

    monkeypatch.setenv("GENLAB_ATTRIBUTION_LAYER4_BLOCK", "1")
    client = YouTubeClient(
        client_id="test",
        client_secret="test",
        refresh_token="test",
    )
    from unittest.mock import patch as _patch

    with (
        _patch.object(client, "_validate_token_preflight", return_value=True, create=True),
        _patch(
            "genlab_core.platforms.youtube.requests.post",
            side_effect=_NoHTTPRaise("L4 block failed"),
        ),
    ):
        result = client.publish(_make_no_marker_payload())
    assert result.success is False
    assert "Layer 4" in result.error


def test_threads_block_branch_short_circuits(monkeypatch):
    from genlab_core.platforms.threads import ThreadsClient

    monkeypatch.setenv("GENLAB_ATTRIBUTION_LAYER4_BLOCK", "1")
    client = ThreadsClient(access_token="test_token", user_id="0")
    from unittest.mock import patch as _patch

    with (
        _patch.object(client, "_validate_token_preflight", return_value=True, create=True),
        _patch(
            "genlab_core.platforms.threads._META_SESSION.post",
            side_effect=_NoHTTPRaise("L4 block failed"),
        ),
    ):
        result = client.publish(_make_no_marker_payload())
    assert result.success is False
    assert "Layer 4" in result.error


def test_twitter_block_branch_short_circuits(monkeypatch):
    from genlab_core.platforms.x_twitter import XTwitterClient

    monkeypatch.setenv("GENLAB_ATTRIBUTION_LAYER4_BLOCK", "1")
    client = XTwitterClient(
        api_key="test",
        api_secret="test",
        access_token="test",
        access_secret="test",
    )
    from unittest.mock import patch as _patch

    # X/Twitter goes through tweepy's Client — patch the internal
    # single-tweet helper so any escape from the L4 gate raises. Also
    # force the rate-limit precheck to pass.
    with (
        _patch.object(client, "_is_currently_rate_limited", return_value=False),
        _patch.object(
            client,
            "_post_single_tweet",
            side_effect=_NoHTTPRaise("L4 block failed"),
        ),
    ):
        result = client.publish(_make_no_marker_payload())
    assert result.success is False
    assert "Layer 4" in result.error


def test_tiktok_block_branch_raises(monkeypatch):
    """TikTok's real publisher raises ValueError from Layer 4 rather
    than returning a PublishResult (the ``publish_video`` signature
    returns a dict, not a Result object). Same short-circuit contract:
    no HTTP call after the block."""
    from genlab_core.publishing.tiktok_client import TikTokClient

    monkeypatch.setenv("GENLAB_ATTRIBUTION_LAYER4_BLOCK", "1")
    client = TikTokClient(
        client_key="test",
        client_secret="test",
        access_token="test",
        refresh_token="test",
        audit_approved=True,
    )
    from unittest.mock import patch as _patch

    import pytest as _pytest

    with _patch(
        "genlab_core.publishing.tiktok_client.requests.post",
        side_effect=_NoHTTPRaise("L4 block failed"),
    ):
        with _pytest.raises(ValueError, match="Layer 4"):
            client.publish_video(
                "/tmp/test-l4-block.mp4",
                "Just a plain caption. No credit line at all.",
            )


class TestTruncatedMarkerRejection:
    """2026-07-21 (Agent 1 side-finding): substring-only check was
    accepting truncated markers like `🎬 Original: @` with no handle.
    Live-observed in scheduled blueprints. Tightened to reject when
    tail after marker is empty OR just a bare '@' prefix."""

    def test_bare_at_symbol_rejected(self):
        """Tail = '@' → rejected (Agent 1's live-observed case)."""
        from genlab_core.platforms.caption_validation import (
            validate_caption_has_attribution,
        )
        ok, reason = validate_caption_has_attribution("check this out 🎬 Original: @")
        assert ok is False
        assert reason == "missing_attribution_line"

    def test_marker_alone_rejected(self):
        """Tail is empty → rejected."""
        from genlab_core.platforms.caption_validation import (
            validate_caption_has_attribution,
        )
        ok, _ = validate_caption_has_attribution("check this out 🎬 Original:")
        assert ok is False

    def test_short_handle_still_accepted(self):
        """Tail with real 3+ char content → accepted."""
        from genlab_core.platforms.caption_validation import (
            validate_caption_has_attribution,
        )
        ok, _ = validate_caption_has_attribution("great video 🎬 Original: @xyz")
        assert ok is True

    def test_url_only_accepted(self):
        """Tail with just a URL (no @-handle) → accepted."""
        from genlab_core.platforms.caption_validation import (
            validate_caption_has_attribution,
        )
        ok, _ = validate_caption_has_attribution(
            "nice moment 🎬 Original: https://youtube.com/watch?v=abc"
        )
        assert ok is True
