"""R-01: health_monitor must DELIVER critical alerts (not just write a table).

notify() POSTs critical alerts to GENLAB_ALERT_WEBHOOK_URL (Slack-compatible);
no-op when the URL is unset or there are no critical alerts; best-effort.
"""

from __future__ import annotations

from unittest.mock import patch

from genlab_core.monitoring.health_monitor import Alert, notify


def _crit(msg: str = "boom") -> Alert:
    return Alert(check="zero_blueprints", severity="critical", message=msg, niche_id="gaming")


def test_notify_no_url_is_noop(monkeypatch):
    monkeypatch.delenv("GENLAB_ALERT_WEBHOOK_URL", raising=False)
    with patch("requests.post") as post:
        assert notify([_crit()]) is False
        post.assert_not_called()


def test_notify_no_critical_is_noop(monkeypatch):
    monkeypatch.setenv("GENLAB_ALERT_WEBHOOK_URL", "https://hooks.slack.test/x")
    warning = Alert(check="x", severity="warning", message="m")
    with patch("requests.post") as post:
        assert notify([warning]) is False
        post.assert_not_called()


def test_notify_posts_critical(monkeypatch):
    monkeypatch.setenv("GENLAB_ALERT_WEBHOOK_URL", "https://hooks.slack.test/x")
    with patch("requests.post") as post:
        assert notify([_crit("channel dark")]) is True
        post.assert_called_once()
        assert "channel dark" in post.call_args.kwargs["json"]["text"]


def test_notify_delivery_failure_is_swallowed(monkeypatch):
    monkeypatch.setenv("GENLAB_ALERT_WEBHOOK_URL", "https://hooks.slack.test/x")
    with patch("requests.post", side_effect=RuntimeError("network down")):
        assert notify([_crit()]) is False  # best-effort, never raises
