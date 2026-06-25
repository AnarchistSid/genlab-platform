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
