"""Pin: bayesian_gate SQL JOIN casts blueprints.id to text.

The first prod run of the Bayesian gate refit (2026-06-26) failed
silently — all 5 niches reported 0 rows because the SQL JOIN
``LEFT JOIN blueprints b ON b.id = c.blueprint_id`` errored with
``operator does not exist: uuid = text``. blueprints.id is UUID
(per the table schema) but auto_approval_calibration.blueprint_id
is TEXT (per migration o5j6k7l8m9n0 which intentionally allows
synthetic test IDs like ``smoke-test-id``).

This pin catches the exact regression: any future revert that
removes the ``::text`` cast breaks the JOIN silently again.
"""

from __future__ import annotations

from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parents[2] / "src" / "genlab_core" / "learning" / "bayesian_gate.py"
)


class TestBayesianGateJoinCast:
    def test_join_casts_blueprints_id_to_text(self):
        content = SOURCE.read_text()
        # The cast must be present — otherwise the JOIN errors at
        # runtime (uuid = text) and silently returns 0 rows for
        # every niche.
        assert "b.id::text = c.blueprint_id" in content, (
            "bayesian_gate.py JOIN must cast blueprints.id to text. "
            "Without it, Postgres errors with 'operator does not "
            "exist: uuid = text' and the refit silently fails for "
            "every niche."
        )

    def test_no_unqualified_join_remains(self):
        """Catch the regression directly: any line with the
        unqualified JOIN expression is wrong."""
        content = SOURCE.read_text()
        for line in content.splitlines():
            # Allow comments mentioning the legacy form
            if line.strip().startswith("--"):
                continue
            assert "b.id = c.blueprint_id" not in line, (
                f"bayesian_gate.py has a non-comment line with the un-cast JOIN: {line!r}"
            )
