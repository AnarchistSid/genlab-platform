"""Pin: _classify_arm LinUCB tie-break wiring (Lever I2 follow-up).

The picker primitive shipped in PR #434. THIS test file pins the
wire-up in ``push_to_backlog._classify_arm``: when the env flag is on
AND the caller provides linucb_arms + context, the multi-match tie-
break tries LinUCB-driven scoring FIRST and falls back to Thompson on
cold-start / errors. With the env flag off, behavior is identical to
the pre-wire baseline.

These pins lock in:

  Default-off (1):
  1. Env unset → Thompson path used (LinUCB never invoked)

  LinUCB path (4):
  2. Env on + linucb_arms + context + trained arms → LinUCB pick wins
     even when Thompson boost favors a different arm
  3. Env on + cold-start (any under-observed arm) → falls back to
     Thompson
  4. Env on + linucb_arms=None → falls back to Thompson (graceful)
  5. Env on + context=None → falls back to Thompson (graceful)

  Single-match short-circuit (1):
  6. Single matched arm bypasses both LinUCB and Thompson — returned
     directly. Wire-up must not introduce any latency in the common
     case.

  Fail-open (1):
  7. LinUCB picker raising mid-pick → falls back to Thompson with
     debug log. Pipeline never crashes on LinUCB failure.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
from genlab_core.learning.linucb import CONTEXT_DIM, LinUCBArm
from genlab_core.pipeline.stages.push_to_backlog import _classify_arm


def _trained_arm(*, n_obs: int = 20, bias_dim: int = 0, reward: float = 1.0) -> LinUCBArm:
    """Helper: build a LinUCBArm with n_obs observations all on bias_dim."""
    arm = LinUCBArm(d=CONTEXT_DIM)
    ctx = np.zeros(CONTEXT_DIM, dtype=np.float64)
    ctx[bias_dim] = 1.0
    for _ in range(n_obs):
        arm.update(ctx, reward)
    return arm


def _ctx(bias_dim: int = 0) -> np.ndarray:
    ctx = np.zeros(CONTEXT_DIM, dtype=np.float64)
    ctx[bias_dim] = 1.0
    return ctx


# Gaming story that matches multiple arms — "patch" hits patch_news,
# "launch" hits trailer_reaction. These are real keywords in _ARM_KEYWORDS.
_MULTI_MATCH_STORY = {"title": "Patch notes", "summary": "Season 2 launch"}
_MULTI_MATCH_CONTENT = {"hook": "Big patch dropping"}


class TestDefaultOffPreservesThompson:
    def test_env_unset_uses_thompson_path(self, monkeypatch):
        """Pin: with GENLAB_LINUCB_PICK_ENABLED unset, the new
        linucb_arms + context kwargs are accepted but never consumed.
        Thompson tie-break runs exactly as it did pre-wire."""
        monkeypatch.delenv("GENLAB_LINUCB_PICK_ENABLED", raising=False)

        # Thompson favors patch_news. LinUCB would favor trailer_reaction
        # (trained on bias_dim=0 with the matching query context). With
        # env off, Thompson wins.
        linucb_arms = {
            "patch_news": _trained_arm(n_obs=20, bias_dim=1),  # weak match
            "trailer_reaction": _trained_arm(n_obs=20, bias_dim=0),  # strong match
        }

        arm_id = _classify_arm(
            "gaming",
            _MULTI_MATCH_STORY,
            _MULTI_MATCH_CONTENT,
            arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
            linucb_arms=linucb_arms,
            context=_ctx(bias_dim=0),
        )
        # Thompson path (env off) → patch_news wins
        assert arm_id == "patch_news"


class TestLinUCBPathOverridesThompson:
    def test_linucb_wins_over_thompson_when_enabled(self, monkeypatch):
        """Pin: env on + sufficient data → LinUCB pick beats Thompson.
        This is the WHOLE POINT of I2 — context-aware tie-break replaces
        context-blind Thompson sampling."""
        monkeypatch.setenv("GENLAB_LINUCB_PICK_ENABLED", "1")

        # Same setup as test above — Thompson favors patch_news but
        # LinUCB strongly favors trailer_reaction (trained context aligns)
        linucb_arms = {
            "patch_news": _trained_arm(n_obs=20, bias_dim=1),
            "trailer_reaction": _trained_arm(n_obs=20, bias_dim=0),
        }

        arm_id = _classify_arm(
            "gaming",
            _MULTI_MATCH_STORY,
            _MULTI_MATCH_CONTENT,
            arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
            linucb_arms=linucb_arms,
            context=_ctx(bias_dim=0),
        )
        # LinUCB path (env on) → trailer_reaction wins despite weaker Thompson
        assert arm_id == "trailer_reaction"

    def test_cold_start_falls_back_to_thompson(self, monkeypatch):
        """Pin: even ONE under-observed arm collapses LinUCB pick to
        None (picker contract). Wire-up must fall back to Thompson."""
        monkeypatch.setenv("GENLAB_LINUCB_PICK_ENABLED", "1")

        # patch_news has 3 obs (below default min_obs=10) → cold-start
        linucb_arms = {
            "patch_news": _trained_arm(n_obs=3, bias_dim=0),
            "trailer_reaction": _trained_arm(n_obs=20, bias_dim=0),
        }

        arm_id = _classify_arm(
            "gaming",
            _MULTI_MATCH_STORY,
            _MULTI_MATCH_CONTENT,
            arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
            linucb_arms=linucb_arms,
            context=_ctx(bias_dim=0),
        )
        # Cold-start → Thompson takes over → patch_news (higher boost)
        assert arm_id == "patch_news"

    def test_no_linucb_arms_falls_back_gracefully(self, monkeypatch):
        """Pin: env on but linucb_arms=None → Thompson path. The opt-in
        is two-keyed: env flag AND caller provides data. Either missing
        = legacy behavior."""
        monkeypatch.setenv("GENLAB_LINUCB_PICK_ENABLED", "1")

        arm_id = _classify_arm(
            "gaming",
            _MULTI_MATCH_STORY,
            _MULTI_MATCH_CONTENT,
            arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
            linucb_arms=None,  # absent
            context=_ctx(),
        )
        assert arm_id == "patch_news"

    def test_no_context_falls_back_gracefully(self, monkeypatch):
        """Pin: env on but context=None → Thompson path. Same two-keyed
        opt-in as above but for the context side."""
        monkeypatch.setenv("GENLAB_LINUCB_PICK_ENABLED", "1")

        arm_id = _classify_arm(
            "gaming",
            _MULTI_MATCH_STORY,
            _MULTI_MATCH_CONTENT,
            arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
            linucb_arms={"patch_news": _trained_arm(n_obs=20)},
            context=None,  # absent
        )
        assert arm_id == "patch_news"


class TestSingleMatchShortCircuit:
    def test_single_match_returned_without_linucb_invocation(self, monkeypatch):
        """Pin: single-matched arm bypasses BOTH LinUCB AND Thompson.
        Wire-up must not introduce any latency in the common case
        (most stories match exactly one keyword set)."""
        monkeypatch.setenv("GENLAB_LINUCB_PICK_ENABLED", "1")

        # Story matches only one gaming arm — "esports" keyword for tournament_recap
        story = {"title": "ESL Cologne grand finals", "summary": "Tournament wrap-up"}
        content = {"hook": "ESL one wrap"}

        # Sanity: confirm only one arm matches before testing
        from genlab_core.pipeline.stages.push_to_backlog import _ARM_KEYWORDS

        text = f"{story['title']} {content['hook']} {story['summary']}".lower()
        matches = [
            aid for aid, kws in _ARM_KEYWORDS.get("gaming", []) if any(kw in text for kw in kws)
        ]
        assert len(matches) == 1, f"Test setup invalid — expected 1 match, got {matches}"

        # Even with LinUCB enabled, single-match short-circuits both
        # LinUCB AND Thompson — should never call into the picker.
        with patch("genlab_core.learning.linucb_picker.pick_best_arm") as mock_pick:
            arm_id = _classify_arm(
                "gaming",
                story,
                content,
                arm_boosts={matches[0]: 1.0},
                linucb_arms={matches[0]: _trained_arm(n_obs=20)},
                context=_ctx(),
            )
        assert arm_id == matches[0]
        # Picker MUST NOT have been called — single-match short-circuit
        mock_pick.assert_not_called()


class TestFailOpenOnPickerError:
    def test_picker_exception_falls_back_to_thompson(self, monkeypatch):
        """Pin: LinUCB picker raising mid-pick (e.g. numpy error,
        unexpected state) must NEVER crash the classifier. Falls back
        to Thompson with debug log."""
        monkeypatch.setenv("GENLAB_LINUCB_PICK_ENABLED", "1")

        with patch(
            "genlab_core.learning.linucb_picker.pick_best_arm",
            side_effect=RuntimeError("simulated picker bug"),
        ):
            arm_id = _classify_arm(
                "gaming",
                _MULTI_MATCH_STORY,
                _MULTI_MATCH_CONTENT,
                arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
                linucb_arms={
                    "patch_news": _trained_arm(n_obs=20),
                    "trailer_reaction": _trained_arm(n_obs=20),
                },
                context=_ctx(),
            )
        # Picker exception → Thompson fallback → patch_news (higher boost)
        assert arm_id == "patch_news"
