"""The alert webhook must know whether it actually delivered, and must not
page 48 times a day about one condition.

OPS-02 Step 2. Two defects in the path built to end silent failures:

  1. `requests.post(...)` returned a response that was never inspected, so a
     revoked or mistyped webhook (404/410) logged "Delivered" and returned
     True. Only a transport exception was ever noticed.
  2. No throttle. The health-monitor timer runs every 30 minutes and `notify()`
     receives the whole run's alerts, not just newly-inserted ones — so a
     persisting CRITICAL paged every 30 minutes. The six credit outages between
     2026-08-03 and 08-22 spanned up to 24h each; had the webhook been set they
     would have delivered ~250 messages saying the same thing.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.monitoring import health_monitor as hm
from genlab_core.monitoring.alerts import Alert


@pytest.fixture
def state(tmp_path, monkeypatch):
    p = tmp_path / "alert_notify_state.json"
    monkeypatch.setattr(hm, "_NOTIFY_STATE_PATH", str(p))
    monkeypatch.setenv("GENLAB_ALERT_WEBHOOK_URL", "https://hooks.example/T/B/X")
    return p


def _crit(check="anthropic_credit_exhausted", niche=None):
    return Alert(check=check, severity="critical", message="m", niche_id=niche)


def _resp(status, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


class TestDeliveryIsVerified:
    def test_2xx_counts_as_delivered(self, state):
        with patch("requests.post", return_value=_resp(200)) as post:
            assert hm.notify([_crit()]) is True
        assert post.called

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 500, 503])
    def test_non_2xx_is_a_failure_not_a_delivery(self, state, status, caplog):
        """The exact defect: a revoked Slack webhook 404s and this used to
        report success."""
        with caplog.at_level(logging.WARNING), patch(
            "requests.post", return_value=_resp(status, "no_service")
        ):
            assert hm.notify([_crit()]) is False
        assert str(status) in caplog.text
        assert "NOT" in caplog.text or "not delivered" in caplog.text.lower()

    def test_failed_delivery_does_not_record_state(self, state):
        """A 404 must not mark the alert as notified, or the throttle would
        suppress the retry too."""
        with patch("requests.post", return_value=_resp(404)):
            hm.notify([_crit()])
        assert not state.exists(), "a failed delivery recorded throttle state"

    def test_transport_exception_still_handled(self, state, caplog):
        with caplog.at_level(logging.WARNING), patch(
            "requests.post", side_effect=OSError("dns")
        ):
            assert hm.notify([_crit()]) is False
        assert "failed" in caplog.text.lower()


class TestThrottle:
    def test_same_set_is_not_re_notified(self, state):
        with patch("requests.post", return_value=_resp(200)) as post:
            assert hm.notify([_crit()]) is True
            assert hm.notify([_crit()]) is False      # throttled
            assert hm.notify([_crit()]) is False
        assert post.call_count == 1, "a persisting condition paged more than once"

    def test_changed_set_pages_again(self, state):
        """A NEW critical must break through the throttle — that is the whole
        point of fingerprinting the set rather than time-boxing blindly."""
        with patch("requests.post", return_value=_resp(200)) as post:
            hm.notify([_crit()])
            hm.notify([_crit(), _crit("service_down")])
        assert post.call_count == 2

    def test_throttle_expires(self, state):
        import time

        with patch("requests.post", return_value=_resp(200)) as post:
            hm.notify([_crit()])
            payload = json.loads(state.read_text())
            payload["ts"] = time.time() - (hm._NOTIFY_REMINDER_SECONDS + 60)
            state.write_text(json.dumps(payload))
            hm.notify([_crit()])
        assert post.call_count == 2, "the reminder never fired"

    def test_throttle_fails_open(self, state, monkeypatch, caplog):
        """A broken throttle must page, never suppress."""
        monkeypatch.setattr(hm, "_NOTIFY_STATE_PATH", "/proc/nonexistent/x.json")
        with caplog.at_level(logging.WARNING), patch(
            "requests.post", return_value=_resp(200)
        ) as post:
            assert hm.notify([_crit()]) is True
        assert post.called, "an unreadable throttle state suppressed the page"


class TestSeverityRouting:
    def test_warning_alerts_do_not_page(self, state):
        with patch("requests.post") as post:
            assert hm.notify([Alert(check="zero_blueprints", severity="warning",
                                    message="m")]) is False
        assert not post.called

    def test_no_url_is_a_noop(self, state, monkeypatch):
        monkeypatch.setenv("GENLAB_ALERT_WEBHOOK_URL", "")
        with patch("requests.post") as post:
            assert hm.notify([_crit()]) is False
        assert not post.called
