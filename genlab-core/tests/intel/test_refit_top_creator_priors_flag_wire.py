"""Pin: refit_top_creator_priors gates on PRODUCER flag, not consumer flag.

2026-07-16 audit finding + fix:
    The script originally gated on ``GENLAB_TOP_CREATOR_PRIORS_ENABLED``
    but that's the CONSUMER-side flag (per CLAUDE.md: "off until
    correlations mature over ≥2 weeks"). Producer + consumer sharing
    one flag created a deadlock — producer waited for consumer OK,
    consumer waited for correlations to mature, correlations never
    computed, artifact directory stayed empty for the full window.

Fix locks the producer to ``GENLAB_TOP_CREATORS_ENABLED`` (already
activated on prod 2026-07-14 per CLAUDE.md's A.2 note). Consumer
still gates on the original flag.

Any refactor that reverts the flag choice back to the consumer flag
re-creates the deadlock. This pin fails loudly in that case.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "refit_top_creator_priors.py"


@pytest.fixture(scope="module")
def refit_module():
    """Load the script as a module for direct function access."""
    spec = importlib.util.spec_from_file_location(
        "refit_top_creator_priors_pin", _SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["refit_top_creator_priors_pin"] = module
    spec.loader.exec_module(module)
    return module


class TestFlagWire:
    def test_flag_enabled_reads_producer_flag(self, refit_module, monkeypatch):
        """Producer flag set → _flag_enabled returns True."""
        monkeypatch.setenv("GENLAB_TOP_CREATORS_ENABLED", "true")
        monkeypatch.delenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", raising=False)
        assert refit_module._flag_enabled() is True, (
            "producer flag GENLAB_TOP_CREATORS_ENABLED=true must enable the "
            "runner. If this fails, a refactor may have reverted to gating "
            "on the consumer flag — see 2026-07-16 audit deadlock fix."
        )

    def test_flag_enabled_does_not_gate_on_consumer_flag(self, refit_module, monkeypatch):
        """The KEY invariant. Consumer flag set + producer flag UNSET
        must return False. If this fails, the deadlock is back:
        producer would need the consumer flag on, but consumer wants
        correlations mature first, so nothing ever runs."""
        monkeypatch.delenv("GENLAB_TOP_CREATORS_ENABLED", raising=False)
        monkeypatch.setenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", "true")
        assert refit_module._flag_enabled() is False, (
            "The runner should NOT gate on GENLAB_TOP_CREATOR_PRIORS_ENABLED "
            "(consumer flag). Doing so re-creates the 2026-07-16 audit "
            "deadlock where correlations never compute. Gate on "
            "GENLAB_TOP_CREATORS_ENABLED (producer flag) instead."
        )

    def test_flag_enabled_exact_match_true(self, refit_module, monkeypatch):
        """Exact-match ``true`` per the intelligence-package convention.
        ``"1"`` / ``"yes"`` / ``"TRUE"`` must NOT match — this ensures
        operator sees explicit intent, not lucky truthy conversion."""
        for wrong_value in ("1", "yes", "TRUE", "on", "y"):
            monkeypatch.setenv("GENLAB_TOP_CREATORS_ENABLED", wrong_value)
            monkeypatch.delenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", raising=False)
            assert refit_module._flag_enabled() is False, (
                f"'{wrong_value}' should not activate — only exact 'true' matches"
            )

    def test_flag_enabled_default_false(self, refit_module, monkeypatch):
        """No env vars set → runner is a no-op. Default-off matches
        the discipline of every other intervention runner."""
        monkeypatch.delenv("GENLAB_TOP_CREATORS_ENABLED", raising=False)
        monkeypatch.delenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", raising=False)
        assert refit_module._flag_enabled() is False
