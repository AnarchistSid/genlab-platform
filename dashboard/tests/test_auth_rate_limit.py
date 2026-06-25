"""PR #564 (2026-06-25) — per-IP rate-limit on basic-auth.

Pins the shared rate-limit helpers + behavior on both auth paths:

  _login_rate_limit_exceeded(ip) -> bool
  _login_record_attempt(ip) -> None

These extract the inline form-POST rate-limit machinery so the
basic-auth header path can use the same logic. Closes the
brute-force gap flagged as a known limitation in PR #562.

Form-POST already had rate-limiting; that surface is preserved
bit-for-bit (post-extraction the inline code calls the same
helpers). Basic-auth is the NEW surface this PR protects.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import pytest

# ── source pins ────────────────────────────────────────────────────


def test_source_pin_helpers_present():
    """The two extracted helpers exist with documented names + PR
    anchor. Pin so a future refactor that renames or inlines them
    surfaces in tests."""
    import server.review_server as mod

    src = Path(mod.__file__).read_text()
    assert "def _login_rate_limit_exceeded(ip: str) -> bool:" in src
    assert "def _login_record_attempt(ip: str) -> None:" in src
    assert "PR #564" in src


def test_source_pin_basic_auth_uses_rate_limit():
    """The basic-auth path MUST consult the rate-limit BEFORE
    calling _authenticate — otherwise unlimited brute-force is
    still possible."""
    import server.review_server as mod

    src = Path(mod.__file__).read_text()
    # Pin both: the check is called, and 429 is returned on excess
    assert "if _login_rate_limit_exceeded(ip):" in src
    # 429 response shape (JSON for API; Retry-After header)
    assert '"Too many login attempts.' in src
    assert "Retry-After" in src


def test_source_pin_form_post_uses_extracted_helper():
    """The form-POST path should now also use the extracted
    helper (single source of truth). Pin that the inline
    pre-#564 implementation is GONE."""
    import server.review_server as mod

    src = Path(mod.__file__).read_text()
    # The form-POST branch uses the helper
    assert src.count("if _login_rate_limit_exceeded(ip):") >= 2, (
        "both form-POST and basic-auth paths must call the helper"
    )
    # The old inline implementation (purge + bucket prune + check)
    # should NOT appear twice — the helper centralises it
    inline_check_lines = src.count("if len(_login_attempts[ip]) >= _LOGIN_RATE_LIMIT:")
    assert inline_check_lines <= 1, (
        f"inline rate-limit check appears {inline_check_lines}x — should be exactly 1 "
        "(inside _login_rate_limit_exceeded)"
    )


# ── unit tests for the helpers ─────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_login_attempts():
    """Each test gets a fresh _login_attempts dict so per-test IP
    state doesn't leak."""
    import server.review_server as mod

    mod._login_attempts.clear()
    yield
    mod._login_attempts.clear()


def test_rate_limit_exceeded_returns_false_for_first_attempts():
    """Fresh IP → False (allowed) for the first 5 attempts."""
    import server.review_server as mod

    for _ in range(mod._LOGIN_RATE_LIMIT):
        assert mod._login_rate_limit_exceeded("1.2.3.4") is False
        mod._login_record_attempt("1.2.3.4")


def test_rate_limit_exceeded_returns_true_on_sixth_attempt():
    """6th attempt within the window → True (denied)."""
    import server.review_server as mod

    for _ in range(mod._LOGIN_RATE_LIMIT):
        mod._login_record_attempt("attacker")
    # 5 attempts recorded → 6th check should return True
    assert mod._login_rate_limit_exceeded("attacker") is True


def test_rate_limit_isolated_per_ip():
    """Attacker burning their budget MUST NOT affect a legitimate
    user from a different IP. Critical: rate-limit is per-IP, not
    global."""
    import server.review_server as mod

    for _ in range(mod._LOGIN_RATE_LIMIT):
        mod._login_record_attempt("attacker")
    # Attacker is locked out
    assert mod._login_rate_limit_exceeded("attacker") is True
    # Legitimate user from different IP is still allowed
    assert mod._login_rate_limit_exceeded("legitimate") is False


def test_rate_limit_window_expires():
    """Timestamps older than _LOGIN_RATE_WINDOW seconds are pruned.
    Pin via monkeypatching the time source — simulate 61s passing."""
    import server.review_server as mod

    with patch.object(mod._time, "time", return_value=1000.0):
        for _ in range(mod._LOGIN_RATE_LIMIT):
            mod._login_record_attempt("user")
        assert mod._login_rate_limit_exceeded("user") is True
    # Advance clock 61 seconds; all timestamps should prune
    with patch.object(mod._time, "time", return_value=1061.0):
        assert mod._login_rate_limit_exceeded("user") is False


def test_dict_purged_when_over_200_ips():
    """Bounded memory: when more than 200 IPs are tracked, stale
    entries get evicted. Pin the cleanup heuristic."""
    import server.review_server as mod

    # Pre-populate 250 IPs with very old timestamps (>10 windows ago)
    very_old = _t_now(mod) - mod._LOGIN_RATE_WINDOW * 100
    for i in range(250):
        mod._login_attempts[f"old.{i}"] = [very_old]

    # New IP triggers the cleanup path
    mod._login_rate_limit_exceeded("fresh.1.2.3")
    # Stale IPs got pruned; size dropped meaningfully
    assert len(mod._login_attempts) < 250


