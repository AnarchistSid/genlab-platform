"""Slack webhook notifier for compliance block decisions —
PR after #583.

Pins for ``compliance.slack_notifier``:

  * No-op when GENLAB_COMPLIANCE_SLACK_WEBHOOK unset (default-off)
  * No-op when env value is empty / whitespace
  * POST issued with the env-supplied URL when set
  * Strict timeout=2.0 seconds (defensive against Slack edge stalls)
  * Returns True on HTTP 2xx, False otherwise
  * Network exceptions never propagate (fail-OPEN)
  * Non-2xx response → WARNING log + False
  * Payload shape contains both `text` fallback + structured `blocks`
  * Reasons + platform + blueprint_id all surface in the payload
  * Long metadata blob truncated to fit Slack's per-block char limit

Pins for the log_compliance_event integration:

  * decision='block' → notify_compliance_block called AFTER DB write
  * decision='warn' → notify_compliance_block NOT called
  * decision='allow' → notify_compliance_block NOT called
  * Notifier exception swallowed; log_compliance_event still True
  * DB write failure → notifier NOT called (never alert on un-logged
    decisions)
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import MagicMock, patch


def _fake_db_conn():
    """Standard fake connection used by the events.py test pattern."""
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    return fake_conn


# ── notify_compliance_block — env gating ──────────────────────────


def test_notify_returns_false_when_env_unset(monkeypatch, caplog):
    from genlab_core.compliance.slack_notifier import notify_compliance_block

    monkeypatch.delenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", raising=False)
    with caplog.at_level(logging.DEBUG):
        result = notify_compliance_block(
            niche_id="gaming",
            event_type="spam_pattern_detected",
        )
    assert result is False
    # DEBUG log surfaces the "would have sent" message for forensic
    # context — operator can verify the function FIRED without Slack
    # being configured yet
    assert any("GENLAB_COMPLIANCE_SLACK_WEBHOOK unset" in r.getMessage() for r in caplog.records)


def test_notify_returns_false_when_env_empty_or_whitespace(monkeypatch):
    from genlab_core.compliance.slack_notifier import notify_compliance_block

    for value in ["", " ", "   "]:
        monkeypatch.setenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", value)
        assert notify_compliance_block("gaming", "spam_pattern_detected") is False


# ── notify_compliance_block — POST behaviour ──────────────────────


def test_notify_posts_to_webhook_url_when_set(monkeypatch):
    from genlab_core.compliance.slack_notifier import notify_compliance_block

    monkeypatch.setenv(
        "GENLAB_COMPLIANCE_SLACK_WEBHOOK",
        "https://hooks.slack.com/services/T/B/X",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = notify_compliance_block(
            niche_id="gaming",
            event_type="spam_pattern_detected",
            reasons=["caption_n_gram_overlap_above_70%"],
        )

    assert result is True
    mock_post.assert_called_once()
    call = mock_post.call_args
    assert call.args[0] == "https://hooks.slack.com/services/T/B/X"


def test_notify_uses_strict_2_second_timeout(monkeypatch):
    """Defensive against Slack edge stalls — strict 2s ceiling."""
    from genlab_core.compliance.slack_notifier import notify_compliance_block

    monkeypatch.setenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", "https://x")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("requests.post", return_value=mock_resp) as mock_post:
        notify_compliance_block("gaming", "spam_pattern_detected")

    assert mock_post.call_args.kwargs["timeout"] == 2.0


def test_notify_returns_false_on_non_2xx(monkeypatch, caplog):
    """4xx / 5xx → False + WARN log with status (operator clue to
    fix a revoked or malformed webhook URL)."""
    from genlab_core.compliance.slack_notifier import notify_compliance_block

    monkeypatch.setenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", "https://x")

    mock_resp = MagicMock()
    mock_resp.status_code = 404  # Slack returns 404 on revoked webhooks
    with patch("requests.post", return_value=mock_resp):
        with caplog.at_level(logging.WARNING):
            result = notify_compliance_block("gaming", "spam_pattern_detected")

    assert result is False
    assert any("HTTP 404" in r.getMessage() for r in caplog.records)


def test_notify_returns_false_on_requests_exception(monkeypatch, caplog):
    """Network errors / timeout / DNS → False, never raises."""
    from genlab_core.compliance.slack_notifier import notify_compliance_block

    monkeypatch.setenv("GENLAB_COMPLIANCE_SLACK_WEBHOOK", "https://x")

    with patch("requests.post", side_effect=RuntimeError("connection reset")):
        with caplog.at_level(logging.WARNING):
            result = notify_compliance_block("gaming", "spam_pattern_detected")

    assert result is False
    assert any("POST failed" in r.getMessage() for r in caplog.records)


# ── payload shape ─────────────────────────────────────────────────


def test_payload_contains_text_fallback_for_mobile():
    """Mobile notification preview uses the `text` field. Must
    contain niche + event_type + reasons summary."""
    from genlab_core.compliance.slack_notifier import _build_slack_payload

    payload = _build_slack_payload(
        niche_id="gaming",
        event_type="spam_pattern_detected",
        blueprint_id=None,
        platform=None,
        reasons=["caption_n_gram_overlap_above_70%"],
        metadata=None,
    )
    assert "gaming" in payload["text"]
    assert "spam_pattern_detected" in payload["text"]
    assert "caption_n_gram_overlap" in payload["text"]


def test_payload_contains_structured_blocks():
    from genlab_core.compliance.slack_notifier import _build_slack_payload

    payload = _build_slack_payload(
        niche_id="gaming",
        event_type="spam_pattern_detected",
        blueprint_id=None,
        platform=None,
        reasons=["reason_one"],
        metadata=None,
    )
    blocks = payload["blocks"]
    types = [b["type"] for b in blocks]
    assert "header" in types
    assert types.count("section") >= 2  # fields section + reasons section


def test_payload_includes_platform_and_blueprint_when_present():
    from genlab_core.compliance.slack_notifier import _build_slack_payload

    bp_id = uuid.uuid4()
    payload = _build_slack_payload(
        niche_id="gaming",
        event_type="spam_pattern_detected",
        blueprint_id=bp_id,
        platform="instagram",
        reasons=[],
        metadata=None,
    )
    # The fields section should include both
    all_text = str(payload)
    assert "instagram" in all_text
    assert str(bp_id) in all_text


def test_payload_truncates_long_metadata_to_fit_slack_block_limit():
    """Slack imposes a 3000-char-per-block limit. The notifier
    truncates metadata that would exceed it (with a `…(truncated)`
    marker so operators know to dig into the audit log for full
    detail)."""
    from genlab_core.compliance.slack_notifier import _build_slack_payload

    huge_metadata = {"key": "x" * 10000}
    payload = _build_slack_payload(
        niche_id="gaming",
        event_type="spam_pattern_detected",
        blueprint_id=None,
        platform=None,
        reasons=[],
        metadata=huge_metadata,
    )
    # The metadata-section text should be truncated
    metadata_section = next(
        b
        for b in payload["blocks"]
        if b["type"] == "section" and "Metadata" in str(b.get("text", {}))
    )
    section_text = metadata_section["text"]["text"]
    assert "…(truncated)" in section_text
    # And shorter than the raw blob
    assert len(section_text) < len(str(huge_metadata)) + 100


# ── log_compliance_event integration ──────────────────────────────


def test_log_block_decision_fires_slack_notifier(monkeypatch):
    """When decision='block' and DB write succeeds → notifier called."""
    from genlab_core.compliance import events

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _fake_db_conn()

    with (
        patch.object(events, "_connect", return_value=fake_conn),
        patch(
            "genlab_core.compliance.slack_notifier.notify_compliance_block",
        ) as mock_notify,
    ):
        result = events.log_compliance_event(
            niche_id="gaming",
            event_type="auto_publish_block",
            decision="block",
            reasons=["reason_x"],
        )

    assert result is True
    mock_notify.assert_called_once()
    kwargs = mock_notify.call_args.kwargs
    assert kwargs["niche_id"] == "gaming"
    assert kwargs["event_type"] == "auto_publish_block"
    assert kwargs["reasons"] == ["reason_x"]


def test_log_warn_decision_does_NOT_fire_slack(monkeypatch):
    """warn decisions don't fire Slack — observation-only events
    shouldn't page the operator."""
    from genlab_core.compliance import events

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _fake_db_conn()

    with (
        patch.object(events, "_connect", return_value=fake_conn),
        patch(
            "genlab_core.compliance.slack_notifier.notify_compliance_block",
        ) as mock_notify,
    ):
        events.log_compliance_event(
            niche_id="gaming",
            event_type="spam_pattern_detected",
            decision="warn",
        )

    mock_notify.assert_not_called()


