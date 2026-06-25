"""PR #567 (2026-06-25) — apply_ai_disclosure helper + publish wire.

Pins:

  * Idempotent: disclosure already in caption → unchanged (no log)
  * No-platform-text: empty disclosure → unchanged (no log)
  * Empty caption → unchanged
  * Per-platform budget: twitter 280-char cap truncates caption to
    make room for disclosure suffix
  * Disclosure exceeds budget alone → fail-safe return unchanged
  * Successful append logs compliance_events.ai_disclosure_added
"""

from __future__ import annotations

from unittest.mock import patch

# ── apply_ai_disclosure happy path ─────────────────────────────────


def test_appends_disclosure_when_absent():
    from genlab_core.compliance.disclosure import apply_ai_disclosure

    with patch("genlab_core.compliance.events.log_compliance_event"):
        out = apply_ai_disclosure("Check out my new reel!", "instagram", niche_id="gaming")
    assert "Check out my new reel!" in out
    assert "AI-assisted" in out
    assert "#ai" in out


def test_idempotent_when_disclosure_already_present():
    """Re-publishing a blueprint must NOT double-append."""
    from genlab_core.compliance.disclosure import apply_ai_disclosure

    caption_with_disclosure = "My post AI-assisted post. #ai"
    with patch("genlab_core.compliance.events.log_compliance_event") as mock_log:
        out = apply_ai_disclosure(caption_with_disclosure, "instagram", niche_id="gaming")
    assert out == caption_with_disclosure
    # No log because nothing changed — idempotent operations don't
    # write audit rows.
    mock_log.assert_not_called()


def test_empty_caption_returns_unchanged():
    from genlab_core.compliance.disclosure import apply_ai_disclosure

    assert apply_ai_disclosure("", "instagram", niche_id="gaming") == ""


def test_empty_platform_returns_unchanged():
    from genlab_core.compliance.disclosure import apply_ai_disclosure

    out = apply_ai_disclosure("hello", "", niche_id="gaming")
    assert out == "hello"


def test_unknown_platform_returns_unchanged_no_log():
    """No disclosure for unknown platform → no log."""
    from genlab_core.compliance.disclosure import apply_ai_disclosure

    with patch("genlab_core.compliance.events.log_compliance_event") as mock_log:
        out = apply_ai_disclosure("hello", "myspace", niche_id="gaming")
    assert out == "hello"
    mock_log.assert_not_called()


# ── per-platform budget enforcement ────────────────────────────────


def test_twitter_budget_truncates_caption_to_make_room():
    """Twitter 280-char cap. apply_ai_disclosure must truncate the
    ORIGINAL caption so the FINAL string (caption + ' ' + disclosure)
    fits in 280. Without this, the [:280] truncation in twitter's
    build_platform_specific would cut off the disclosure half-way."""
    from genlab_core.compliance.disclosure import (
        PLATFORM_CHAR_BUDGETS,
        apply_ai_disclosure,
        generate_ai_disclosure,
    )

    disclosure = generate_ai_disclosure("twitter")
    suffix_len = len(disclosure) + 1  # " " prefix
    budget = PLATFORM_CHAR_BUDGETS["twitter"]

    # 280-char caption — at the budget. After adding disclosure it
    # would overflow → caption must be truncated.
    long = "x" * budget
    with patch("genlab_core.compliance.events.log_compliance_event"):
        out = apply_ai_disclosure(long, "twitter", niche_id="gaming")
    assert len(out) <= budget
    assert out.endswith(disclosure)
    # Caption portion is truncated to budget - suffix_len chars
    expected_caption_len = budget - suffix_len
    assert out.startswith("x" * expected_caption_len)


def test_disclosure_alone_exceeds_budget_fail_safe(caplog):
    """If the disclosure itself can't fit (hypothetical for twitter,
    real if budget gets shrunk), the helper returns the caption
    unchanged and logs a WARNING. Better than shipping a broken
    truncated disclosure."""
    import logging

    from genlab_core.compliance import disclosure as disc_mod

    # Force the budget to be smaller than any disclosure text
    with patch.dict(disc_mod.PLATFORM_CHAR_BUDGETS, {"twitter": 5}):
        with caplog.at_level(logging.WARNING):
            out = disc_mod.apply_ai_disclosure("hello", "twitter", niche_id="gaming")
    assert out == "hello"  # unchanged
    # Use getMessage() to format %d into the actual string
    assert any("exceeds budget" in r.getMessage() for r in caplog.records)


def test_instagram_no_budget_just_appends():
    """Platforms without char caps (IG/FB/YT/TT) just append —
    no truncation logic fires."""
    from genlab_core.compliance.disclosure import apply_ai_disclosure

    long = "x" * 2000  # well under IG's 2200 cap
    with patch("genlab_core.compliance.events.log_compliance_event"):
        out = apply_ai_disclosure(long, "instagram", niche_id="gaming")
    assert out.startswith("x" * 2000)  # original preserved entirely
    assert "AI-assisted" in out


# ── compliance_events logging ──────────────────────────────────────


