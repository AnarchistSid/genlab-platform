"""Publisher wire for niche-pause — PR #577 final consumer.

Pins:

  * _publisher_niche_is_paused delegates to niche_pause.is_paused
  * Paused niche → run_publish returns EXIT_SUCCESS WITHOUT calling
    token refresh, blueprint selection, or any publish work
  * Not paused → run_publish proceeds normally
  * ImportError on niche_pause → False (graceful degrade)
  * Exception in is_paused → False (fail-OPEN — never halt publish
    on transient DB error)
  * Pause check runs BEFORE _preflight_token_refresh (saves wasted
    OAuth refresh round-trip when paused)
  * Pause check runs in BOTH normal AND retry_only modes
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

# ── _publisher_niche_is_paused unit ────────────────────────────────


def test_helper_delegates_to_niche_pause_is_paused():
    from genlab_core.publishing.publish_all_platforms import _publisher_niche_is_paused

    with patch(
        "genlab_core.scheduling.niche_pause.is_paused",
        return_value=True,
    ) as mock_paused:
        result = _publisher_niche_is_paused("gaming")
    assert result is True
    mock_paused.assert_called_once_with("gaming")


def test_helper_returns_false_when_not_paused():
    from genlab_core.publishing.publish_all_platforms import _publisher_niche_is_paused

    with patch("genlab_core.scheduling.niche_pause.is_paused", return_value=False):
        assert _publisher_niche_is_paused("gaming") is False


def test_helper_handles_import_error_gracefully():
    """Pre-PR-577 core → no niche_pause module → False (safe deploy
    against an older core that doesn't have the table yet)."""
    from genlab_core.publishing.publish_all_platforms import _publisher_niche_is_paused

    with patch.dict(
        "sys.modules",
        {"genlab_core.scheduling.niche_pause": None},
    ):
        assert _publisher_niche_is_paused("gaming") is False


def test_helper_fail_open_on_exception():
    """is_paused exception → False. Publishing MUST NOT halt on a
    transient DB hiccup — same fail-OPEN as the auto_approver wire."""
    from genlab_core.publishing.publish_all_platforms import _publisher_niche_is_paused

    with patch(
        "genlab_core.scheduling.niche_pause.is_paused",
        side_effect=RuntimeError("simulated DB error"),
    ):
        assert _publisher_niche_is_paused("gaming") is False


# ── run_publish integration ───────────────────────────────────────


def test_run_publish_skips_when_paused(monkeypatch):
    """Paused niche → run_publish returns EXIT_SUCCESS WITHOUT
    calling token refresh, blueprint selection, or any publish
    work. The launchd job sees success; the operator sees the
    pause reason in pipeline logs."""
    from genlab_core.publishing import publish_all_platforms as pap_mod
    from genlab_core.publishing.publish_all_platforms import EXIT_SUCCESS, run_publish

    monkeypatch.setattr(pap_mod, "_publisher_niche_is_paused", lambda nid: True)

    # If the pause check is wired correctly, NONE of these should fire:
    token_refresh = MagicMock()
    recover_stuck = MagicMock()
    select_bp = MagicMock()
    monkeypatch.setattr(pap_mod, "_preflight_token_refresh", token_refresh)
    monkeypatch.setattr(pap_mod, "recover_stuck_publishing", recover_stuck)
    monkeypatch.setattr(pap_mod, "select_blueprint", select_bp)

    rc = run_publish(
        niche_id="gaming",
        backlog_client=MagicMock(),
        daily_cap=MagicMock(),
        enabled_platforms=["instagram", "youtube"],
    )
    assert rc == EXIT_SUCCESS
    # Critical: none of the downstream work happened
    token_refresh.assert_not_called()
    recover_stuck.assert_not_called()
    select_bp.assert_not_called()


def test_run_publish_skips_retry_pass_when_paused(monkeypatch):
    """retry_only=True must ALSO respect the pause. Without this,
    the 10:30 UTC retry pass could publish a recovered blueprint
    on a niche the operator paused."""
    from genlab_core.publishing import publish_all_platforms as pap_mod
    from genlab_core.publishing.publish_all_platforms import EXIT_SUCCESS, run_publish

    monkeypatch.setattr(pap_mod, "_publisher_niche_is_paused", lambda nid: True)

    run_retry = MagicMock()
    monkeypatch.setattr(pap_mod, "_run_retry_pass", run_retry)
    monkeypatch.setattr(pap_mod, "_preflight_token_refresh", MagicMock())
    monkeypatch.setattr(pap_mod, "recover_stuck_publishing", MagicMock())
    monkeypatch.setattr(pap_mod, "recover_publish_failed", MagicMock())

    rc = run_publish(
        niche_id="gaming",
        backlog_client=MagicMock(),
        daily_cap=MagicMock(),
        enabled_platforms=["instagram"],
        retry_only=True,
    )
    assert rc == EXIT_SUCCESS
    run_retry.assert_not_called()  # retry pass MUST also be skipped


def test_run_publish_proceeds_when_not_paused(monkeypatch):
    """Not paused → token refresh fires (the next step in the
    happy path). We stub select_blueprint to return None so the
    test exits cleanly without exercising the full publish path."""
    from genlab_core.publishing import publish_all_platforms as pap_mod
    from genlab_core.publishing.publish_all_platforms import run_publish

    monkeypatch.setattr(pap_mod, "_publisher_niche_is_paused", lambda nid: False)
    token_refresh = MagicMock()
    monkeypatch.setattr(pap_mod, "_preflight_token_refresh", token_refresh)
    monkeypatch.setattr(pap_mod, "recover_stuck_publishing", MagicMock())
    monkeypatch.setattr(pap_mod, "recover_publish_failed", MagicMock())
    monkeypatch.setattr(pap_mod, "select_blueprint", lambda *a, **kw: None)
    monkeypatch.setattr(pap_mod, "_run_retry_pass", MagicMock())

    run_publish(
        niche_id="gaming",
        backlog_client=MagicMock(),
        daily_cap=MagicMock(),
        enabled_platforms=["instagram"],
    )
    # Token refresh was called → pause check returned False as expected
    token_refresh.assert_called_once_with("gaming", ["instagram"])


# ── source pins ────────────────────────────────────────────────────


def test_source_pin_helper_present():
    """The _publisher_niche_is_paused helper exists with the
    expected name + PR anchor. Pin so a future refactor that
    renames or inlines it surfaces in tests."""
    import genlab_core.publishing.publish_all_platforms as mod

    src = Path(mod.__file__).read_text()
    assert "def _publisher_niche_is_paused(niche_id: str) -> bool:" in src
    assert "PR #577 consumer wire" in src


def test_source_pin_pause_check_before_token_refresh():
    """Pin order: pause check MUST run BEFORE _preflight_token_refresh.
    Otherwise we'd waste an OAuth round-trip on a paused niche.
    Same logic as the auto-approver guard ordering — early exit
    saves downstream work."""
    import genlab_core.publishing.publish_all_platforms as mod

    src = Path(mod.__file__).read_text()
    # Both lines must exist
    assert "if _publisher_niche_is_paused(niche_id):" in src
    assert "_preflight_token_refresh(niche_id, enabled_platforms)" in src
    # rindex finds the LAST occurrence — that's the call site, not
    # the function definition. Pause check call must come BEFORE
    # the token-refresh call site.
    pause_check_idx = src.rindex("if _publisher_niche_is_paused(niche_id):")
    token_refresh_call_idx = src.rindex("_preflight_token_refresh(niche_id, enabled_platforms)")
    assert pause_check_idx < token_refresh_call_idx, (
        "Pause check MUST run BEFORE _preflight_token_refresh — "
        "otherwise paused niches waste an OAuth refresh roundtrip"
    )
