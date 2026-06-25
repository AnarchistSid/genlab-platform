"""PR #569 (2026-06-25) — in-blueprint spam-pattern detection.

Pins:

  * check_spam_patterns registered into policy_gate at import time
  * Sub-check signals (hashtag stuffing, caps yelling, emoji
    stuffing) each fire independently with named reasons
  * Multiple signals combine into one warn decision (forensic detail
    in reasons + metadata)
  * Defensive: non-mapping blueprint → allow
  * Operator-extensible BANNED_HASHTAGS via env var
"""

from __future__ import annotations

from unittest.mock import patch

# ── registration ───────────────────────────────────────────────────


def test_check_registered_in_policy_gate_at_import():
    """The compliance package's __init__ imports spam_detection,
    triggering register_check at module-import time."""
    import genlab_core.compliance  # noqa: F401 — triggers registration
    from genlab_core.compliance.policy_gate import registered_check_names

    assert "spam_pattern_check" in registered_check_names()


# ── hashtag stuffing sub-check ─────────────────────────────────────


def test_hashtag_count_above_soft_threshold_warns():
    """16-30 hashtags = soft warning (above the empirical reach
    cliff at ~15)."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {"hashtags": " ".join(f"#tag{i}" for i in range(20))}
    result = check_spam_patterns(bp, "instagram", "gaming")
    assert result.decision == "warn"
    assert "hashtag_count_above_soft_threshold" in result.reasons
    assert result.metadata.get("hashtag_count") == 20


def test_hashtag_count_above_hard_limit_warns():
    """>30 hashtags = Instagram's documented limit; reach throttling
    confirmed."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {"hashtags": " ".join(f"#tag{i}" for i in range(35))}
    result = check_spam_patterns(bp, "instagram", "gaming")
    assert result.decision == "warn"
    assert "hashtag_count_exceeds_hard_limit" in result.reasons


def test_normal_hashtag_count_does_not_warn():
    """1-15 hashtags is the optimal range — should not fire."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {
        "hashtags": "#gaming #ps5 #playstation #review",
        "caption": "Normal caption with proper case and content.",
    }
    result = check_spam_patterns(bp, "instagram", "gaming")
    assert result.decision == "allow"


def test_banned_hashtag_use_warns():
    """Follow-train + spam-engagement tags trigger reach throttling."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {
        "hashtags": "#gaming #f4f #fun",
        "caption": "Normal caption.",
    }
    result = check_spam_patterns(bp, "instagram", "gaming")
    assert result.decision == "warn"
    assert "banned_hashtag_used" in result.reasons
    assert "f4f" in result.metadata["banned_tags_present"]


def test_hashtag_field_accepts_list_shape():
    """The blueprint may have hashtags as list OR space-separated
    string — same as payload_builder. Pin both shapes."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {"hashtags": ["#followforfollow", "#gaming"], "caption": "Normal text."}
    result = check_spam_patterns(bp, "instagram", "gaming")
    assert result.decision == "warn"
    assert "banned_hashtag_used" in result.reasons


def test_banned_hashtags_env_extension(monkeypatch):
    """Operators can extend the banned list via env var without
    a code deploy."""
    monkeypatch.setenv("GENLAB_BANNED_HASHTAGS_EXTRA", "trending,viral")
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {"hashtags": "#gaming #trending", "caption": "Normal text."}
    result = check_spam_patterns(bp, "instagram", "gaming")
    assert result.decision == "warn"
    assert "trending" in result.metadata["banned_tags_present"]


# ── caps-yelling sub-check ─────────────────────────────────────────


def test_all_caps_caption_warns():
    """>70% uppercase letters = yelling signal."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {"caption": "INSANE GAMING MOMENT THAT BROKE EVERYTHING", "hashtags": ""}
    result = check_spam_patterns(bp, "instagram", "gaming")
    assert result.decision == "warn"
    assert "caption_all_caps_yelling" in result.reasons
    assert "caps_ratio" in result.metadata


def test_mixed_case_caption_does_not_warn():
    """Normal sentence-cased captions stay under the 70% threshold."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {
        "caption": "Look at this amazing play that I just discovered!",
        "hashtags": "#gaming",
    }
    result = check_spam_patterns(bp, "instagram", "gaming")
    assert result.decision == "allow"


def test_short_caption_below_threshold_skipped():
    """<10 letters is too short to meaningfully measure ratio.
    Defensive — don't fire on a caption like 'OK!' that happens
    to be 100% uppercase."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {"caption": "OK!", "hashtags": ""}
    result = check_spam_patterns(bp, "instagram", "gaming")
    assert "caption_all_caps_yelling" not in result.reasons