def test_successful_append_logs_compliance_event():
    """Every actual append writes one ai_disclosure_added row to
    compliance_events. Pin the event_type, decision, platform, +
    niche_id bound params."""
    from genlab_core.compliance.disclosure import apply_ai_disclosure

    with patch("genlab_core.compliance.events.log_compliance_event") as mock_log:
        apply_ai_disclosure("hello", "instagram", niche_id="gaming", blueprint_id="bp-1")
    assert mock_log.call_count == 1
    kwargs = mock_log.call_args.kwargs
    assert kwargs["niche_id"] == "gaming"
    assert kwargs["event_type"] == "ai_disclosure_added"
    assert kwargs["decision"] == "allow"
    assert kwargs["platform"] == "instagram"
    assert kwargs["blueprint_id"] == "bp-1"
    assert "caption_disclosure_appended" in kwargs["reasons"]


def test_log_failure_does_not_propagate():
    """compliance_events.log_compliance_event already fails-OPEN.
    Pin that even if it raised (it doesn't, but defense in depth),
    the caller still gets the disclosed caption back."""
    from genlab_core.compliance.disclosure import apply_ai_disclosure

    with patch(
        "genlab_core.compliance.events.log_compliance_event",
        side_effect=RuntimeError("simulated logging blowup"),
    ):
        # Must not raise; caller gets the disclosed caption
        out = apply_ai_disclosure("hello", "instagram", niche_id="gaming")
    assert "AI-assisted" in out


def test_niche_id_defaults_to_all_when_empty():
    """If caller omits niche_id, the log row uses 'all' so the
    audit row still lands (rather than being rejected by the
    empty-niche guard in log_compliance_event)."""
    from genlab_core.compliance.disclosure import apply_ai_disclosure

    with patch("genlab_core.compliance.events.log_compliance_event") as mock_log:
        apply_ai_disclosure("hello", "instagram", niche_id="")
    assert mock_log.call_args.kwargs["niche_id"] == "all"


# ── publish wire integration ───────────────────────────────────────


def _minimal_fields(**overrides):
    """Minimal SharePoint-shape fields dict for build_payload."""
    base = {
        "id": "bp-test-1",
        "niche_id": "gaming",
        "caption": "Test post caption.",
        "hashtags": "#gaming #test",
        "hook": "Hook text",
        "visual_paths": '["/tmp/nonexistent.mp4"]',
        "format": "reel",
        "media_type": "video",
    }
    base.update(overrides)
    return base


def test_build_payload_applies_disclosure_to_caption(monkeypatch):
    """End-to-end: build_payload runs apply_ai_disclosure on the
    final caption. Verified by checking the returned PublishPayload.

    Stubs visual-paths validation so the test doesn't need a real
    file. transcode_for_platform is stubbed to pass through."""
    from pathlib import Path

    import genlab_core.publishing.payload_builder as pb_mod

    # Stub the path existence + size checks
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "stat", lambda self: type("S", (), {"st_size": 1_000_000})())
    monkeypatch.setattr(pb_mod, "transcode_for_platform", lambda p, _: p)

    fields = _minimal_fields(caption="Original caption text")
    with patch("genlab_core.compliance.events.log_compliance_event"):
        payload = pb_mod.build_payload(fields, "instagram")

    # The disclosure suffix should be in the final caption
    assert "Original caption text" in payload.caption
    assert "AI-assisted" in payload.caption


def test_build_payload_twitter_respects_280_budget(monkeypatch):
    """Twitter's tweet_text gets disclosure applied AT or BELOW the
    280-char cap. Pin so a future refactor doesn't break the budget
    by applying the disclosure AFTER the [:280] cut."""
    from pathlib import Path

    import genlab_core.publishing.payload_builder as pb_mod

    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "stat", lambda self: type("S", (), {"st_size": 1_000_000})())
    monkeypatch.setattr(pb_mod, "transcode_for_platform", lambda p, _: p)

    # 280-char caption — at the budget. Disclosure would overflow.
    long_caption = "y" * 280
    fields = _minimal_fields(caption=long_caption)
    with patch("genlab_core.compliance.events.log_compliance_event"):
        payload = pb_mod.build_payload(fields, "twitter")

    tweet_text = payload.platform_specific.tweet_text
    assert len(tweet_text) <= 280
    assert "AI-assisted" in tweet_text or tweet_text.endswith("#ai")


def test_build_payload_idempotent_against_double_disclosure(monkeypatch):
    """If a blueprint's caption already contains the disclosure
    (e.g. operator-edited), build_payload must NOT double-append."""
    from pathlib import Path

    import genlab_core.publishing.payload_builder as pb_mod

    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "stat", lambda self: type("S", (), {"st_size": 1_000_000})())
    monkeypatch.setattr(pb_mod, "transcode_for_platform", lambda p, _: p)

    fields = _minimal_fields(caption="Original caption text AI-assisted post. #ai")
    with patch("genlab_core.compliance.events.log_compliance_event"):
        payload = pb_mod.build_payload(fields, "instagram")

    # Count occurrences of #ai — should be exactly 1
    assert payload.caption.count("#ai") == 1
