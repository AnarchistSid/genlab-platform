"""PR #568 (2026-06-25) — copyright_safety primitives + YouTube wire.

Pins:

  * derive_source_url maps YouTube sources to canonical URL; unknown
    sources / empty inputs return None
  * format_youtube_attribution returns the suffix or '' (idempotent
    via substring match at the call site)
  * check_copyright_attribution registered into policy_gate on import
  * Check fires WARN only for YouTube + missing attribution
  * Non-YouTube platforms get 'allow' (per-platform impls deferred)
  * Operator-supplied source_url overrides the missing-attribution warn
  * YouTube payload appends attribution to description
  * Idempotent: re-publishing doesn't double-append
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ── derive_source_url ──────────────────────────────────────────────


def test_derive_youtube_url_for_known_sources():
    from genlab_core.compliance.copyright_safety import derive_source_url

    for source in [
        "youtube_trending",
        "youtube_search",
        "youtube_playlist",
        "youtube_rss",
        "youtube",
    ]:
        url = derive_source_url("dQw4w9WgXcQ", source)
        assert url == "https://youtube.com/watch?v=dQw4w9WgXcQ"


def test_derive_returns_none_for_empty_inputs():
    from genlab_core.compliance.copyright_safety import derive_source_url

    assert derive_source_url("", "youtube_trending") is None
    assert derive_source_url("abc", "") is None
    assert derive_source_url(None, "youtube_trending") is None
    assert derive_source_url("abc", None) is None


def test_derive_returns_none_for_unknown_source():
    """Future per-platform implementations will add twitch_trending,
    tiktok_search, reddit_top etc. Today they return None — the
    attribution check then fires WARN."""
    from genlab_core.compliance.copyright_safety import derive_source_url

    assert derive_source_url("abc123", "twitch_trending") is None
    assert derive_source_url("abc123", "tiktok_search") is None


def test_derive_is_case_insensitive():
    from genlab_core.compliance.copyright_safety import derive_source_url

    assert derive_source_url("abc", "YOUTUBE_TRENDING") == derive_source_url(
        "abc", "youtube_trending"
    )


# ── format_youtube_attribution ─────────────────────────────────────


def test_attribution_format_returns_suffix_for_known_source():
    from genlab_core.compliance.copyright_safety import format_youtube_attribution

    bp = {"video_id": "abc123", "source": "youtube_trending"}
    out = format_youtube_attribution(bp)
    assert "Footage:" in out
    assert "youtube.com/watch?v=abc123" in out


def test_attribution_returns_empty_for_unknown_source():
    from genlab_core.compliance.copyright_safety import format_youtube_attribution

    bp = {"video_id": "abc", "source": "twitch_trending"}
    assert format_youtube_attribution(bp) == ""


def test_attribution_returns_empty_for_missing_video_id():
    from genlab_core.compliance.copyright_safety import format_youtube_attribution

    bp = {"video_id": "", "source": "youtube_trending"}
    assert format_youtube_attribution(bp) == ""


def test_attribution_starts_with_newlines_for_readability():
    """Attribution goes on its own line so YouTube description has
    body-text → blank line → 'Footage: …' rather than running into
    the previous sentence."""
    from genlab_core.compliance.copyright_safety import format_youtube_attribution

    bp = {"video_id": "x", "source": "youtube_trending"}
    out = format_youtube_attribution(bp)
    assert out.startswith("\n\n")


# ── policy_gate registration ───────────────────────────────────────


def test_check_registered_in_policy_gate_at_import():
    """The compliance package's __init__ imports copyright_safety,
    which registers the check at module-import time. Pin that the
    registry contains the check name."""
    import genlab_core.compliance  # noqa: F401 — triggers registration
    from genlab_core.compliance.policy_gate import registered_check_names

    assert "copyright_attribution_check" in registered_check_names()


# ── check_copyright_attribution behavior ───────────────────────────


def test_check_returns_allow_for_non_youtube_platform():
    """Phase A only checks YouTube — other platforms get 'allow'
    pending per-platform implementations. Pin so a future refactor
    doesn't accidentally extend the warn-fire to all platforms
    without explicit per-platform logic."""
    from genlab_core.compliance.copyright_safety import check_copyright_attribution

    bp = {}  # no source attribution
    for platform in ["instagram", "facebook", "tiktok", "x_twitter", "threads"]:
        result = check_copyright_attribution(bp, platform, "gaming")
        assert result.decision == "allow"


def test_check_returns_allow_when_youtube_has_derivable_url():
    from genlab_core.compliance.copyright_safety import check_copyright_attribution

    bp = {"video_id": "abc123", "source": "youtube_trending"}
    result = check_copyright_attribution(bp, "youtube", "gaming")
    assert result.decision == "allow"


def test_check_returns_allow_when_explicit_source_url_field():
    """Operator-supplied source_url overrides — useful for clips
    sourced outside our automated fetchers (e.g. manual operator
    additions from creator submissions)."""
    from genlab_core.compliance.copyright_safety import check_copyright_attribution

    bp = {"source_url": "https://twitch.tv/some_clip"}
    result = check_copyright_attribution(bp, "youtube", "gaming")
    assert result.decision == "allow"


def test_check_returns_warn_when_youtube_missing_attribution():
    """The core gate behavior: YouTube publish + no source URL +
    no source_url field → WARN with 'missing_source_attribution'
    reason."""
    from genlab_core.compliance.copyright_safety import check_copyright_attribution

    bp = {"source": "twitch_trending"}  # unknown to derive_source_url
    result = check_copyright_attribution(bp, "youtube", "gaming")
    assert result.decision == "warn"
    assert "missing_source_attribution" in result.reasons
    assert "video_id_present" in result.metadata
    assert "source" in result.metadata


def test_check_warn_includes_metadata_for_forensics():
    """The metadata helps operators debug WHY the check fired —
    is video_id present? what source did we have?"""
    from genlab_core.compliance.copyright_safety import check_copyright_attribution

    bp = {"video_id": "", "source": "unknown_source"}
    result = check_copyright_attribution(bp, "youtube", "gaming")
    assert result.decision == "warn"
    assert result.metadata["video_id_present"] is False
    assert result.metadata["source"] == "unknown_source"


def test_check_handles_non_mapping_blueprint_safely():
    """Defensive: caller passes a non-dict (programmer error) →
    'allow' rather than crash. The policy_gate's wrapper will
    catch any uncaught raise, but it's still safer to be defensive
    at the check level."""
    from genlab_core.compliance.copyright_safety import check_copyright_attribution

    result = check_copyright_attribution(None, "youtube", "gaming")  # type: ignore[arg-type]
    assert result.decision == "allow"


# ── policy_gate aggregate integration ──────────────────────────────


def test_policy_gate_aggregate_uses_attribution_check():
    """check_publish_policy aggregates the registered checks. With
    just copyright_attribution_check + a YouTube publish missing
    attribution, the aggregate is 'warn' (most-restrictive)."""
    from genlab_core.compliance.policy_gate import check_publish_policy

    bp = {"source": "twitch_trending"}
    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy(bp, "youtube", "gaming")
    assert result.decision == "warn"
    assert "copyright_attribution_check:missing_source_attribution" in result.reasons


def test_policy_gate_aggregate_allow_when_attribution_present():
    from genlab_core.compliance.policy_gate import check_publish_policy

    bp = {"video_id": "abc123", "source": "youtube_trending"}
    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy(bp, "youtube", "gaming")
    assert result.decision == "allow"


# ── source pin on registration site-effect ─────────────────────────


def test_source_pin_registration_in_init():
    """The compliance __init__ MUST import copyright_safety so the
    registration side-effect fires at package-import time.

    PR #569 reformatted the imports into a parenthesized multi-line
    group; whitespace-normalise + check for the import identifier."""
    import re

    import genlab_core.compliance as pkg

    src = Path(pkg.__file__).read_text()
    normalised = re.sub(r"\s+", " ", src)
    assert "from genlab_core.compliance import" in normalised
    assert "copyright_safety" in normalised


# ── format_source_attribution (PR #A, 2026-07-10) ──────────────────
#
# Sibling of format_youtube_attribution designed for audience-facing
# captions on every platform (FB, IG, Threads, YT description) — not
# just the Content ID text-classifier. Landed after the Markanimation
# incident where a real creator DM'd asking for credit on a reposted
# video and we discovered:
#   (1) push_to_backlog only wired attribution into youtube_content
#   (2) the format was URL-only ("Footage: <url>"), no channel handle
# These tests pin the "human-readable channel handle + URL" contract
# and the substring-idempotence needed for re-runs to not double-append.


def test_source_attribution_includes_channel_handle_when_known():
    from genlab_core.compliance.copyright_safety import format_source_attribution

    bp = {
        "video_id": "FW1it6lrPNQ",
        "source": "youtube_trending",
        "source_channel_title": "Markanimation",
    }
    out = format_source_attribution(bp)
    assert "@Markanimation" in out
    assert "youtube.com/watch?v=FW1it6lrPNQ" in out
    # Emoji marker so viewers spot the credit line at a glance
    assert "\U0001f3ac" in out  # 🎬


def test_source_attribution_falls_back_to_url_only_when_no_channel():
    from genlab_core.compliance.copyright_safety import format_source_attribution

    bp = {"video_id": "abc123", "source": "youtube_trending"}
    out = format_source_attribution(bp)
    assert "@" not in out  # no bogus handle
    assert "youtube.com/watch?v=abc123" in out


def test_source_attribution_reads_channel_name_field_too():
    """Fetcher writes ``channel_name`` on TrendingVideo; older story
    paths may use ``channel_title``. The helper accepts any of the
    three aliases so pipeline compat doesn't require a rename sweep."""
    from genlab_core.compliance.copyright_safety import format_source_attribution

    for key in ("source_channel_title", "channel_name", "channel_title"):
        out = format_source_attribution(
            {
                "video_id": "x",
                "source": "youtube_trending",
                key: "MAKI",
            }
        )
        assert "@MAKI" in out


