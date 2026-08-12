"""Pin the AUTO #2 Phase 3 auto-advance module.

Contract:

  * Flag off -> no advance, reason="flag_off"
  * At ladder cap (1.0) -> no advance, reason="at_ladder_cap"
  * Cooldown not elapsed -> no advance, reason=cooldown_remaining_Nd
  * Signal not ready -> no advance, reason=signal_not_ready ...
  * All conditions met -> advance to next ladder step, persist state
  * State write failure -> no advance persistence

  * `get_state_override_for_niche(niche_id, yaml_pct=X)` returns
    max(state_pct, yaml_pct) — monotone-up semantics. Operator YAML
    lower is always authoritative (state can't demote).

  * State file corrupt / missing / permission denied -> fail-open
    to yaml_pct baseline.

Structural pin:

  * auto_approver.load_policy calls get_state_override_for_niche
    so the persisted advance takes effect on next pass.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from genlab_core.scheduling.ratchet_advancer import (
    _COOLDOWN_DAYS,
    _LADDER,
    AdvancementDecision,
    check_and_advance,
    get_state_override_for_niche,
)


@pytest.fixture(autouse=True)
def _state_file_in_tmpdir(tmp_path, monkeypatch):
    """Redirect the state file to a per-test tmpdir so tests don't
    read/write the prod path at /opt/genlab/.runtime/."""
    monkeypatch.setenv("GENLAB_RATCHET_STATE_PATH", str(tmp_path / "state.json"))
    return tmp_path / "state.json"


def _write_state(path, niches: dict):
    path.write_text(json.dumps({"version": 1, "niches": niches}))


class TestGetStateOverride:
    def test_missing_state_returns_yaml(self):
        assert get_state_override_for_niche("gaming", yaml_pct=0.1) == 0.1

    def test_state_higher_wins(self, _state_file_in_tmpdir):
        _write_state(_state_file_in_tmpdir, {"gaming": {"current_pct": 0.5}})
        assert get_state_override_for_niche("gaming", yaml_pct=0.1) == 0.5

    def test_yaml_higher_wins_state_cannot_demote(self, _state_file_in_tmpdir):
        """Operator YAML edit is authoritative. If state says 0.1 but
        YAML says 0.5 (operator wants faster rollout), respect YAML."""
        _write_state(_state_file_in_tmpdir, {"gaming": {"current_pct": 0.1}})
        assert get_state_override_for_niche("gaming", yaml_pct=0.5) == 0.5

    def test_state_equal_yaml_returns_yaml(self, _state_file_in_tmpdir):
        _write_state(_state_file_in_tmpdir, {"gaming": {"current_pct": 0.25}})
        assert get_state_override_for_niche("gaming", yaml_pct=0.25) == 0.25

    def test_corrupt_state_returns_yaml(self, _state_file_in_tmpdir):
        _state_file_in_tmpdir.write_text("{malformed json")
        assert get_state_override_for_niche("gaming", yaml_pct=0.1) == 0.1

    def test_missing_niche_in_state_returns_yaml(self, _state_file_in_tmpdir):
        _write_state(_state_file_in_tmpdir, {"anime": {"current_pct": 0.5}})
        assert get_state_override_for_niche("gaming", yaml_pct=0.1) == 0.1


class TestFlagGating:
    def test_flag_off_no_advance(self, monkeypatch):
        monkeypatch.delenv("GENLAB_AUTO_ADVANCE_ROLLOUT_ENABLED", raising=False)
        result = check_and_advance("gaming")
        assert result.advanced is False
        assert result.reason == "flag_off"


class TestLadderClimb:
    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("GENLAB_AUTO_ADVANCE_ROLLOUT_ENABLED", "1")

    def _mock_signal(self, ready: bool):
        from dataclasses import dataclass

        @dataclass
        class _S:
            calibration_samples: int = 0
            calibration_agreement_count: int = 0
            calibration_agreement_rate: float = 0.0
            calibration_ready: bool = False
            outcome_samples: int = 30
            outcome_good_count: int = 25
            outcome_good_rate: float = 0.83
            outcome_ready: bool = True
            combined_ready: bool = ready

        return _S()

    def test_signal_not_ready_no_advance(self, monkeypatch):
        with patch(
            "genlab_core.scheduling.ratchet_advancement.check_ratchet_advancement_signal",
            return_value=self._mock_signal(ready=False),
        ):
            result = check_and_advance("gaming")
        assert result.advanced is False
        assert "signal_not_ready" in result.reason

    def test_advance_from_cold_start(self, _state_file_in_tmpdir):
        """No prior state -> starts at 0.1, advances to 0.25."""
        with patch(
            "genlab_core.scheduling.ratchet_advancement.check_ratchet_advancement_signal",
            return_value=self._mock_signal(ready=True),
        ):
            result = check_and_advance("gaming")
        assert result.advanced is True
        assert result.current_pct == 0.25
        assert result.target_pct == 0.25
        # State persisted
        state = json.loads(_state_file_in_tmpdir.read_text())
        assert state["niches"]["gaming"]["current_pct"] == 0.25
        assert state["niches"]["gaming"]["history"]
        assert state["niches"]["gaming"]["history"][0]["to"] == 0.25

    def test_ladder_advances_one_step_at_a_time(self, _state_file_in_tmpdir):
        _write_state(_state_file_in_tmpdir, {"gaming": {"current_pct": 0.25}})
        with patch(
            "genlab_core.scheduling.ratchet_advancement.check_ratchet_advancement_signal",
            return_value=self._mock_signal(ready=True),
        ):
            result = check_and_advance("gaming")
        # From 0.25 -> next step is 0.5, NOT 1.0
        assert result.current_pct == 0.5

    def test_at_cap_no_advance(self, _state_file_in_tmpdir):
        _write_state(_state_file_in_tmpdir, {"gaming": {"current_pct": 1.0}})
        with patch(
            "genlab_core.scheduling.ratchet_advancement.check_ratchet_advancement_signal",
            return_value=self._mock_signal(ready=True),
        ):
            result = check_and_advance("gaming")
        assert result.advanced is False
        assert result.reason == "at_ladder_cap"

    def test_cooldown_blocks_advance(self, _state_file_in_tmpdir):
        """Recently advanced (< 7d ago) -> no advance even if signal ready."""
        recent = (datetime.now(UTC) - timedelta(days=3)).isoformat()
        _write_state(_state_file_in_tmpdir, {
            "gaming": {"current_pct": 0.25, "last_advanced_at": recent},
        })
        with patch(
            "genlab_core.scheduling.ratchet_advancement.check_ratchet_advancement_signal",
            return_value=self._mock_signal(ready=True),
        ):
            result = check_and_advance("gaming")
        assert result.advanced is False
        assert "cooldown_remaining" in result.reason

    def test_cooldown_elapsed_advances(self, _state_file_in_tmpdir):
        old = (datetime.now(UTC) - timedelta(days=_COOLDOWN_DAYS + 1)).isoformat()
        _write_state(_state_file_in_tmpdir, {
            "gaming": {"current_pct": 0.25, "last_advanced_at": old},
        })
        with patch(
            "genlab_core.scheduling.ratchet_advancement.check_ratchet_advancement_signal",
            return_value=self._mock_signal(ready=True),
        ):
            result = check_and_advance("gaming")
        assert result.advanced is True
        assert result.current_pct == 0.5

    def test_corrupt_last_advanced_at_treated_as_never(self, _state_file_in_tmpdir):
        """Corrupt timestamp shouldn't stall the ratchet — treat as
        never-advanced so cooldown check passes."""
        _write_state(_state_file_in_tmpdir, {
            "gaming": {"current_pct": 0.25, "last_advanced_at": "not-a-date"},
        })
        with patch(
            "genlab_core.scheduling.ratchet_advancement.check_ratchet_advancement_signal",
            return_value=self._mock_signal(ready=True),
        ):
            result = check_and_advance("gaming")
        assert result.advanced is True

    def test_history_appended_on_advance(self, _state_file_in_tmpdir):
        _write_state(_state_file_in_tmpdir, {
            "gaming": {"current_pct": 0.25, "history": [
                {"at": "2026-08-01T00:00:00+00:00", "from": 0.1, "to": 0.25, "reason": "..."},
            ]},
        })
        old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        state = {"version": 1, "niches": {
            "gaming": {
                "current_pct": 0.25,
                "last_advanced_at": old,
                "history": [{"at": "2026-08-01T00:00:00+00:00", "from": 0.1, "to": 0.25, "reason": "..."}],
            }
        }}
        _state_file_in_tmpdir.write_text(json.dumps(state))
        with patch(
            "genlab_core.scheduling.ratchet_advancement.check_ratchet_advancement_signal",
            return_value=self._mock_signal(ready=True),
        ):
            check_and_advance("gaming")
        new_state = json.loads(_state_file_in_tmpdir.read_text())
        assert len(new_state["niches"]["gaming"]["history"]) == 2

    def test_advance_logs_warn(self, _state_file_in_tmpdir, caplog):
        with patch(
            "genlab_core.scheduling.ratchet_advancement.check_ratchet_advancement_signal",
            return_value=self._mock_signal(ready=True),
        ), caplog.at_level(logging.WARNING):
            check_and_advance("gaming")
        assert any("ADVANCED niche=gaming" in r.message for r in caplog.records)


class TestLadderConstant:
    def test_ladder_is_monotonically_increasing(self):
        for i in range(len(_LADDER) - 1):
            assert _LADDER[i] < _LADDER[i + 1]

    def test_ladder_starts_at_ten_percent(self):
        assert _LADDER[0] == 0.1

    def test_ladder_caps_at_one(self):
        assert _LADDER[-1] == 1.0


class TestLoadPolicyWire:
    def test_load_policy_source_calls_state_override(self):
        """Structural pin: auto_approver.load_policy imports + calls
        get_state_override_for_niche so persisted advances take effect."""
        import pathlib

        path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "scheduling"
            / "auto_approver.py"
        )
        src = path.read_text()
        assert "from genlab_core.scheduling.ratchet_advancer import" in src
        assert "get_state_override_for_niche" in src
        assert "yaml_pct=rollout_pct" in src
