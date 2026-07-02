"""Intervention 7 consumer-wire pins (2026-07-02) — DR replay
artifact into Strategist prompt.

The wire has two parts:

* ``StateCollector._counterfactual_replay(niche_id)`` reads the
  latest ``replay-*-<niche>.json`` from
  ``$GENLAB_TMP/counterfactual-replay/`` (with fallback to
  ``replay-*-all.json``). Returns None when no artifact / malformed.

* ``prompts._format_counterfactual_replay(replay)`` renders the
  section for the Strategist prompt. Returns cold-start line when
  replay is None; renders top-5 arms by DR reward when populated.

Pins:

* No artifact → None returned + prompt shows explicit cold-start line
* Artifact present → top-5 arms rendered, DR badge label reflects
  ``dr_enabled``
* Malformed JSON → falls back to None (never crashes Strategist)
* Niche-specific artifact preferred over all-niches fallback
* All-niches fallback used when niche-specific missing
* n_with_reward filter — arms below n=3 don't feature (too noisy)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _write_artifact(root: Path, filename: str, payload: dict) -> Path:
    dest = root / "counterfactual-replay"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / filename
    path.write_text(json.dumps(payload))
    return path


class TestCounterfactualReplayFormatter:
    """The prompt formatter is a pure function — tests it in
    isolation without needing the DB-backed StateCollector."""

    def test_none_renders_cold_start_line(self):
        from genlab_core.intelligence.prompts import _format_counterfactual_replay

        out = _format_counterfactual_replay(None)
        assert "no artifact yet" in out

    def test_empty_per_arm_renders_empty_message(self):
        from genlab_core.intelligence.prompts import _format_counterfactual_replay

        out = _format_counterfactual_replay({"per_arm": [], "dr_enabled": False, "window_days": 30})
        assert "empty replay" in out

    def test_all_arms_below_threshold(self):
        """Arms with n_with_reward < 3 must be filtered out."""
        from genlab_core.intelligence.prompts import _format_counterfactual_replay

        out = _format_counterfactual_replay(
            {
                "per_arm": [
                    {"arm_id": "a", "n_with_reward": 2, "ips_reward": 0.5},
                ],
                "dr_enabled": False,
                "window_days": 30,
            }
        )
        assert "no arm cleared n>=3" in out

    def test_renders_top_arms_by_dr_when_enabled(self):
        from genlab_core.intelligence.prompts import _format_counterfactual_replay

        replay = {
            "dr_enabled": True,
            "window_days": 30,
            "per_arm": [
                {
                    "arm_id": "style:gaming:low",
                    "n_with_reward": 10,
                    "ips_reward": 0.1,
                    "dr_reward": 0.12,
                },
                {
                    "arm_id": "style:gaming:high",
                    "n_with_reward": 15,
                    "ips_reward": 0.4,
                    "dr_reward": 0.55,
                },
            ],
        }
        out = _format_counterfactual_replay(replay)
        # DR label present when flag on
        assert "Signal: DR" in out
        # High-DR arm surfaces first (line index earlier)
        low_idx = out.find("style:gaming:low")
        high_idx = out.find("style:gaming:high")
        assert 0 <= high_idx < low_idx

    def test_falls_back_to_ips_when_dr_disabled(self):
        from genlab_core.intelligence.prompts import _format_counterfactual_replay

        out = _format_counterfactual_replay(
            {
                "dr_enabled": False,
                "window_days": 30,
                "per_arm": [
                    {
                        "arm_id": "style:gaming:a",
                        "n_with_reward": 10,
                        "ips_reward": 0.3,
                        "dr_reward": None,
                    }
                ],
            }
        )
        assert "IPS" in out
        assert "DR stub" in out  # Explicit signal that dr_reward is null


class TestCounterfactualReplayLoader:
    """StateCollector's artifact-loader path — tested by
    monkeypatching GENLAB_TMP and writing fixture files."""

    def _make_collector(self):
        # StateCollector needs a conn — but _counterfactual_replay
        # doesn't touch the DB. Construct a minimal instance.
        from genlab_core.intelligence.state_collector import PostgresStateCollector

        c = PostgresStateCollector.__new__(PostgresStateCollector)
        c._conn = None
        return c

    def test_no_dir_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        c = self._make_collector()
        assert c._counterfactual_replay("gaming") is None

    def test_niche_specific_artifact_loaded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        payload = {
            "niche": "gaming",
            "dr_enabled": True,
            "window_days": 30,
            "per_arm": [{"arm_id": "a", "n_with_reward": 5, "dr_reward": 0.4}],
        }
        _write_artifact(tmp_path, "replay-20260701-043000-gaming.json", payload)
        c = self._make_collector()
        result = c._counterfactual_replay("gaming")
        assert result is not None
        assert result["niche"] == "gaming"

    def test_falls_back_to_all_when_specific_missing(self, tmp_path, monkeypatch):
        """When no niche-specific artifact exists but a cross-niche
        ``replay-*-all.json`` does, use the cross-niche as fallback
        so the LLM has SOMETHING to reason about."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        payload = {
            "niche": "all",
            "dr_enabled": False,
            "window_days": 30,
            "per_arm": [],
        }
        _write_artifact(tmp_path, "replay-20260701-043000-all.json", payload)
        c = self._make_collector()
        result = c._counterfactual_replay("gaming")
        assert result is not None
        assert result["niche"] == "all"

    def test_niche_specific_preferred_over_all(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        _write_artifact(
            tmp_path,
            "replay-20260601-043000-all.json",
            {"niche": "all", "per_arm": []},
        )
        _write_artifact(
            tmp_path,
            "replay-20260701-043000-gaming.json",
            {"niche": "gaming", "per_arm": []},
        )
        c = self._make_collector()
        result = c._counterfactual_replay("gaming")
        assert result["niche"] == "gaming"

    def test_newest_by_mtime_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        p_old = _write_artifact(
            tmp_path,
            "replay-20260601-043000-gaming.json",
            {"generated_at": "old", "per_arm": []},
        )
        p_new = _write_artifact(
            tmp_path,
            "replay-20260701-043000-gaming.json",
            {"generated_at": "new", "per_arm": []},
        )
        os.utime(p_old, (time.time() - 3600, time.time() - 3600))
        os.utime(p_new, (time.time(), time.time()))
        c = self._make_collector()
        result = c._counterfactual_replay("gaming")
        assert result["generated_at"] == "new"

    def test_malformed_json_falls_back_to_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        dest = tmp_path / "counterfactual-replay"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "replay-20260701-043000-gaming.json").write_text("{corrupt")
        c = self._make_collector()
        # Must NOT raise — Strategist prompt build depends on this
        # returning None gracefully.
        assert c._counterfactual_replay("gaming") is None


class TestPromptIntegration:
    """The prompt-build call site — confirms the section header
    lands where expected and the formatter is called with the
    right key from state."""

    def test_prompt_includes_counterfactual_section(self):
        from genlab_core.intelligence.prompts import build_user_prompt

        state = {
            "niche_id": "gaming",
            "week_of": "2026-07-01",
            "run_at": "2026-07-02T05:00:00Z",
            "counterfactual_replay": None,
        }
        prompt = build_user_prompt(state)
        assert "COUNTERFACTUAL REPLAY" in prompt
        assert "no artifact yet" in prompt

    def test_prompt_renders_populated_replay(self):
        from genlab_core.intelligence.prompts import build_user_prompt

        state = {
            "niche_id": "gaming",
            "week_of": "2026-07-01",
            "run_at": "2026-07-02T05:00:00Z",
            "counterfactual_replay": {
                "dr_enabled": True,
                "window_days": 30,
                "per_arm": [
                    {
                        "arm_id": "style:gaming:revelation",
                        "n_with_reward": 10,
                        "ips_reward": 0.3,
                        "dr_reward": 0.45,
                    }
                ],
            },
        }
        prompt = build_user_prompt(state)
        assert "style:gaming:revelation" in prompt
        assert "Signal: DR" in prompt
