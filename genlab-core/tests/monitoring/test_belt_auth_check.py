"""Belt's credential, monitored on its own.

Belt auth was only probed INSIDE check_anthropic_credit, which escalates when
belt is not serving -- and that works only while Anthropic is ALSO broken.
Measured 2026-09-17: the belt session expired at 12:30 and the platform ran with
zero approvals for six hours. It surfaced only because Anthropic happened to be
unfunded at the same moment. A coincidence is not a monitor.
"""

import pytest
from genlab_core.monitoring.checks.infrastructure import check_belt_auth


@pytest.fixture
def belt(monkeypatch):
    state = {"status": "ok", "keyed": False}

    import genlab_core.integrations.belt_client as bc

    monkeypatch.setattr(bc, "auth_status", lambda: state["status"], raising=False)
    monkeypatch.setattr(bc, "using_api_key", lambda: state["keyed"], raising=False)
    return state


def test_authenticated_belt_is_silent(belt):
    assert check_belt_auth() == []


def test_an_expired_session_is_CRITICAL_on_its_own(belt):
    """The case that ran for six hours in silence."""
    belt["status"], belt["keyed"] = "unauthenticated", False
    a = check_belt_auth()
    assert len(a) == 1
    assert a[0].severity == "critical" and a[0].check == "belt_unauthenticated"


def test_the_expired_session_message_names_the_durable_fix(belt):
    belt["status"], belt["keyed"] = "unauthenticated", False
    msg = check_belt_auth()[0].message
    assert "INFSH_API_KEY" in msg and "does not expire" in msg


def test_a_rejected_api_key_is_reported_as_a_config_error_not_an_expiry(belt):
    """With a key set, 'unauthenticated' means a BAD key -- a different fix."""
    belt["status"], belt["keyed"] = "unauthenticated", True
    a = check_belt_auth()[0]
    assert a.severity == "critical"
    assert "bad or revoked" in a.message and a.auto_fix == "rotate INFSH_API_KEY"


def test_an_unreachable_binary_is_a_WARNING_not_a_CRITICAL(belt):
    """Retryable. Paging on a network blip trains the operator to ignore pages."""
    belt["status"] = "unavailable"
    a = check_belt_auth()[0]
    assert a.severity == "warning" and a.check == "belt_unavailable"


def test_the_alert_says_what_breaks_downstream(belt):
    belt["status"], belt["keyed"] = "unauthenticated", False
    msg = check_belt_auth()[0].message
    assert "primary LLM tier" in msg
    assert "judge" in msg


def test_a_probe_that_raises_never_takes_the_monitor_down(belt, monkeypatch):
    import genlab_core.integrations.belt_client as bc

    def boom():
        raise RuntimeError("belt exploded")

    monkeypatch.setattr(bc, "auth_status", boom, raising=False)
    assert check_belt_auth() == []


def test_details_carry_both_signals_for_the_dashboard(belt):
    belt["status"], belt["keyed"] = "unauthenticated", True
    d = check_belt_auth()[0].details
    assert d["auth_status"] == "unauthenticated" and d["using_api_key"] is True


def test_the_check_is_wired_into_the_health_monitor_run():
    """A check nobody calls is a check that does not exist."""
    import pathlib

    src = pathlib.Path(
        __import__("genlab_core.monitoring.health_monitor",
                   fromlist=["x"]).__file__).read_text()
    assert "all_alerts.extend(check_belt_auth())" in src
