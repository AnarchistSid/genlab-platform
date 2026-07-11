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