def test_source_attribution_returns_empty_for_unknown_source():
    from genlab_core.compliance.copyright_safety import format_source_attribution

    bp = {
        "video_id": "abc",
        "source": "twitch_trending",
        "source_channel_title": "someone",
    }
    assert format_source_attribution(bp) == ""


def test_source_attribution_returns_empty_for_missing_video_id():
    from genlab_core.compliance.copyright_safety import format_source_attribution

    bp = {"video_id": "", "source": "youtube_trending", "source_channel_title": "x"}
    assert format_source_attribution(bp) == ""


def test_source_attribution_strips_channel_whitespace():
    from genlab_core.compliance.copyright_safety import format_source_attribution

    bp = {
        "video_id": "y",
        "source": "youtube_trending",
        "source_channel_title": "  MAKI  ",
    }
    out = format_source_attribution(bp)
    assert "@MAKI —" in out  # trimmed, no leading/trailing spaces


def test_source_attribution_substring_idempotent():
    """push_to_backlog appends this line to captions with an
    ``if credit not in text`` guard. This test pins the fixed
    string shape the guard depends on — if the format ever
    changes, the guard becomes silently ineffective and captions
    would double-append on re-runs."""
    from genlab_core.compliance.copyright_safety import format_source_attribution

    bp = {
        "video_id": "abc",
        "source": "youtube_trending",
        "source_channel_title": "MAKI",
    }
    out = format_source_attribution(bp)
    # If someone changes the format to include a timestamp or nonce,
    # this test fires and forces them to reconsider idempotence.
    assert out == "\U0001f3ac Original: @MAKI — https://youtube.com/watch?v=abc"


