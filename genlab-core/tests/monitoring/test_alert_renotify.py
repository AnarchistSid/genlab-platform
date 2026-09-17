"""An unresolved CRITICAL must keep paging, or an outage looks resolved.

Measured 2026-09-17: `anthropic_credit_exhausted` was created 07:00:15 and
notified once at 07:05:29. Twelve hours later every LLM tier was down and
approvals were at zero on every niche -- and the operator had received exactly
one message, that morning. The check fired, the webhook worked, the row was
written. Dedup then silenced a day-long outage, and the escalation at 12:30
(belt session expiring on top of Anthropic being unfunded) was swallowed too.
"""

import importlib.util
import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "notify_critical_alerts.py"
_spec = importlib.util.spec_from_file_location("notify_critical_alerts", _SRC)
notifier = importlib.util.module_from_spec(_spec)
sys.modules["notify_critical_alerts"] = notifier
_spec.loader.exec_module(notifier)


def test_a_renotify_cadence_exists_and_is_hours_not_never():
    assert 1 <= notifier.RENOTIFY_AFTER_HOURS <= 24


def test_the_select_no_longer_requires_notified_at_to_be_null():
    """The old query could only ever send once per alert row."""
    src = _SRC.read_text()
    sel = src[src.index("def fetch_pending"):src.index("def post_to_webhook")]
    assert "notified_at IS NULL" in sel, "still sends never-notified alerts"
    assert "notified_at <" in sel, (
        "no re-notification clause: an unresolved CRITICAL would page once and "
        "then go silent forever")


def test_mark_notified_refreshes_the_timestamp():
    """With `AND notified_at IS NULL` the UPDATE pins the first send time and
    every later reminder becomes a no-op."""
    src = _SRC.read_text()
    upd = src[src.index("def mark_notified"):]
    upd = upd[:upd.index("conn.commit()")]
    assert "SET notified_at = NOW()" in upd
    assert "notified_at IS NULL" not in upd, (
        "the guard pins notified_at at the first send")


def test_a_reminder_announces_itself_with_the_age():
    alert = {"message": "Anthropic credit exhausted", "is_reminder": True,
             "age_hours": 12.3}
    out = notifier._decorate(alert)
    assert "STILL UNRESOLVED" in out["message"] and "12.3h" in out["message"]
    assert "Anthropic credit exhausted" in out["message"]


def test_a_first_send_is_not_labelled_a_reminder():
    alert = {"message": "Anthropic credit exhausted", "is_reminder": False,
             "age_hours": 0.1}
    assert notifier._decorate(alert)["message"] == "Anthropic credit exhausted"


def test_decorate_never_drops_the_original_message():
    for reminder in (True, False):
        out = notifier._decorate({"message": "X marks it", "is_reminder": reminder,
                                  "age_hours": 5.0})
        assert "X marks it" in out["message"]


def test_the_twelve_hour_case_would_now_re_send():
    """The concrete regression: notified 07:05, still unresolved at 19:00."""
    hours_since_notify = 11.9
    assert hours_since_notify > notifier.RENOTIFY_AFTER_HOURS, (
        "a 12-hour-old unresolved CRITICAL must be past the re-notify cadence")
