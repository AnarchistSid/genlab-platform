"""Tests for ``scripts/auto_ramp_auto2.py`` — AUTO #2 weekly ramp.

Covers operator decision #3 (2026-06-30): a niche's
``publishing.yaml > auto_publish > rollout_pct`` should auto-bump by
+10% per qualifying week when the bandit_validation harness reports
'bandit useful' AND the calibration logger reports > 90% gate-vs-
operator agreement.

The DB-touching helpers (``_fetch_calibration_stats``,
``_fetch_latest_validation``) are stubbed via the
``calibration_fn`` / ``validation_fn`` parameters that
``evaluate_niche`` and ``run`` accept; no live DB needed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# Load the script as a module — it lives outside the package tree.
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "auto_ramp_auto2.py"
_spec = importlib.util.spec_from_file_location("auto_ramp_auto2", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["auto_ramp_auto2"] = mod
_spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


_PUBLISHING_TEMPLATE = """\
# Test publishing.yaml — comments below must survive any ramp write.

# OPERATOR NOTE — DO NOT delete this comment.
auto_publish:
  # Inline comment above rollout_pct (preservation pin).
  enabled: true
  min_confidence: 0.85
  max_approvals_per_pass: 3
  rollout_pct: {pct}
"""


def _write_publishing_yaml(tmp: Path, niche_id: str, pct: float) -> Path:
    """Drop a publishing.yaml under a fake niche dir tree."""
    dir_name = mod.NICHE_DIR_NAMES[niche_id]
    cfg_dir = tmp / dir_name / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "publishing.yaml"
    path.write_text(_PUBLISHING_TEMPLATE.format(pct=pct))
    return path


def _bootstrap_fake_root(tmp: Path, pcts: dict[str, float]) -> Path:
    """Create publishing.yaml files for each niche in ``pcts``.

    Niches NOT present in ``pcts`` get a missing publishing.yaml so
    the script's ``read_rollout_pct`` returns 0.0 / None — handy
    for "what happens when the file's gone" cases.
    """
    for niche_id, pct in pcts.items():
        _write_publishing_yaml(tmp, niche_id, pct)
    return tmp


def _stub_calibration(per_niche: dict[str, tuple[float, int]]):
    """Build a calibration_fn stub from {niche_id: (rate, n)}."""

    def _fn(niche_id: str) -> tuple[float, int]:
        return per_niche.get(niche_id, (0.0, 0))

    return _fn


def _stub_validation(per_niche: dict[str, tuple[str, datetime | None]]):
    """Build a validation_fn stub from {niche_id: (interp, computed_at)}."""

    def _fn(niche_id: str) -> tuple[str, datetime | None]:
        return per_niche.get(niche_id, ("low signal", None))

    return _fn


# ---------------------------------------------------------------------------
# Test 1 — qualifying niche ramps (apply mode)
# ---------------------------------------------------------------------------


class TestQualifyingRamp:
    def test_apply_bumps_rollout_pct(self, tmp_path):
        root = _bootstrap_fake_root(tmp_path, {"ai_creators": 0.10})
        now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        summary = mod.run(
            niche_filter=["ai_creators"],
            max_rollout_pct=0.50,
            apply=True,
            genlab_root=root,
            now=now,
            calibration_fn=_stub_calibration({"ai_creators": (0.95, 50)}),
            validation_fn=_stub_validation(
                {"ai_creators": ("bandit useful", now - timedelta(hours=2))}
            ),
        )
        assert summary["ramped"] == ["ai_creators"]
        assert summary["applied"] is True
        # YAML now has rollout_pct=0.2.
        pub = root / "BlackboxBrief" / "config" / "publishing.yaml"
        text = pub.read_text()
        assert "rollout_pct: 0.2" in text
        # The OPERATOR NOTE comment survived the write.
        assert "OPERATOR NOTE — DO NOT delete" in text
        # Audit log written.
        audit = root / ".tmp" / "auto2_ramp_log.jsonl"
        assert audit.exists()
        row = json.loads(audit.read_text().strip().splitlines()[-1])
        assert row["niche_id"] == "ai_creators"
        assert row["from_pct"] == 0.10
        assert row["to_pct"] == 0.20
        assert row["agreement_rate"] == 0.95


# ---------------------------------------------------------------------------
# Test 2 — agreement below threshold → no ramp
# ---------------------------------------------------------------------------


class TestAgreementGate:
    def test_low_agreement_skips(self, tmp_path):
        root = _bootstrap_fake_root(tmp_path, {"ai_creators": 0.10})
        now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        summary = mod.run(
            niche_filter=["ai_creators"],
            apply=True,
            genlab_root=root,
            now=now,
            calibration_fn=_stub_calibration({"ai_creators": (0.85, 50)}),  # below 0.90
            validation_fn=_stub_validation(
                {"ai_creators": ("bandit useful", now - timedelta(hours=2))}
            ),
        )
        assert summary["ramped"] == []
        assert summary["skipped"] == ["ai_creators"]
        # YAML unchanged.
        pub = root / "BlackboxBrief" / "config" / "publishing.yaml"
        assert "rollout_pct: 0.1" in pub.read_text()


# ---------------------------------------------------------------------------
# Test 3 — interpretation != "bandit useful" → no ramp
# ---------------------------------------------------------------------------


class TestInterpretationGate:
    @pytest.mark.parametrize("interp", ["low signal", "developing", "bandit ineffective"])
    def test_non_useful_interpretation_skips(self, tmp_path, interp):
        root = _bootstrap_fake_root(tmp_path, {"ai_creators": 0.10})
        now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        summary = mod.run(
            niche_filter=["ai_creators"],
            apply=True,
            genlab_root=root,
            now=now,
            calibration_fn=_stub_calibration({"ai_creators": (0.95, 50)}),
            validation_fn=_stub_validation({"ai_creators": (interp, now - timedelta(hours=2))}),
        )
        assert summary["ramped"] == []
        # YAML unchanged.
        pub = root / "BlackboxBrief" / "config" / "publishing.yaml"
        assert "rollout_pct: 0.1" in pub.read_text()


# ---------------------------------------------------------------------------
# Test 4 — at cap → no ramp
# ---------------------------------------------------------------------------


class TestAtCap:
    def test_already_at_cap_skips(self, tmp_path):
        root = _bootstrap_fake_root(tmp_path, {"ai_creators": 0.50})
        now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        summary = mod.run(
            niche_filter=["ai_creators"],
            max_rollout_pct=0.50,
            apply=True,
            genlab_root=root,
            now=now,
            calibration_fn=_stub_calibration({"ai_creators": (0.95, 50)}),
            validation_fn=_stub_validation(
                {"ai_creators": ("bandit useful", now - timedelta(hours=2))}
            ),
        )
        assert summary["ramped"] == []
        # Decision rationale names the cap.
        decision = summary["decisions"][0]
        assert "at/above cap" in decision["reason"]

    def test_increment_clamped_to_cap(self, tmp_path):
        # current=0.45 → naive +0.10 would be 0.55 > 0.50 cap → ramp to 0.50.
        root = _bootstrap_fake_root(tmp_path, {"ai_creators": 0.45})
        now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        summary = mod.run(
            niche_filter=["ai_creators"],
            max_rollout_pct=0.50,
            apply=True,
            genlab_root=root,
            now=now,
            calibration_fn=_stub_calibration({"ai_creators": (0.95, 50)}),
            validation_fn=_stub_validation(
                {"ai_creators": ("bandit useful", now - timedelta(hours=2))}
            ),
        )
        assert summary["ramped"] == ["ai_creators"]
        pub = root / "BlackboxBrief" / "config" / "publishing.yaml"
        text = pub.read_text()
        # ruamel may render 0.5 or 0.50 — check semantic value via re-load.
        from ruamel.yaml import YAML

        doc = YAML(typ="rt").load(text)
        assert doc["auto_publish"]["rollout_pct"] == 0.50


# ---------------------------------------------------------------------------
# Test 5 — dry-run never writes
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_modify_yaml_or_audit(self, tmp_path):
        root = _bootstrap_fake_root(tmp_path, {"ai_creators": 0.10})
        now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        summary = mod.run(
            niche_filter=["ai_creators"],
            apply=False,
            genlab_root=root,
            now=now,
            calibration_fn=_stub_calibration({"ai_creators": (0.95, 50)}),
            validation_fn=_stub_validation(
                {"ai_creators": ("bandit useful", now - timedelta(hours=2))}
            ),
        )
        # The decision would have ramped, but no write happened.
        assert summary["ramped"] == []
        assert summary["applied"] is False
        assert summary["decisions"][0]["will_ramp"] is True
        # YAML unchanged.
        pub = root / "BlackboxBrief" / "config" / "publishing.yaml"
        assert "rollout_pct: 0.1" in pub.read_text()
        # No audit file written.
        assert not (root / ".tmp" / "auto2_ramp_log.jsonl").exists()


# ---------------------------------------------------------------------------
# Safety — stale validation data → no ramp
# ---------------------------------------------------------------------------


class TestStaleValidation:
    def test_validation_older_than_48h_skips(self, tmp_path):
        root = _bootstrap_fake_root(tmp_path, {"ai_creators": 0.10})
        now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        summary = mod.run(
            niche_filter=["ai_creators"],
            apply=True,
            genlab_root=root,
            now=now,
            calibration_fn=_stub_calibration({"ai_creators": (0.95, 50)}),
            # 49h old > 48h freshness window.
            validation_fn=_stub_validation(
                {"ai_creators": ("bandit useful", now - timedelta(hours=49))}
            ),
        )
        assert summary["ramped"] == []
        assert "stale" in summary["decisions"][0]["reason"]

    def test_no_validation_row_skips(self, tmp_path):
        root = _bootstrap_fake_root(tmp_path, {"ai_creators": 0.10})
        now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        summary = mod.run(
            niche_filter=["ai_creators"],
            apply=True,
            genlab_root=root,
            now=now,
            calibration_fn=_stub_calibration({"ai_creators": (0.95, 50)}),
            validation_fn=_stub_validation({"ai_creators": ("bandit useful", None)}),
        )
        assert summary["ramped"] == []
        assert "no validation row" in summary["decisions"][0]["reason"]


# ---------------------------------------------------------------------------
# Safety — per-run cap of 2 ramps
# ---------------------------------------------------------------------------


class TestPerRunCap:
    def test_three_qualifiers_only_two_ramp(self, tmp_path):
        # Three niches all qualify; only 2 ramp this run.
        root = _bootstrap_fake_root(
            tmp_path,
            {"ai_creators": 0.10, "gaming": 0.10, "sports": 0.10},
        )
        now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        # All three pass gates; sample_count breaks the tie so the top
        # two by n are picked (anime=80, sports=60 win; ai_creators=40
        # is deferred).
        summary = mod.run(
            niche_filter=["ai_creators", "gaming", "sports"],
            apply=True,
            genlab_root=root,
            now=now,
            calibration_fn=_stub_calibration(
                {
                    "ai_creators": (0.95, 40),
                    "gaming": (0.95, 80),
                    "sports": (0.95, 60),
                }
            ),
            validation_fn=_stub_validation(
                {
                    "ai_creators": ("bandit useful", now - timedelta(hours=2)),
                    "gaming": ("bandit useful", now - timedelta(hours=2)),
                    "sports": ("bandit useful", now - timedelta(hours=2)),
                }
            ),
        )
        # Highest sample_counts win.
        assert set(summary["ramped"]) == {"gaming", "sports"}
        assert summary["deferred"] == ["ai_creators"]
        # The deferred niche's YAML was NOT touched.
        pub = root / "BlackboxBrief" / "config" / "publishing.yaml"
        assert "rollout_pct: 0.1" in pub.read_text()


# ---------------------------------------------------------------------------
# YAML preservation pin
# ---------------------------------------------------------------------------


class TestYamlPreservation:
    """Pin: ruamel roundtrip preserves comments + blank lines + key order.

    Without ruamel (e.g. if someone swaps in pyyaml.safe_dump), every
    ramp write would silently delete the publishing.yaml runbook
    comments — exactly the failure mode learning/config_updater.py was
    fixed for (T#62, 2026-06-15).
    """

    def test_comments_and_blank_lines_preserved(self, tmp_path):
        root = _bootstrap_fake_root(tmp_path, {"ai_creators": 0.10})
        now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        mod.run(
            niche_filter=["ai_creators"],
            apply=True,
            genlab_root=root,
            now=now,
            calibration_fn=_stub_calibration({"ai_creators": (0.95, 50)}),
            validation_fn=_stub_validation(
                {"ai_creators": ("bandit useful", now - timedelta(hours=2))}
            ),
        )
        pub = root / "BlackboxBrief" / "config" / "publishing.yaml"
        text = pub.read_text()
        # All 3 comments survive (header, OPERATOR NOTE, inline).
        assert "# Test publishing.yaml" in text
        assert "OPERATOR NOTE — DO NOT delete" in text
        assert "preservation pin" in text
        # Other keys preserved in order.
        assert text.index("enabled:") < text.index("min_confidence:")
        assert text.index("min_confidence:") < text.index("max_approvals_per_pass:")
        assert text.index("max_approvals_per_pass:") < text.index("rollout_pct:")

    def test_value_round_trips_via_ruamel(self, tmp_path):
        """The written rollout_pct loads back as 0.2 (not 0.19999...)."""
        from ruamel.yaml import YAML

        root = _bootstrap_fake_root(tmp_path, {"ai_creators": 0.10})
        now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        mod.run(
            niche_filter=["ai_creators"],
            apply=True,
            genlab_root=root,
            now=now,
            calibration_fn=_stub_calibration({"ai_creators": (0.95, 50)}),
            validation_fn=_stub_validation(
                {"ai_creators": ("bandit useful", now - timedelta(hours=2))}
            ),
        )
        pub = root / "BlackboxBrief" / "config" / "publishing.yaml"
        doc = YAML(typ="rt").load(pub.read_text())
        # Float comparison with small tolerance — ruamel preserves the
        # operator-written form; +0.10 increment on 0.10 should be 0.20.
        assert abs(doc["auto_publish"]["rollout_pct"] - 0.20) < 1e-9


# ---------------------------------------------------------------------------
# CLI plumbing — --niche-id filter, --max-rollout-pct override
# ---------------------------------------------------------------------------


class TestCliFiltering:
    def test_niche_filter_limits_evaluation(self, tmp_path):
        root = _bootstrap_fake_root(
            tmp_path,
            {"ai_creators": 0.10, "gaming": 0.10},
        )
        now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        summary = mod.run(
            niche_filter=["ai_creators"],
            apply=False,
            genlab_root=root,
            now=now,
            calibration_fn=_stub_calibration(
                {
                    "ai_creators": (0.95, 50),
                    "gaming": (0.95, 50),
                }
            ),
            validation_fn=_stub_validation(
                {
                    "ai_creators": ("bandit useful", now - timedelta(hours=2)),
                    "gaming": ("bandit useful", now - timedelta(hours=2)),
                }
            ),
        )
        # Only ai_creators evaluated.
        assert len(summary["decisions"]) == 1
        assert summary["decisions"][0]["niche_id"] == "ai_creators"

    def test_max_rollout_pct_override_lowers_cap(self, tmp_path):
        # current=0.05, cap=0.10 — qualifying niche bumps to 0.10 exactly.
        root = _bootstrap_fake_root(tmp_path, {"ai_creators": 0.05})
        now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        summary = mod.run(
            niche_filter=["ai_creators"],
            max_rollout_pct=0.10,
            apply=True,
            genlab_root=root,
            now=now,
            calibration_fn=_stub_calibration({"ai_creators": (0.95, 50)}),
            validation_fn=_stub_validation(
                {"ai_creators": ("bandit useful", now - timedelta(hours=2))}
            ),
        )
        assert summary["ramped"] == ["ai_creators"]
        from ruamel.yaml import YAML

        pub = root / "BlackboxBrief" / "config" / "publishing.yaml"
        doc = YAML(typ="rt").load(pub.read_text())
        # Clamped to cap (0.05 + 0.10 = 0.15 > 0.10 → 0.10).
        assert abs(doc["auto_publish"]["rollout_pct"] - 0.10) < 1e-9