def test_source_attribution_returns_empty_for_non_mapping():
    from genlab_core.compliance.copyright_safety import format_source_attribution

    assert format_source_attribution(None) == ""  # type: ignore[arg-type]
    assert format_source_attribution("not a dict") == ""  # type: ignore[arg-type]


# ── Twitch attribution fallback (PR #TwitchAttribution, 2026-07-11) ─
#
# Layer 5 revealed that gaming had 0% attribution because the Twitch
# source path emits ``source="twitch_clips"`` — not in
# ``_SOURCE_URL_TEMPLATES``, so ``derive_source_url`` returned None.
# The fallback now uses the pre-formed ``video_url`` / ``source_url``
# / ``canonical_url`` field directly for Twitch-sourced blueprints and
# reads the creator handle from ``broadcaster``.


def test_source_attribution_twitch_uses_video_url_directly():
    """Twitch clip URLs contain broadcaster + clip slugs (not derivable
    from a single ID). The fallback reads the pre-formed URL directly."""
    from genlab_core.compliance.copyright_safety import format_source_attribution

    bp = {
        "source": "twitch_clips",
        "video_url": "https://clips.twitch.tv/AmazedTiredStorkFUNgineer",
        "broadcaster": "streamerX",
    }
    out = format_source_attribution(bp)
    assert "@streamerX" in out
    assert "https://clips.twitch.tv/AmazedTiredStorkFUNgineer" in out


def test_source_attribution_twitch_falls_back_to_source_url():
    """Some story shapes carry the URL as ``source_url`` instead of
    ``video_url`` — the fallback should try both."""
    from genlab_core.compliance.copyright_safety import format_source_attribution

    bp = {
        "source": "twitch_clips",
        "source_url": "https://www.twitch.tv/streamer/clip/abc",
        "broadcaster": "streamer",
    }
    out = format_source_attribution(bp)
    assert "@streamer" in out
    assert "https://www.twitch.tv/streamer/clip/abc" in out


def test_source_attribution_twitch_rejects_non_url_video_url():
    """Defence against garbage ``video_url`` values (empty string,
    non-URL text) that could result in a bogus attribution line."""
    from genlab_core.compliance.copyright_safety import format_source_attribution

    for bad in ("", "not_a_url", "   ", "clip123"):
        bp = {
            "source": "twitch_clips",
            "video_url": bad,
            "broadcaster": "streamer",
        }
        assert format_source_attribution(bp) == ""


