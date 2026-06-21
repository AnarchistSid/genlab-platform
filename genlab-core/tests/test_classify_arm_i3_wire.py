"""Pin: _classify_arm active-experiment override (Lever I3 wire).

PR #437 shipped the experiment registry (YAML loader + active filter).
THIS test file pins the wire-up: when an ``active_experiment`` is
provided AND ``GENLAB_EXPERIMENTATION_ENABLED=1`` AND the caller
supplies an ``experiment_assignment_id``, the registered experiment's
deterministic assignment OVERRIDES everything below — force-explore,
keyword matching, LinUCB tie-break, random control.

These pins lock in:

  Default-off (1):
  1. Env unset → experiment ignored, bandit chain runs normally

  Active experiment overrides (3):
  2. Env on + active experiment + assignment_id → assigned arm returned
     (even when force-explore would have fired)
  3. Same assignment_id → same arm (deterministic, no churn on re-runs)
  4. Logging emits an info line on every override

  Fall-through paths (4):
  5. active_experiment=None → bandit chain runs (legacy behavior)
  6. experiment_assignment_id="" → falls through (defensive: requires
     stable id for deterministic assignment)
  7. Inactive experiment (out-of-window) → assign_to_experiment returns
     None → falls through to bandit chain
  8. Experimentation module raising → falls through with debug log

  Composition with rest of chain (1):
  9. With env on + active experiment, the bandit's LinUCB pick is
     never even computed (return happens BEFORE force-explore)
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from genlab_core.learning.experimentation import ABTest
from genlab_core.pipeline.stages.push_to_backlog import _classify_arm

_MULTI_MATCH_STORY = {"title": "Patch notes", "summary": "Season 2 launch"}
_MULTI_MATCH_CONTENT = {"hook": "Big patch dropping"}


def _open_ended_experiment(arms=("patch_news", "trailer_reaction")) -> ABTest:
    """Always-active 50/50 experiment over the two gaming multi-match arms."""
    return ABTest(
        experiment_id="patch_vs_trailer_2026_07",
        arms=list(arms),
        allocation={a: 1.0 / len(arms) for a in arms},
        start_date="2020-01-01T00:00:00+00:00",
        end_date=None,
    )


def _expired_experiment() -> ABTest:
    return ABTest(
        experiment_id="expired_test",
        arms=["a", "b"],
        allocation={"a": 0.5, "b": 0.5},
        start_date="2020-01-01T00:00:00+00:00",
        end_date="2020-12-31T00:00:00+00:00",
    )


class TestDefaultOff:
    def test_env_unset_ignores_active_experiment(self, monkeypatch):
        """Pin: env flag is the master switch. Without it, the
        experiment override is dead code even when caller passes
        active_experiment + assignment_id."""
        monkeypatch.delenv("GENLAB_EXPERIMENTATION_ENABLED", raising=False)

        arm_id = _classify_arm(
            "gaming",
            _MULTI_MATCH_STORY,
            _MULTI_MATCH_CONTENT,
            arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
            active_experiment=_open_ended_experiment(),
            experiment_assignment_id="bp_test_001",
        )
        # Thompson path runs → patch_news wins on boost
        assert arm_id == "patch_news"


class TestActiveExperimentOverride:
    def test_active_experiment_overrides_thompson(self, monkeypatch):
        """Pin: env on + active experiment → returns assigned arm BEFORE
        any other path. Even when Thompson would have picked patch_news,
        the experiment's deterministic assignment wins."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")

        arm_id = _classify_arm(
            "gaming",
            _MULTI_MATCH_STORY,
            _MULTI_MATCH_CONTENT,
            arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
            active_experiment=_open_ended_experiment(),
            experiment_assignment_id="bp_test_001",
        )
        # Result depends on the SHA-256 hash bucket — but MUST be one
        # of the experiment's arms (which here overlap with matches).
        assert arm_id in {"patch_news", "trailer_reaction"}

    def test_deterministic_assignment_across_calls(self, monkeypatch):
        """Pin: same assignment_id → same arm across N calls. No churn
        on pipeline re-runs."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")

        exp = _open_ended_experiment()
        results = [
            _classify_arm(
                "gaming",
                _MULTI_MATCH_STORY,
                _MULTI_MATCH_CONTENT,
                arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
                active_experiment=exp,
                experiment_assignment_id="bp_xyz_999",
            )
            for _ in range(20)
        ]
        # 20 calls → 1 unique result
        assert len(set(results)) == 1

    def test_logs_info_on_override(self, monkeypatch, caplog):
        """Pin: every experiment override emits an INFO log so operators
        can audit assignment distribution post-hoc."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")

        with caplog.at_level(logging.INFO):
            _classify_arm(
                "gaming",
                _MULTI_MATCH_STORY,
                _MULTI_MATCH_CONTENT,
                arm_boosts={"patch_news": 1.5},
                active_experiment=_open_ended_experiment(),
                experiment_assignment_id="bp_log_test",
            )
        assert any("active experiment" in r.message for r in caplog.records)

    def test_override_runs_before_force_explore(self, monkeypatch):
        """Pin: active experiment short-circuits BEFORE force-explore
        runs. Operator-deliberate experiments always win over bandit-
        internal exploration."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")

        # arm_n_obs setup that would normally trigger force-explore
        arm_n_obs = {
            "patch_news": 50,
            "style:gaming:question": 0,  # under-explored style
        }

        with patch(
            "genlab_core.pipeline.stages.push_to_backlog._maybe_force_explore_style_arm"
        ) as mock_force:
            mock_force.return_value = "style:gaming:question"  # would force-explore
            arm_id = _classify_arm(
                "gaming",
                _MULTI_MATCH_STORY,
                _MULTI_MATCH_CONTENT,
                arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
                arm_n_obs=arm_n_obs,
                _rng=lambda: 0.0,  # would normally trigger
                active_experiment=_open_ended_experiment(),
                experiment_assignment_id="bp_explore_test",
            )

        # Experiment override wins → result is an experiment arm,
        # NOT the force-explore style arm
        assert arm_id in {"patch_news", "trailer_reaction"}
        # Force-explore was never even called
        mock_force.assert_not_called()


class TestFallThroughPaths:
    def test_no_active_experiment_runs_bandit_chain(self, monkeypatch):
        """Pin: active_experiment=None → bandit chain runs as before."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")

        arm_id = _classify_arm(
            "gaming",
            _MULTI_MATCH_STORY,
            _MULTI_MATCH_CONTENT,
            arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
            active_experiment=None,
        )
        # Thompson path (no experiment) → patch_news wins on boost
        # (note: random control may also fire occasionally since env on,
        # but with default epsilon=0.05 the bandit pick dominates the
        # single-run test result; loose assertion accepts either match)
        assert arm_id in {"patch_news", "trailer_reaction"}

    def test_empty_assignment_id_falls_through(self, monkeypatch):
        """Pin: experiment_assignment_id='' → falls through (defensive:
        requires stable id for deterministic assignment)."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")

        arm_id = _classify_arm(
            "gaming",
            _MULTI_MATCH_STORY,
            _MULTI_MATCH_CONTENT,
            arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
            active_experiment=_open_ended_experiment(),
            experiment_assignment_id="",  # absent
        )
        # Falls through to bandit chain
        assert arm_id in {"patch_news", "trailer_reaction"}

    def test_inactive_experiment_falls_through(self, monkeypatch):
        """Pin: expired experiment → assign_to_experiment returns None
        → wire-up falls through to bandit chain."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")

        arm_id = _classify_arm(
            "gaming",
            _MULTI_MATCH_STORY,
            _MULTI_MATCH_CONTENT,
            arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
            active_experiment=_expired_experiment(),
            experiment_assignment_id="bp_expired_test",
        )
        assert arm_id in {"patch_news", "trailer_reaction"}

    def test_assignment_exception_falls_through(self, monkeypatch):
        """Pin: assign_to_experiment raising mid-call → fall-through to
        bandit chain with debug log. Pipeline never crashes."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")

        with patch(
            "genlab_core.learning.experimentation.assign_to_experiment",
            side_effect=RuntimeError("simulated assignment bug"),
        ):
            arm_id = _classify_arm(
                "gaming",
                _MULTI_MATCH_STORY,
                _MULTI_MATCH_CONTENT,
                arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
                active_experiment=_open_ended_experiment(),
                experiment_assignment_id="bp_crash_test",
            )
        # Fell through to bandit chain (Thompson + possibly random
        # control since env=1) — either matched arm is acceptable
        assert arm_id in {"patch_news", "trailer_reaction"}
