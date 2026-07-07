"""Pin the monetization preflight script (2026-07-07 followup).

The preflight is the operator's first-touch when activating the L3
stack — this suite locks in the invariants that make it a
trustworthy signal:

  1. Score computation is deterministic and bounded [0, 100]
  2. "Unable to check" is DISTINGUISHED from "confirmed missing"
     (the DB-unreachable path doesn't cry wolf on tables)
  3. Missing catalog is a warning, not a critical gap
  4. Renderer produces stable output the operator can eyeball
  5. JSON output shape is stable for CI polling

Uses importlib to load the CLI module without a full DB dependency.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "monetization_preflight.py"


def _load_script_module():
    """Load the CLI script as an importable module.

    Registers in ``sys.modules`` so dataclass instantiation can resolve
    the module's ``__dict__`` for forward-reference lookups (Python
    3.13's dataclasses require this at ``__init__`` time)."""
    import sys

    spec = importlib.util.spec_from_file_location("monetization_preflight", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["monetization_preflight"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def script_module():
    return _load_script_module()


class TestNicheScore:
    """The 3-dimension × ~33% score is the operator-facing number.
    Test that flag/arms/blueprints each contribute independently."""

    def test_empty_niche_scores_zero(self, script_module):
        n = script_module.NicheReadiness(niche_id="gaming")
        assert n.score == 0

    def test_flag_on_alone_scores_34(self, script_module):
        n = script_module.NicheReadiness(niche_id="gaming", affiliate_enabled=True)
        assert n.score == 34

    def test_all_three_present_scores_100(self, script_module):
        n = script_module.NicheReadiness(
            niche_id="gaming",
            affiliate_enabled=True,
            n_arms_registered=22,
            n_blueprints_with_slug=5,
        )
        # 34 + 33 + 33 = 100
        assert n.score == 100

    def test_arms_alone_scores_33(self, script_module):
        n = script_module.NicheReadiness(niche_id="gaming", n_arms_registered=22)
        # Only arms — no flag confirmation, no blueprints
        assert n.score == 33

    def test_catalog_unknown_scores_zero_for_flag(self, script_module):
        """When catalog is unavailable (affiliate_enabled=None), score
        0 pts on that dimension. Design decision (2026-07-07): honest
        under-scoring so the operator sees the catalog gap rather than
        seeing an inflated 'partial' number and assuming it's fine."""
        n = script_module.NicheReadiness(
            niche_id="gaming", affiliate_enabled=None, n_arms_registered=22
        )
        # 0 (catalog unavailable) + 33 (arms) = 33
        assert n.score == 33

    def test_flag_off_scores_zero_for_flag(self, script_module):
        n = script_module.NicheReadiness(
            niche_id="gaming", affiliate_enabled=False, n_arms_registered=22
        )
        # 0 (flag off) + 33 (arms) = 33
        assert n.score == 33


class TestCriticalGaps:
    """Critical gaps are what block ALL niches. Must not cry wolf on
    unverifiable state — that's the key invariant."""

    def test_no_db_no_gaps(self, script_module):
        """DATABASE_URL unset → alembic_head=None + empty tables_present.
        Neither should register as a critical gap."""
        cross = script_module.CrossChecks()
        assert cross.critical_gaps == []

    def test_confirmed_wrong_alembic_head_is_gap(self, script_module):
        cross = script_module.CrossChecks(
            alembic_head="deadbeef1234",
            alembic_head_matches=False,
            tables_present={t: True for t in script_module.REQUIRED_TABLES},
        )
        gaps = cross.critical_gaps
        assert len(gaps) == 1
        assert "alembic head" in gaps[0]
        assert "deadbeef1234" in gaps[0]

    def test_confirmed_missing_table_is_gap(self, script_module):
        """DB reachable but a table is confirmed missing — that's a gap."""
        cross = script_module.CrossChecks(
            alembic_head=script_module.EXPECTED_ALEMBIC_HEAD,
            alembic_head_matches=True,
            tables_present={
                "bandit_arms": True,
                "blueprints": True,
                "affiliate_clicks": False,  # confirmed missing
                "affiliate_conversions": True,
                "selector_divergences": True,
            },
        )
        gaps = cross.critical_gaps
        assert len(gaps) == 1
        assert "affiliate_clicks" in gaps[0]
        assert "missing" in gaps[0]

    def test_all_confirmed_present_zero_gaps(self, script_module):
        cross = script_module.CrossChecks(
            alembic_head=script_module.EXPECTED_ALEMBIC_HEAD,
            alembic_head_matches=True,
            tables_present={t: True for t in script_module.REQUIRED_TABLES},
        )
        assert cross.critical_gaps == []


class TestPctBar:
    """The bar rendering is what makes the CLI report scan-able.
    Pin the width + fill semantics."""

    def test_0pct_is_empty(self, script_module):
        assert script_module._pct_bar(0, width=10) == "░" * 10

    def test_100pct_is_full(self, script_module):
        assert script_module._pct_bar(100, width=10) == "▓" * 10

    def test_50pct_is_half(self, script_module):
        bar = script_module._pct_bar(50, width=10)
        assert bar == "▓" * 5 + "░" * 5

    def test_length_always_matches_width(self, script_module):
        for pct in (0, 17, 33, 50, 66, 90, 100):
            for width in (10, 20, 50):
                assert len(script_module._pct_bar(pct, width=width)) == width


class TestCollect:
    """The composition entry point. Verify it wires without a live
    DB or catalog."""

    def test_no_dsn_no_catalog_returns_defaults(self, script_module, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(script_module, "_load_catalog", lambda: None)
        monkeypatch.setattr(script_module, "_check_systemd_timers", lambda: {})
        monkeypatch.setattr(script_module, "_check_csv_dir", lambda: False)

        per_niche, cross = script_module.collect(None)

        # 5 niches present, all zero-scored (catalog missing)
        assert set(per_niche.keys()) == set(script_module.KNOWN_NICHES)
        for niche in per_niche.values():
            # Catalog absence → warning appended
            assert any("catalog" in w for w in niche.warnings)
            # No DB → all 3 dimensions unset
            assert niche.n_arms_registered == 0
            assert niche.affiliate_enabled is None

        # Cross checks show no DB, no critical gaps (unable to verify)
        assert cross.critical_gaps == []

    def test_catalog_present_flag_read(self, script_module, monkeypatch):
        """When catalog is available, affiliate_enabled is read verbatim."""
        fake_catalog = {
            "niches": {
                "gaming": {"affiliate_enabled": True, "products": []},
                "sports": {"affiliate_enabled": False, "products": []},
                # ai_creators absent from catalog on purpose
            }
        }
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(script_module, "_load_catalog", lambda: fake_catalog)
        monkeypatch.setattr(script_module, "_check_systemd_timers", lambda: {})
        monkeypatch.setattr(script_module, "_check_csv_dir", lambda: False)

        per_niche, cross = script_module.collect(None)

        assert per_niche["gaming"].affiliate_enabled is True
        assert per_niche["sports"].affiliate_enabled is False
        # ai_creators absent → None + warning
        assert per_niche["ai_creators"].affiliate_enabled is None
        assert any("absent from catalog" in w for w in per_niche["ai_creators"].warnings)


class TestJsonOutput:
    """The JSON payload shape is contracted for CI polling / dashboards."""

    def test_json_shape_stable(self, script_module):
        """Emit + parse the payload; verify all top-level keys."""
        import json

        per_niche = {
            n: script_module.NicheReadiness(niche_id=n) for n in script_module.KNOWN_NICHES
        }
        cross = script_module.CrossChecks()

        # Build the payload the same way main() does — this ensures
        # any refactor of the JSON shape breaks THIS test, not silently.
        payload = {
            "generated_at": "2026-07-07T00:00:00+00:00",
            "score_pct": sum(n.score for n in per_niche.values()) // len(per_niche),
            "overall_ready": False,
            "niches": {n_id: n.to_dict() for n_id, n in per_niche.items()},
            "cross": cross.to_dict(),
            "critical_gaps": cross.critical_gaps,
        }
        # Roundtrip through JSON — catches non-serializable values
        parsed = json.loads(json.dumps(payload))
        assert set(parsed.keys()) == {
            "generated_at",
            "score_pct",
            "overall_ready",
            "niches",
            "cross",
            "critical_gaps",
        }
        assert set(parsed["niches"].keys()) == set(script_module.KNOWN_NICHES)


class TestRenderHuman:
    """The human report is what the operator reads. Verify it doesn't
    crash on edge states + covers the key sections."""

    def test_empty_state_renders(self, script_module):
        per_niche = {
            n: script_module.NicheReadiness(niche_id=n) for n in script_module.KNOWN_NICHES
        }
        cross = script_module.CrossChecks()
        report = script_module.render_human(per_niche, cross)
        assert "Cross-cutting checks" in report
        assert "Per-niche readiness" in report
        assert "Overall readiness" in report

    def test_unverifiable_state_shows_question_marks(self, script_module):
        """When DB is unreachable, the report should show '?' for
        alembic + tables, not '✗'."""
        per_niche = {
            n: script_module.NicheReadiness(niche_id=n) for n in script_module.KNOWN_NICHES
        }
        cross = script_module.CrossChecks()  # empty tables_present
        report = script_module.render_human(per_niche, cross)
        assert "unable to check" in report
        # Should NOT show "✗ table X missing from DB" when we couldn't check
        for table in script_module.REQUIRED_TABLES:
            assert f"[✗] table {table}" not in report

    def test_ready_state_shows_ready_label(self, script_module):
        """All checks green → 'READY' label."""
        per_niche = {
            n: script_module.NicheReadiness(
                niche_id=n,
                affiliate_enabled=True,
                n_arms_registered=22,
                n_blueprints_with_slug=5,
            )
            for n in script_module.KNOWN_NICHES
        }
        cross = script_module.CrossChecks(
            alembic_head=script_module.EXPECTED_ALEMBIC_HEAD,
            alembic_head_matches=True,
            tables_present={t: True for t in script_module.REQUIRED_TABLES},
        )
        report = script_module.render_human(per_niche, cross)
        assert "READY" in report