def test_source_attribution_broadcaster_used_only_when_channel_missing():
    """YouTube's source_channel_title / channel_name / channel_title
    still win over Twitch's broadcaster field when both are present.
    Ordering pin — swapping the order would silently prefer
    broadcaster for YouTube-sourced content, which is wrong."""
    from genlab_core.compliance.copyright_safety import format_source_attribution

    bp = {
        "video_id": "abc",
        "source": "youtube_trending",
        "source_channel_title": "YT_channel",
        "broadcaster": "twitch_streamer",  # should not be used
    }
    out = format_source_attribution(bp)
    assert "@YT_channel" in out
    assert "twitch_streamer" not in out


def test_source_attribution_twitch_url_only_when_no_broadcaster():
    """Twitch clip URL is enough on its own — even without a
    broadcaster field, the URL alone anchors the attribution."""
    from genlab_core.compliance.copyright_safety import format_source_attribution

    bp = {
        "source": "twitch_clips",
        "video_url": "https://clips.twitch.tv/xyz",
    }
    out = format_source_attribution(bp)
    assert "@" not in out
    assert "https://clips.twitch.tv/xyz" in out


# ── Layer 3 (PR #Layer3, 2026-07-11) ───────────────────────────────
#
# check_copyright_attribution can now escalate from 'warn' to 'block'
# via GENLAB_ATTRIBUTION_LAYER3_ENFORCE=1. YouTube-only. Default off so
# shipping this PR is a no-op until an operator flips the flag.


def test_layer3_default_is_warn_not_block(monkeypatch):
    """Ship-day contract: without the env flag, the gate stays 'warn'
    (observation-only). Any regression that flips the default silently
    would block every YT publish that has no derivable source URL."""
    from genlab_core.compliance.copyright_safety import check_copyright_attribution

    monkeypatch.delenv("GENLAB_ATTRIBUTION_LAYER3_ENFORCE", raising=False)
    bp = {"video_id": "", "source": "operator_upload"}
    result = check_copyright_attribution(bp, "youtube", "gaming")
    assert result.decision == "warn"
    assert "missing_source_attribution" in result.reasons
    assert result.metadata["layer3_enforce"] is False


def test_layer3_flip_returns_block_for_youtube(monkeypatch):
    """With GENLAB_ATTRIBUTION_LAYER3_ENFORCE=1 the gate escalates
    to 'block' for YouTube publishes missing source attribution.
    This is the load-bearing behaviour operators toggle after the
    Layer 5 attribution_present_pct card holds ≥98% for 1-2 weeks."""
    from genlab_core.compliance.copyright_safety import check_copyright_attribution

    monkeypatch.setenv("GENLAB_ATTRIBUTION_LAYER3_ENFORCE", "1")
    bp = {"video_id": "", "source": "operator_upload"}
    result = check_copyright_attribution(bp, "youtube", "gaming")
    assert result.decision == "block"
    assert "missing_source_attribution" in result.reasons
    assert result.metadata["layer3_enforce"] is True


def test_layer3_flip_does_not_affect_meta_platforms(monkeypatch):
    """Even with the flag set, non-YouTube platforms return 'allow'
    unchanged. Meta enforcement is a 24h Rights Manager sweep — covered
    by fb_survival_check, not this gate. Extending Layer 3 to Meta
    platforms is a deliberate follow-up decision, not an accidental
    side-effect of flipping one env var."""
    from genlab_core.compliance.copyright_safety import check_copyright_attribution

    monkeypatch.setenv("GENLAB_ATTRIBUTION_LAYER3_ENFORCE", "1")
    bp = {"video_id": "", "source": "operator_upload"}
    for platform in ("facebook", "instagram", "threads", "tiktok"):
        result = check_copyright_attribution(bp, platform, "gaming")
        assert result.decision == "allow", (
            f"{platform} must stay 'allow' under Layer 3 — "
            "Meta-platform enforcement is a separate PR"
        )


def test_layer3_does_not_block_youtube_with_derivable_url(monkeypatch):
    """The block should only fire when attribution is genuinely missing.
    A blueprint with a derivable source URL (video_id + youtube source)
    stays 'allow' even under enforcement — the fair-use posture is
    intact."""
    from genlab_core.compliance.copyright_safety import check_copyright_attribution

    monkeypatch.setenv("GENLAB_ATTRIBUTION_LAYER3_ENFORCE", "1")
    bp = {"video_id": "abc123", "source": "youtube_trending"}
    result = check_copyright_attribution(bp, "youtube", "gaming")
    assert result.decision == "allow"