def test_caps_ratio_ignores_digits_and_punctuation():
    """The denominator is LETTER count, not total chars. A caption
    like '100% FREE!!!' should NOT trigger on the letters alone
    being all uppercase if there are too few letters total."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {"caption": "100% FREE!!!", "hashtags": ""}  # 8 letters total
    result = check_spam_patterns(bp, "instagram", "gaming")
    # Too short (<10 letters) → no warn
    assert "caption_all_caps_yelling" not in result.reasons


# ── emoji-stuffing sub-check ──────────────────────────────────────


def test_emoji_stuffing_warns():
    """>20% of caption chars being emoji = stuffing pattern."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {"caption": "🔥🔥🔥amazing🔥🔥🔥", "hashtags": ""}
    result = check_spam_patterns(bp, "instagram", "gaming")
    assert result.decision == "warn"
    assert "emoji_stuffing" in result.reasons


def test_single_emoji_accent_does_not_warn():
    """A normal caption with one trailing emoji is fine — under the
    20% density threshold."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {"caption": "Check out this amazing gaming play 🔥", "hashtags": ""}
    result = check_spam_patterns(bp, "instagram", "gaming")
    assert "emoji_stuffing" not in result.reasons


def test_all_emoji_caption_fires_specific_reason():
    """Content-light signal — caption is JUST emoji. Fires both
    'emoji_stuffing' (density check) AND 'caption_all_emoji'
    (specific signal for forensic clarity)."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {"caption": "🔥🔥🔥🔥🔥", "hashtags": ""}
    result = check_spam_patterns(bp, "instagram", "gaming")
    assert result.decision == "warn"
    assert "caption_all_emoji" in result.reasons


# ── multi-signal combination ──────────────────────────────────────


def test_multiple_signals_combine_into_one_warn():
    """A blueprint with several spam patterns produces ONE warn
    with ALL reasons listed — forensic detail for operator review.

    Caption is engineered to trigger ALL 4 sub-signals:
      * caps yelling (100% upper)
      * emoji stuffing (10 emoji / 30 chars = 0.33 > 0.20 threshold)
      * hashtag soft threshold (25 tags > 15)
      * banned hashtag (#f4f)
    """
    from genlab_core.compliance.spam_detection import check_spam_patterns

    bp = {
        "caption": "AMAZING MOMENT 🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥",
        "hashtags": " ".join(f"#tag{i}" for i in range(25)) + " #f4f",
    }
    result = check_spam_patterns(bp, "instagram", "gaming")
    assert result.decision == "warn"
    # All 4 sub-checks should have fired
    assert "hashtag_count_above_soft_threshold" in result.reasons
    assert "banned_hashtag_used" in result.reasons
    assert "caption_all_caps_yelling" in result.reasons
    assert "emoji_stuffing" in result.reasons


# ── defensive ──────────────────────────────────────────────────────


def test_non_mapping_blueprint_returns_allow():
    """Caller passed wrong type → 'allow' rather than crash."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    result = check_spam_patterns(None, "instagram", "gaming")  # type: ignore[arg-type]
    assert result.decision == "allow"


def test_empty_blueprint_returns_allow():
    """Blueprint with no caption + no hashtags → 'allow'. Nothing
    to flag."""
    from genlab_core.compliance.spam_detection import check_spam_patterns

    result = check_spam_patterns({}, "instagram", "gaming")
    assert result.decision == "allow"


def test_aggregate_via_policy_gate():
    """check_publish_policy aggregates spam_pattern_check with
    other registered checks. The reason names get prefixed with
    the source check name in the aggregate."""
    from genlab_core.compliance.policy_gate import check_publish_policy

    bp = {"hashtags": "#f4f #l4l #followforfollow"}
    with patch("genlab_core.compliance.policy_gate.log_compliance_event"):
        result = check_publish_policy(bp, "instagram", "gaming")
    assert result.decision == "warn"
    assert any("spam_pattern_check:banned_hashtag_used" in r for r in result.reasons)