def _t_now(mod):
    return mod._time.time()


# ── integration: basic-auth path returns 429 on excess ────────────


def _flask_app():
    """Get the Flask app from review_server (skipping the SocketIO
    wrapper since we only need request-routing for these tests)."""
    import server.review_server as mod

    return mod.app


def _basic_auth_header(user: str, password: str) -> str:
    """Compose an HTTP Basic Auth header value."""
    raw = f"{user}:{password}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def test_basic_auth_returns_429_on_sixth_attempt(monkeypatch):
    """6 wrong-password attempts from the same IP via basic-auth →
    the 6th gets a 429 response. The status code matters: 401
    invites retry; 429 signals 'come back later'."""
    import server.review_server as mod

    monkeypatch.setattr(mod, "_AUTH_USER", "admin")
    monkeypatch.setattr(mod, "_AUTH_PASS", "correct_password")
    # _AUTH_ENABLED is evaluated ONCE at module-load from env; in CI
    # the env vars aren't set so the module-level bool is False.
    # Force it True so the auth chain (rate-limit + _authenticate)
    # actually runs in tests.
    monkeypatch.setattr(mod, "_AUTH_ENABLED", True)

    app = _flask_app()
    with app.test_client() as c:
        # 5 wrong attempts — all should return 401
        for _ in range(mod._LOGIN_RATE_LIMIT):
            r = c.get(
                "/api/v1/health",
                headers={"Authorization": _basic_auth_header("admin", "wrong")},
            )
            # 401 or some other auth-denied status — but NOT 429 yet
            assert r.status_code != 429
        # 6th attempt → 429
        r6 = c.get(
            "/api/v1/health",
            headers={"Authorization": _basic_auth_header("admin", "wrong")},
        )
        assert r6.status_code == 429
        assert "Too many login attempts" in r6.get_data(as_text=True)
        assert r6.headers.get("Retry-After") == "60"


def test_basic_auth_correct_password_inside_budget_succeeds(monkeypatch):
    """Legitimate user with correct password on attempt #1 should
    succeed (budget not yet exhausted). Pin so the rate-limit
    doesn't accidentally break the happy path."""
    import server.review_server as mod

    monkeypatch.setattr(mod, "_AUTH_USER", "admin")
    monkeypatch.setattr(mod, "_AUTH_PASS", "correct_password")
    # _AUTH_ENABLED is evaluated ONCE at module-load from env; in CI
    # the env vars aren't set so the module-level bool is False.
    # Force it True so the auth chain (rate-limit + _authenticate)
    # actually runs in tests.
    monkeypatch.setattr(mod, "_AUTH_ENABLED", True)
    # Disable the DB users-table path so we exercise pure env-var auth
    with patch("genlab_core.auth.users.get_user_by_username", return_value=None):
        app = _flask_app()
        with app.test_client() as c:
            r = c.get(
                "/api/v1/health",
                headers={"Authorization": _basic_auth_header("admin", "correct_password")},
            )
    # 200 (or any non-401, non-429); critical: NOT denied
    assert r.status_code != 401
    assert r.status_code != 429


def test_basic_auth_no_authorization_header_skips_rate_limit(monkeypatch):
    """A request with NO Authorization header at all (e.g. browser
    chrome probing for the realm) must NOT consume rate-limit
    budget. Otherwise a few-tab-refresh would lock the legitimate
    user out before they even tried to log in."""
    import server.review_server as mod

    monkeypatch.setattr(mod, "_AUTH_USER", "admin")
    monkeypatch.setattr(mod, "_AUTH_PASS", "correct_password")
    # _AUTH_ENABLED is evaluated ONCE at module-load from env; in CI
    # the env vars aren't set so the module-level bool is False.
    # Force it True so the auth chain (rate-limit + _authenticate)
    # actually runs in tests.
    monkeypatch.setattr(mod, "_AUTH_ENABLED", True)

    app = _flask_app()
    with app.test_client() as c:
        # 10 requests with no Authorization → no rate-limit consumed
        for _ in range(10):
            c.get("/api/v1/health")
    # IP bucket should be empty (no attempts recorded)
    assert "127.0.0.1" not in mod._login_attempts or not mod._login_attempts.get("127.0.0.1")


def test_basic_auth_rate_limit_isolated_per_ip(monkeypatch):
    """Attacker from .1 burning budget MUST NOT lock out .2.
    Pin per-IP isolation at the integration layer."""
    import server.review_server as mod

    monkeypatch.setattr(mod, "_AUTH_USER", "admin")
    monkeypatch.setattr(mod, "_AUTH_PASS", "correct_password")
    # _AUTH_ENABLED is evaluated ONCE at module-load from env; in CI
    # the env vars aren't set so the module-level bool is False.
    # Force it True so the auth chain (rate-limit + _authenticate)
    # actually runs in tests.
    monkeypatch.setattr(mod, "_AUTH_ENABLED", True)

    # Pre-burn attacker's budget directly
    for _ in range(mod._LOGIN_RATE_LIMIT):
        mod._login_record_attempt("9.9.9.9")

    app = _flask_app()
    with app.test_client() as c:
        # Test client's remote_addr is 127.0.0.1; this should succeed
        r = c.get(
            "/api/v1/health",
            headers={"Authorization": _basic_auth_header("admin", "correct_password")},
        )
    # 127.0.0.1 has fresh budget; attacker (9.9.9.9) doesn't affect it
    assert r.status_code != 429