def test_log_allow_decision_does_NOT_fire_slack(monkeypatch):
    from genlab_core.compliance import events

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _fake_db_conn()

    with (
        patch.object(events, "_connect", return_value=fake_conn),
        patch(
            "genlab_core.compliance.slack_notifier.notify_compliance_block",
        ) as mock_notify,
    ):
        events.log_compliance_event(
            niche_id="gaming",
            event_type="pre_publish_check",
            decision="allow",
        )

    mock_notify.assert_not_called()


def test_log_notifier_exception_swallowed_and_log_still_succeeds(monkeypatch, caplog):
    """A Slack outage / exception during notify must NOT change the
    log_compliance_event return value. DB write is the source of
    truth."""
    from genlab_core.compliance import events

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = _fake_db_conn()

    with (
        patch.object(events, "_connect", return_value=fake_conn),
        patch(
            "genlab_core.compliance.slack_notifier.notify_compliance_block",
            side_effect=RuntimeError("simulated Slack outage"),
        ),
    ):
        with caplog.at_level(logging.WARNING):
            result = events.log_compliance_event(
                niche_id="gaming",
                event_type="auto_publish_block",
                decision="block",
            )

    assert result is True
    # The dispatch-failure WARNING should be in the log
    assert any("slack notify dispatch failed" in r.getMessage() for r in caplog.records)


def test_log_db_write_failure_does_NOT_fire_slack(monkeypatch):
    """If the DB write fails, NEVER fire Slack — alerting on a
    decision we couldn't persist would create phantom block records
    in operator slack with no audit-log row to match."""
    from genlab_core.compliance import events

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    # Fake connection where execute() raises
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated DB error")

    with (
        patch.object(events, "_connect", return_value=fake_conn),
        patch(
            "genlab_core.compliance.slack_notifier.notify_compliance_block",
        ) as mock_notify,
    ):
        result = events.log_compliance_event(
            niche_id="gaming",
            event_type="auto_publish_block",
            decision="block",
        )

    assert result is False  # DB write failed
    mock_notify.assert_not_called()  # no phantom alert
