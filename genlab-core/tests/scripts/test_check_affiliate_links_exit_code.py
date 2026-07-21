"""Pin tests for `check_affiliate_links.py` exit-code semantics (2026-07-21).

Class-of-bug: rule #26 (partial-success returning non-zero fires systemd
alarm cascade). Prior behaviour was `exit 1 on ANY broken link` which
made `service_down` CRITICAL fire every hour despite 78/80 links being
healthy — normal state given operator-known dead URLs.

New semantic: threshold-based exit (>=10% broken = incident-worthy).
Broken URLs still fully reported via stdout so operator sees them.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "genlab-core"
    / "scripts"
    / "check_affiliate_links.py"
)
spec = importlib.util.spec_from_file_location("check_affiliate_links", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules["check_affiliate_links"] = mod
spec.loader.exec_module(mod)


class TestExitCodeThreshold:
    def test_docstring_documents_threshold_semantic(self):
        """Docstring must document the threshold-based exit code to
        prevent future maintainers from reverting to the old
        `exit 1 on any broken` shape."""
        assert "10%" in mod.__doc__
        assert "rule #26" in mod.__doc__.lower()

    def test_source_uses_threshold_constant(self):
        """The threshold (0.10) must be a named constant, not a
        magic number, so future tuning is explicit."""
        import inspect

        src = inspect.getsource(mod.main)
        assert "BROKEN_RATE_THRESHOLD" in src, (
            "threshold must be a named constant (not magic 0.10)"
        )
        assert "0.10" in src, "threshold value 0.10 must be present"

    def test_source_exits_0_below_threshold(self):
        """Below-threshold branch must sys.exit(0), not exit 1."""
        import inspect

        src = inspect.getsource(mod.main)
        # Grep for the below-threshold-exit comment then verify the
        # nearby exit code
        assert "below" in src.lower() and "threshold" in src.lower()
        # Count exit-0 vs exit-1 calls; must have >= 2 exit(0) after fix
        # (the "no broken" branch + the "below threshold" branch).
        assert src.count("sys.exit(0)") >= 2, (
            "must have at least 2 sys.exit(0) sites — the all-healthy "
            "branch + the below-threshold branch"
        )

    def test_source_exits_1_at_or_above_threshold(self):
        """At-or-above-threshold branch must still exit 1 so genuine
        outages still fire the alarm."""
        import inspect

        src = inspect.getsource(mod.main)
        assert "sys.exit(1)" in src, (
            "must retain a sys.exit(1) path for the >=10% incident case"
        )
