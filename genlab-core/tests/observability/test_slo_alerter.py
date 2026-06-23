"""Tests for the SLO-violation webhook sink (PR #505).

Pins:
  - opt-in via env var (unset → no-op, no network call)
  - severity routing (failed → critical, partial+violations → warning,
    success → no alert)
  - payload shape (Slack-compatible ``text`` + structured fields)
  - fail-open posture (network error / non-2xx → False, never raises)
  - integration: RunReport calls the alerter after writing the report

Webhook behaviour is heavily mocked — we never hit real Slack/Discord
in CI. The tests pin the CALL shape, not the destination's response.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.observability.slo_alerter import (
    _build_payload,
    _severity_for_status,
    fire_slo_alert,
)

# ── _severity_for_status ─────────────────────────────────────────────


def test_severity_failed_with_violations_returns_critical():
    assert _severity_for_status("failed", ["zero blueprints"]) == "critical"


def test_severity_partial_with_violations_returns_warning():
    assert _severity_for_status("partial", ["whisper captions failed"]) == "warning"


def test_severity_no_violations_returns_none():
    """Clean runs stay silent — no operator notification needed."""
    assert _severity_for_status("success", []) is None
    assert _severity_for_status("failed", []) is None  # defensive — no message → no alert


def test_severity_success_with_violations_returns_none():
    """Logically impossible (run_report promotes on violations) but
    defensive — no alert fires."""
    assert _severity_for_status("success", ["something"]) is None


# ── _build_payload ───────────────────────────────────────────────────


def test_payload_includes_slack_compatible_text_field():
    report = {
        "niche_id": "gaming",
        "run_id": "gaming_20260623_153619",
        "status": "failed",
        "metrics": {"blueprints_count": 0},
        "slo_violations": ["Zero blueprints produced from 4 stories"],
        "bottleneck_stage": "video_gate",
        "bottleneck_reason": "all 5/5 stories dropped for missing clips",
    }
    payload = _build_payload(report, "critical")
    # Slack incoming-webhook reads ``text`` as the message body.
    assert "text" in payload
    assert "critical" in payload["text"]
    assert "gaming" in payload["text"]
    assert "gaming_20260623_153619" in payload["text"]


def test_payload_includes_structured_fields_for_custom_consumers():
    report = {
        "niche_id": "sports",
        "run_id": "r",
        "status": "partial",
        "metrics": {"blueprints_count": 3},
        "slo_violations": ["v1", "v2"],
        "bottleneck_stage": None,
        "bottleneck_reason": None,
    }
    payload = _build_payload(report, "warning")
    assert payload["severity"] == "warning"
    assert payload["niche_id"] == "sports"
    assert payload["status"] == "partial"
    assert payload["blueprints_count"] == 3
    assert payload["slo_violations"] == ["v1", "v2"]
    # PR #504 bottleneck fields propagate
    assert payload["bottleneck_stage"] is None
    assert payload["bottleneck_reason"] is None


def test_payload_propagates_bottleneck_fields_when_set():
    report = {
        "niche_id": "x",
        "run_id": "r",
        "status": "failed",
        "metrics": {"blueprints_count": 0},
        "slo_violations": ["z"],
        "bottleneck_stage": "pre_download_dedup",
        "bottleneck_reason": "all 5 stories deduped",
    }
    payload = _build_payload(report, "critical")
    assert payload["bottleneck_stage"] == "pre_download_dedup"
    assert payload["bottleneck_reason"] == "all 5 stories deduped"


# ── fire_slo_alert (integration with env + requests) ─────────────────


def test_fire_no_op_when_env_unset(monkeypatch):
    """Default: no env → no network call → returns False."""
    monkeypatch.delenv("GENLAB_SLO_ALERT_WEBHOOK", raising=False)
    with patch("genlab_core.observability.slo_alerter.requests.post") as mock_post:
        ok = fire_slo_alert(
            {
                "niche_id": "g",
                "run_id": "r",
                "status": "failed",
                "slo_violations": ["zero blueprints"],
            }
        )
    assert ok is False
    mock_post.assert_not_called()


def test_fire_no_op_when_no_violations(monkeypatch):
    """Webhook configured but no SLO violations → no POST."""
    monkeypatch.setenv("GENLAB_SLO_ALERT_WEBHOOK", "https://example.com/hook")
    with patch("genlab_core.observability.slo_alerter.requests.post") as mock_post:
        ok = fire_slo_alert(
            {
                "niche_id": "g",
                "run_id": "r",
                "status": "success",
                "slo_violations": [],
            }
        )
    assert ok is False
    mock_post.assert_not_called()


def test_fire_posts_when_violations_present(monkeypatch):
    """Real path: env set + status=failed + violations → POST fires."""
    monkeypatch.setenv("GENLAB_SLO_ALERT_WEBHOOK", "https://example.com/hook")
    mock_resp = MagicMock(status_code=200)
    with patch(
        "genlab_core.observability.slo_alerter.requests.post", return_value=mock_resp
    ) as mock_post:
        ok = fire_slo_alert(
            {
                "niche_id": "gaming",
                "run_id": "r1",
                "status": "failed",
                "metrics": {"blueprints_count": 0},
                "slo_violations": ["Zero blueprints"],
                "bottleneck_stage": "video_gate",
                "bottleneck_reason": "all stories clipless",
            }
        )
    assert ok is True
    mock_post.assert_called_once()
    call = mock_post.call_args
    # URL was the env var
    assert call.args[0] == "https://example.com/hook"
    # Payload shape: text + severity present
    assert "text" in call.kwargs["json"]
    assert call.kwargs["json"]["severity"] == "critical"
    # Timeout passed
    assert call.kwargs["timeout"] == 5.0


def test_fire_fail_open_on_network_error(monkeypatch):
    """Webhook URL unreachable → returns False, does NOT raise."""
    monkeypatch.setenv("GENLAB_SLO_ALERT_WEBHOOK", "https://example.com/hook")
    with patch(
        "genlab_core.observability.slo_alerter.requests.post",
        side_effect=Exception("connection refused"),
    ):
        # Must not raise
        ok = fire_slo_alert(
            {
                "niche_id": "x",
                "run_id": "r",
                "status": "failed",
                "slo_violations": ["zero blueprints"],
            }
        )
    assert ok is False


def test_fire_fail_open_on_non_2xx(monkeypatch):
    """Webhook returns 500 → returns False, no exception."""
    monkeypatch.setenv("GENLAB_SLO_ALERT_WEBHOOK", "https://example.com/hook")
    mock_resp = MagicMock(status_code=500, text="server error")
    with patch("genlab_core.observability.slo_alerter.requests.post", return_value=mock_resp):
        ok = fire_slo_alert(
            {
                "niche_id": "x",
                "run_id": "r",
                "status": "failed",
                "slo_violations": ["zero blueprints"],
            }
        )
    assert ok is False


def test_fire_partial_status_routes_to_warning_severity(monkeypatch):
    """status=partial + violations → severity=warning (not critical)."""
    monkeypatch.setenv("GENLAB_SLO_ALERT_WEBHOOK", "https://example.com/hook")
    mock_resp = MagicMock(status_code=204)  # 204 No Content is valid 2xx
    with patch(
        "genlab_core.observability.slo_alerter.requests.post", return_value=mock_resp
    ) as mock_post:
        fire_slo_alert(
            {
                "niche_id": "ai_creators",
                "run_id": "ai_creators_20260623_001",
                "status": "partial",
                "metrics": {"blueprints_count": 2},
                "slo_violations": ["whisper captions: 2 failed"],
            }
        )
    payload = mock_post.call_args.kwargs["json"]
    assert payload["severity"] == "warning"
    assert "warning" in payload["text"]


# ── integration: RunReport wires the alerter ─────────────────────────


def test_run_report_calls_slo_alerter_after_report_write(monkeypatch):
    """End-to-end pin: RunReport invokes fire_slo_alert(report).

    Patches fire_slo_alert at the SOURCE module (per the test-pattern
    lesson in CLAUDE.md — lazy imports look up bindings in the source
    module's namespace at call time).
    """
    monkeypatch.setenv("GENLAB_SLO_ALERT_WEBHOOK", "https://example.com/hook")
    from genlab_core.pipeline.stages.run_report import RunReport

    ctx = {
        "niche_id": "ai_creators",
        "niche_config": {},
        "stories": [],
        "run_stats": {
            "backlog_push": {"blueprints_pushed": 0},
            "errors": [],
            "_stage_timings": {"_": 1.0},
        },
    }

    with patch("genlab_core.observability.slo_alerter.fire_slo_alert") as mock_fire:
        mock_fire.return_value = True
        RunReport().execute(ctx)

    mock_fire.assert_called_once()
    report_arg = mock_fire.call_args.args[0]
    assert report_arg["niche_id"] == "ai_creators"
    assert report_arg["status"] == "failed"  # 0 blueprints = always failed


def test_run_report_continues_when_alerter_raises(monkeypatch):
    """Defensive: even if fire_slo_alert itself somehow raises (e.g.
    import-time error), RunReport must NOT crash. The wider pipeline
    has already finished — failing to alert must not poison the
    run_report write or downstream stages.
    """
    monkeypatch.setenv("GENLAB_SLO_ALERT_WEBHOOK", "https://example.com/hook")
    from genlab_core.pipeline.stages.run_report import RunReport

    ctx = {
        "niche_id": "x",
        "niche_config": {},
        "stories": [],
        "run_stats": {
            "backlog_push": {"blueprints_pushed": 0},
            "errors": [],
            "_stage_timings": {"_": 1.0},
        },
    }

    with patch(
        "genlab_core.observability.slo_alerter.fire_slo_alert",
        side_effect=RuntimeError("synthetic alerter explosion"),
    ):
        # Must not raise
        result = RunReport().execute(ctx)

    # Run report itself still completed and was written
    assert "report" in result["run_stats"]


@pytest.fixture(autouse=True)
def _clear_webhook_env(monkeypatch):
    """Default to env unset for every test — prevents accidental leaks
    between tests if a test forgets to set it."""
    monkeypatch.delenv("GENLAB_SLO_ALERT_WEBHOOK", raising=False)
    yield
