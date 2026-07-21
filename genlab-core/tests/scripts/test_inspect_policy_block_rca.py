"""Tests for scripts/inspect_policy_block_rca.py — CLI-arg parsing
+ flag-forcing + niche validation. LLM calls are patched out; the
real LLM path is exercised by tests/compliance/test_policy_block_rca.py.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Load the script as a module — it lives outside the package tree.
# Same pattern as test_archive_stale_visual_paths.py.
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "inspect_policy_block_rca.py"
spec = importlib.util.spec_from_file_location("inspect_policy_block_rca", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules["inspect_policy_block_rca"] = mod
spec.loader.exec_module(mod)


class TestNicheValidation:
    def test_invalid_niche_exits_1(self, capsys) -> None:
        rc = mod.main(["--niche", "not_a_real_niche"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "must be 'all' or one of" in err

    def test_valid_single_niche_ok(self, monkeypatch) -> None:
        with patch(
            "genlab_core.compliance.policy_block_rca.analyze_recent_policy_blocks",
            return_value=[],
        ):
            rc = mod.main(["--niche", "gaming"])
        assert rc == 0

    def test_all_niche_iterates_five(self, monkeypatch) -> None:
        with patch(
            "genlab_core.compliance.policy_block_rca.analyze_recent_policy_blocks",
            return_value=[],
        ) as analyze:
            rc = mod.main(["--niche", "all"])
        assert rc == 0
        assert analyze.call_count == 5
        # Called once per canonical niche
        call_niches = {c.args[0] for c in analyze.call_args_list}
        assert call_niches == {"ai_creators", "gaming", "sports", "movies", "anime"}


class TestFlagForcing:
    def test_flag_is_set_before_analyze_call(self, monkeypatch) -> None:
        """CLI must set GENLAB_POLICY_BLOCK_RCA_ENABLED=1 BEFORE
        calling analyze — otherwise the analyze call sees flag OFF
        and returns [] immediately without ever hitting the LLM."""
        monkeypatch.delenv("GENLAB_POLICY_BLOCK_RCA_ENABLED", raising=False)

        captured_flag: list[str | None] = []

        def _fake_analyze(*_args, **_kwargs):
            # Capture whatever the flag value was AT call time
            captured_flag.append(
                os.environ.get("GENLAB_POLICY_BLOCK_RCA_ENABLED")
            )
            return []

        with patch(
            "genlab_core.compliance.policy_block_rca.analyze_recent_policy_blocks",
            side_effect=_fake_analyze,
        ):
            mod.main(["--niche", "gaming"])

        assert captured_flag == ["1"], (
            f"Flag was {captured_flag[0]!r} at analyze call time; "
            "CLI failed to force-enable it"
        )

    def test_flag_forced_even_if_already_off(self, monkeypatch) -> None:
        """Explicit '0' in env must be overridden to '1'."""
        monkeypatch.setenv("GENLAB_POLICY_BLOCK_RCA_ENABLED", "0")

        captured_flag: list[str | None] = []

        def _fake_analyze(*_args, **_kwargs):
            captured_flag.append(
                os.environ.get("GENLAB_POLICY_BLOCK_RCA_ENABLED")
            )
            return []

        with patch(
            "genlab_core.compliance.policy_block_rca.analyze_recent_policy_blocks",
            side_effect=_fake_analyze,
        ):
            mod.main(["--niche", "gaming"])

        assert captured_flag == ["1"]


class TestArgForwarding:
    def test_window_days_and_min_samples_forwarded(self) -> None:
        with patch(
            "genlab_core.compliance.policy_block_rca.analyze_recent_policy_blocks",
            return_value=[],
        ) as analyze:
            mod.main(["--niche", "gaming", "--window-days", "60", "--min-samples", "1"])

        analyze.assert_called_once()
        kwargs = analyze.call_args.kwargs
        assert kwargs["window_days"] == 60
        assert kwargs["min_samples"] == 1

    def test_defaults_are_30_and_3(self) -> None:
        with patch(
            "genlab_core.compliance.policy_block_rca.analyze_recent_policy_blocks",
            return_value=[],
        ) as analyze:
            mod.main(["--niche", "gaming"])

        kwargs = analyze.call_args.kwargs
        assert kwargs["window_days"] == 30
        assert kwargs["min_samples"] == 3


class TestOutputFormatting:
    def test_no_verdicts_prints_note(self, capsys) -> None:
        with patch(
            "genlab_core.compliance.policy_block_rca.analyze_recent_policy_blocks",
            return_value=[],
        ):
            mod.main(["--niche", "gaming"])
        out = capsys.readouterr().out
        assert "no verdicts" in out
        assert "NOTE" in out

    def test_verdict_output_includes_category_and_patterns(self, capsys) -> None:
        from genlab_core.compliance.policy_block_rca import RCAVerdict

        verdict = RCAVerdict(
            violation_category="spam_signals",
            confidence=0.85,
            avoid_patterns=["avoid stacking CTAs", "avoid >5 hashtags"],
            sample_blueprint_ids=["b1", "b2"],
        )
        with patch(
            "genlab_core.compliance.policy_block_rca.analyze_recent_policy_blocks",
            return_value=[verdict],
        ):
            mod.main(["--niche", "gaming"])
        out = capsys.readouterr().out
        assert "spam_signals" in out
        assert "0.85" in out
        assert "avoid stacking CTAs" in out
        assert "avoid >5 hashtags" in out
        assert "b1" in out or "b2" in out
