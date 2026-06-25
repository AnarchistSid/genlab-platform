"""Daily compliance digest Slack message — PR after #584.

Pins for ``slack_notifier.send_compliance_digest``:

  * No-op when env GENLAB_COMPLIANCE_SLACK_WEBHOOK unset
  * POST issued when env set + at least one niche has activity
  * POST issued even when by_niche is empty (positive 'all clean'
    confirmation that monitoring is alive)
  * Strict 2s timeout reused from notify_compliance_block
  * Returns True on HTTP 2xx, False otherwise
  * Network exceptions never propagate (fail-OPEN)
  * Non-2xx → WARNING log

Pins for the digest payload shape:

  * Empty by_niche → ✅ 'all clean' single-section block
  * Non-empty → 📊 header + per-niche summary lines
  * Per-niche line includes warn/block counts + top_event_type
  * Sort order: blocks desc → warns desc (operator attention)
  * window_days reflected in headline ('24h' for 1d, 'Nd' otherwise)

Pins for the CLI ``compliance_digest_sender.main``:

  * --window-days propagates to stats_by_niche + send_digest
  * Exit 0 when env unset (silent no-op)
  * Exit 0 when send succeeds
  * Exit 1 when env set + send fails (operator-visible failure)
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

# ── send_compliance_digest — env gating ───────────────────────────


def test_digest_returns_false_when_env_unset(monkeypatch, caplog):
    from genlab_core.compliance.slack_notifier import send_compliance_digest

    monkeypatch.delenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", raising=False)
    with caplog.at_level(logging.DEBUG):
        result = send_compliance_digest({}, window_days=1)
    assert result is False
    assert any("digest skipped" in r.getMessage() for r in caplog.records)


# ── send_compliance_digest — POST behaviour ───────────────────────


def test_digest_posts_for_empty_by_niche_too(monkeypatch):
    """All-clean digest STILL sends — positive monitoring confirmation."""
    from genlab_core.compliance.slack_notifier import send_compliance_digest

    monkeypatch.setenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", "https://x")
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = send_compliance_digest({}, window_days=1)

    assert result is True
    mock_post.assert_called_once()


def test_digest_posts_with_active_niches(monkeypatch):
    from genlab_core.compliance.slack_notifier import send_compliance_digest

    monkeypatch.setenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", "https://x")
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    by_niche = {
        "gaming": {
            "warns": 5,
            "blocks": 1,
            "top_event_type": "spam_pattern_detected",
        },
    }
    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = send_compliance_digest(by_niche, window_days=1)

    assert result is True
    posted_payload = mock_post.call_args.kwargs["json"]
    # Mobile fallback should mention the activity counts
    assert "1 niche" in posted_payload["text"]
    assert "5w / 1b" in posted_payload["text"]


def test_digest_uses_2_second_timeout(monkeypatch):
    from genlab_core.compliance.slack_notifier import send_compliance_digest

    monkeypatch.setenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", "https://x")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("requests.post", return_value=mock_resp) as mock_post:
        send_compliance_digest({}, window_days=1)
    assert mock_post.call_args.kwargs["timeout"] == 2.0


def test_digest_returns_false_on_non_2xx(monkeypatch, caplog):
    from genlab_core.compliance.slack_notifier import send_compliance_digest

    monkeypatch.setenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", "https://x")
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    with patch("requests.post", return_value=mock_resp):
        with caplog.at_level(logging.WARNING):
            result = send_compliance_digest({}, window_days=1)
    assert result is False
    assert any("HTTP 500" in r.getMessage() for r in caplog.records)


def test_digest_returns_false_on_requests_exception(monkeypatch):
    from genlab_core.compliance.slack_notifier import send_compliance_digest

    monkeypatch.setenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", "https://x")
    with patch("requests.post", side_effect=RuntimeError("net down")):
        result = send_compliance_digest({}, window_days=1)
    assert result is False


# ── payload shape ─────────────────────────────────────────────────


def test_payload_empty_by_niche_renders_all_clean():
    from genlab_core.compliance.slack_notifier import _build_digest_payload

    payload = _build_digest_payload({}, window_days=1)
    # The message says "all niches clean" — match either word
    # form so future copy tweaks don't break the pin
    assert "clean" in payload["text"].lower()
    # Single ✅ section block, no header
    assert len(payload["blocks"]) == 1
    section_text = payload["blocks"][0]["text"]["text"]
    assert "✅" in section_text
    assert "24h" in section_text


def test_payload_active_renders_header_plus_summary():
    from genlab_core.compliance.slack_notifier import _build_digest_payload

    by_niche = {
        "gaming": {"warns": 3, "blocks": 0, "top_event_type": "spam"},
    }
    payload = _build_digest_payload(by_niche, window_days=1)
    # Header + section
    assert payload["blocks"][0]["type"] == "header"
    assert "📊" in payload["blocks"][0]["text"]["text"]
    section_text = payload["blocks"][1]["text"]["text"]
    # Per-niche line includes counts + top
    assert "`gaming`" in section_text
    assert "3w" in section_text
    assert "`spam`" in section_text


def test_payload_sorts_by_blocks_desc_then_warns_desc():
    """Operator attention: blocks weighted above warns."""
    from genlab_core.compliance.slack_notifier import _build_digest_payload

    by_niche = {
        "anime": {"warns": 10, "blocks": 0, "top_event_type": "x"},
        "gaming": {"warns": 1, "blocks": 2, "top_event_type": "y"},
        "movies": {"warns": 5, "blocks": 0, "top_event_type": "z"},
    }
    payload = _build_digest_payload(by_niche, window_days=1)
    section_text = payload["blocks"][1]["text"]["text"]

    # gaming (2 blocks) should appear before anime (0 blocks even
    # though 10 warns) — blocks dominate the sort
    gaming_idx = section_text.index("`gaming`")
    anime_idx = section_text.index("`anime`")
    movies_idx = section_text.index("`movies`")
    assert gaming_idx < anime_idx
    # Among 0-block niches, anime (10w) before movies (5w)
    assert anime_idx < movies_idx


def test_payload_window_label_24h_for_default():
    from genlab_core.compliance.slack_notifier import _build_digest_payload

    payload = _build_digest_payload({}, window_days=1)
    assert "24h" in payload["text"]


def test_payload_window_label_uses_days_for_non_1():
    from genlab_core.compliance.slack_notifier import _build_digest_payload

    payload = _build_digest_payload({}, window_days=7)
    assert "7d" in payload["text"]


def test_payload_handles_niche_without_top_event_type():
    """top_event_type=None (only allows) → no '· top:' suffix in line."""
    from genlab_core.compliance.slack_notifier import _build_digest_payload

    by_niche = {
        "gaming": {"warns": 0, "blocks": 0, "top_event_type": None},
    }
    payload = _build_digest_payload(by_niche, window_days=1)
    section_text = payload["blocks"][1]["text"]["text"]
    assert "`gaming`" in section_text
    assert "· top:" not in section_text


# ── CLI compliance_digest_sender.main ─────────────────────────────


def test_cli_propagates_window_days(monkeypatch):
    """--window-days N is passed to stats_by_niche AND send_digest."""
    from genlab_core.compliance.compliance_digest_sender import main

    monkeypatch.setenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", "https://x")
    with (
        patch("genlab_core.compliance.events.stats_by_niche", return_value={}) as mock_stats,
        patch(
            "genlab_core.compliance.slack_notifier.send_compliance_digest",
            return_value=True,
        ) as mock_send,
    ):
        rc = main(["--window-days", "7"])

    assert rc == 0
    mock_stats.assert_called_once_with(window_days=7)
    # The library function gets the same window_days for its
    # subject line
    assert mock_send.call_args.kwargs["window_days"] == 7


def test_cli_default_window_days_is_1():
    from genlab_core.compliance.compliance_digest_sender import main

    with (
        patch("genlab_core.compliance.events.stats_by_niche", return_value={}) as mock_stats,
        patch(
            "genlab_core.compliance.slack_notifier.send_compliance_digest",
            return_value=True,
        ),
    ):
        rc = main([])
    assert rc == 0
    mock_stats.assert_called_once_with(window_days=1)


def test_cli_exit_0_when_env_unset(monkeypatch):
    """No webhook configured → silent no-op exit 0 (normal operation
    for environments that haven't activated the digest yet)."""
    from genlab_core.compliance.compliance_digest_sender import main

    monkeypatch.delenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", raising=False)
    with (
        patch("genlab_core.compliance.events.stats_by_niche", return_value={}),
        patch(
            "genlab_core.compliance.slack_notifier.send_compliance_digest",
            return_value=False,
        ),
    ):
        rc = main([])
    assert rc == 0


def test_cli_exit_0_when_send_succeeds(monkeypatch):
    from genlab_core.compliance.compliance_digest_sender import main

    monkeypatch.setenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", "https://x")
    with (
        patch("genlab_core.compliance.events.stats_by_niche", return_value={}),
        patch(
            "genlab_core.compliance.slack_notifier.send_compliance_digest",
            return_value=True,
        ),
    ):
        rc = main([])
    assert rc == 0


def test_cli_exit_1_when_env_set_but_send_fails(monkeypatch):
    """Operator activated the digest but the POST failed →
    systemd sees exit 1, alert on the alert mechanism."""
    from genlab_core.compliance.compliance_digest_sender import main

    monkeypatch.setenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", "https://x")
    with (
        patch("genlab_core.compliance.events.stats_by_niche", return_value={}),
        patch(
            "genlab_core.compliance.slack_notifier.send_compliance_digest",
            return_value=False,
        ),
    ):
        rc = main([])
    assert rc == 1
