"""Pins for AUTO #2 — auto-approval enforcement worker.

Most important invariants:
  1. **DEFAULT IS NO-OP**: a fresh worker run with no YAML changes and
     no env vars approves zero blueprints. This pin must NEVER fail
     without explicit owner intent — it's the "no surprise auto-publish"
     guarantee.
  2. Kill switch wins over YAML — operator can globally halt approvals
     without editing 5 files.
  3. Idempotency — re-running the worker never double-approves.
  4. Per-pass cap respected — a misconfigured gate can't approve all
     blueprints in one pass.
  5. Low confidence skipped — even if gate says approved, sub-threshold
     confidence still blocks the approval.
  6. Source tag set on every auto-approval so calibration logger can
     exclude these from the confusion matrix.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.scheduling.auto_approval_gate import AutoApprovalDecision
from genlab_core.scheduling.auto_approver import (
    AUTO_APPROVAL_SOURCE_TAG,
    AutoApprovalPolicy,
    load_policy,
    run_pass,
)


def _decision(approved: bool = True, confidence: float = 0.9) -> AutoApprovalDecision:
    return AutoApprovalDecision(
        approved=approved,
        confidence=confidence,
        passed_checks=["has_video", "has_hook"] if approved else ["has_video"],
        failed_checks=[] if approved else ["composite_score"],
        reasons=["ok"],
    )


def _stub_client(blueprints: list[dict]) -> MagicMock:
    client = MagicMock()
    client.blueprints.all.return_value = blueprints
    client.blueprints.update.return_value = None
    return client


# ── Safety: default is no-op ──────────────────────────────────────────────


class TestSafetyDefaults:
    def test_disabled_policy_skips_everything_no_db_touch(self, monkeypatch):
        """The headline safety invariant: if the operator hasn't flipped
        the YAML flag, the worker MUST NOT touch the backlog. Tests pass
        a backlog mock; if any update fires, this test fails loudly.
        """
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        # Force load_policy to return a disabled policy regardless of FS
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=AutoApprovalPolicy(enabled=False),
        ):
            client = _stub_client([{"id": "bp1"}])
            result = run_pass("gaming", backlog_client=client)
            assert result.policy_disabled is True
            assert result.auto_approved == []
            client.blueprints.update.assert_not_called()
            # CRITICAL: even the candidate query must NOT run when
            # disabled — we don't want a misconfigured query to crash
            # the worker.
            client.blueprints.all.assert_not_called()

    def test_kill_switch_overrides_enabled_policy(self, monkeypatch):
        """Operator can halt all approvals via env var without touching YAML."""
        monkeypatch.setenv("GENLAB_AUTO_APPROVE_DISABLED", "1")
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=AutoApprovalPolicy(enabled=True, min_confidence=0.0),
        ):
            client = _stub_client([{"id": "bp1", "fields": {"hook_text": "x", "extra": {}}}])
            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=1.0),
            )
            assert result.kill_switch_active is True
            assert result.auto_approved == []
            client.blueprints.update.assert_not_called()

    @pytest.mark.parametrize("value", ["", "0", "false", "False"])
    def test_kill_switch_inactive_for_falsy_values(self, monkeypatch, value):
        """Empty / 0 / false strings DON'T activate the kill switch —
        otherwise leaving the var defined-but-empty in .env would silently
        block approvals."""
        monkeypatch.setenv("GENLAB_AUTO_APPROVE_DISABLED", value)
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=AutoApprovalPolicy(enabled=True, min_confidence=0.0),
        ):
            client = _stub_client([])
            result = run_pass("gaming", backlog_client=client)
            assert result.kill_switch_active is False


# ── Approval gating: only the right blueprints get approved ───────────────


class TestApprovalGating:
    def _enabled_policy(self, **overrides):
        kwargs = dict(enabled=True, min_confidence=0.7, max_approvals_per_pass=10)
        kwargs.update(overrides)
        return AutoApprovalPolicy(**kwargs)

    def test_high_confidence_approved_blueprint_gets_approved(self, monkeypatch):
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._enabled_policy(),
        ):
            client = _stub_client([{"id": "bp1", "fields": {"hook_text": "h", "extra": {}}}])
            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=0.9),
            )
            assert result.auto_approved == ["bp1"]
            client.blueprints.update.assert_called_once()
            # The update payload must carry the source tag + canonical action
            call = client.blueprints.update.call_args
            assert call.args[0] == "bp1"
            update_fields = call.args[1]
            assert update_fields["action_taken"] == "approved"
            assert update_fields["action_taken_source"] == AUTO_APPROVAL_SOURCE_TAG
            assert update_fields["auto_approval_confidence"] == 0.9
            # typecast must be True (matches the dashboard's pattern)
            assert call.kwargs.get("typecast") is True

    def test_gate_rejected_blueprint_not_approved(self, monkeypatch):
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._enabled_policy(),
        ):
            client = _stub_client([{"id": "bp1", "fields": {"hook_text": "h", "extra": {}}}])
            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=False, confidence=1.0),
            )
            assert result.auto_approved == []
            assert result.skipped_gate_rejected == ["bp1"]
            client.blueprints.update.assert_not_called()

    def test_low_confidence_skipped_even_when_gate_approved(self, monkeypatch):
        """Sub-threshold confidence blocks approval — the operator's
        chosen min_confidence is the safety knob."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._enabled_policy(min_confidence=0.95),
        ):
            client = _stub_client([{"id": "bp1", "fields": {"hook_text": "h", "extra": {}}}])
            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=0.80),
            )
            assert result.auto_approved == []
            assert result.skipped_low_confidence == ["bp1"]
            client.blueprints.update.assert_not_called()

    def test_already_actioned_blueprint_is_idempotent(self, monkeypatch):
        """Re-running the worker on a blueprint that already has
        action_taken set must NOT touch it — even if the gate would
        still approve it. This is the safety net against duplicate
        approvals during retry storms."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._enabled_policy(),
        ):
            client = _stub_client(
                [
                    {
                        "id": "bp1",
                        "fields": {
                            "hook_text": "h",
                            "action_taken": "approved",  # already actioned
                            "extra": {},
                        },
                    }
                ]
            )
            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=1.0),
            )
            assert result.auto_approved == []
            assert result.skipped_idempotent == ["bp1"]
            client.blueprints.update.assert_not_called()

    def test_per_pass_cap_limits_blast_radius(self, monkeypatch):
        """A misconfigured gate that approves everything must not be
        able to flood the publishing queue. Cap stops at N regardless
        of how many candidates are eligible."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._enabled_policy(max_approvals_per_pass=2),
        ):
            client = _stub_client(
                [{"id": f"bp{i}", "fields": {"hook_text": "h", "extra": {}}} for i in range(5)]
            )
            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=1.0),
            )
            assert len(result.auto_approved) == 2
            assert result.cap_reached is True
            assert client.blueprints.update.call_count == 2


# ── Dry-run mode: log but don't act ────────────────────────────────────────


class TestDryRun:
    def test_dry_run_records_intent_without_backlog_write(self, monkeypatch):
        """Operator's first runs after flipping the YAML flag use
        --dry-run to confirm the gate's choices look right before going
        live."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=AutoApprovalPolicy(
                enabled=True, min_confidence=0.0, max_approvals_per_pass=10
            ),
        ):
            client = _stub_client([{"id": "bp1", "fields": {"hook_text": "h", "extra": {}}}])
            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=1.0),
                dry_run=True,
            )
            assert result.dry_run is True
            assert result.auto_approved == ["bp1"]  # tracked, but NOT written
            client.blueprints.update.assert_not_called()


# ── Error handling: worker never crashes the cron ─────────────────────────


class TestErrorHandling:
    def test_query_failure_recorded_does_not_raise(self, monkeypatch):
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=AutoApprovalPolicy(enabled=True),
        ):
            client = MagicMock()
            client.blueprints.all.side_effect = RuntimeError("db down")
            result = run_pass("gaming", backlog_client=client)
            assert result.auto_approved == []
            assert any("blueprint query failed" in e for e in result.errors)

    def test_gate_exception_skips_only_that_blueprint(self, monkeypatch):
        """A bad gate evaluation on one blueprint must NOT stop the
        worker from processing the others."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=AutoApprovalPolicy(
                enabled=True, min_confidence=0.0, max_approvals_per_pass=10
            ),
        ):
            client = _stub_client(
                [
                    {"id": "bp_bad", "fields": {"hook_text": "h", "extra": {}}},
                    {"id": "bp_good", "fields": {"hook_text": "h", "extra": {}}},
                ]
            )

            def gate(blueprint):
                if blueprint["id"] == "bp_bad":
                    raise ValueError("gate broke")
                return _decision(approved=True, confidence=1.0)

            result = run_pass("gaming", backlog_client=client, gate_evaluate=gate)
            assert result.auto_approved == ["bp_good"]
            assert any("bp_bad" in e for e in result.errors)

    def test_backlog_update_exception_recorded_does_not_stop_pass(self, monkeypatch):
        """If the backlog write fails for one blueprint, the worker
        continues with the next — partial progress is better than zero."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=AutoApprovalPolicy(
                enabled=True, min_confidence=0.0, max_approvals_per_pass=10
            ),
        ):
            client = MagicMock()
            client.blueprints.all.return_value = [
                {"id": "bp_first", "fields": {"hook_text": "h", "extra": {}}},
                {"id": "bp_second", "fields": {"hook_text": "h", "extra": {}}},
            ]
            # First update fails, second succeeds
            client.blueprints.update.side_effect = [RuntimeError("write failed"), None]

            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=1.0),
            )
            assert result.auto_approved == ["bp_second"]
            assert any("bp_first" in e for e in result.errors)
            assert client.blueprints.update.call_count == 2


# ── Policy loading from YAML ───────────────────────────────────────────────


class TestPolicyLoading:
    def test_missing_yaml_returns_disabled_default(self, tmp_path, monkeypatch):
        """Niche dir exists but no publishing.yaml — must not raise;
        must return disabled policy."""
        # Build a fake GenLab tree
        fake_root = tmp_path / "fake_genlab"
        (fake_root / "CriticalRush" / "niches" / "gaming" / "config").mkdir(parents=True)
        policy = load_policy("gaming", genlab_root=fake_root)
        assert policy.enabled is False
        assert policy.min_confidence == 0.85
        assert policy.max_approvals_per_pass == 3

    def test_yaml_without_auto_publish_block_returns_disabled(self, tmp_path):
        fake_root = tmp_path / "fake_genlab"
        cfg_dir = fake_root / "CriticalRush" / "niches" / "gaming" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "publishing.yaml").write_text("rate_limits: {}\n")
        policy = load_policy("gaming", genlab_root=fake_root)
        assert policy.enabled is False

    def test_yaml_with_auto_publish_block_loads_fields(self, tmp_path):
        fake_root = tmp_path / "fake_genlab"
        cfg_dir = fake_root / "ClutchWire" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "publishing.yaml").write_text(
            "auto_publish:\n  enabled: true\n  min_confidence: 0.92\n  max_approvals_per_pass: 5\n"
        )
        policy = load_policy("sports", genlab_root=fake_root)
        assert policy.enabled is True
        assert policy.min_confidence == 0.92
        assert policy.max_approvals_per_pass == 5

    def test_invalid_field_types_fall_back_to_disabled(self, tmp_path):
        fake_root = tmp_path / "fake_genlab"
        cfg_dir = fake_root / "ClutchWire" / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "publishing.yaml").write_text(
            "auto_publish:\n  min_confidence: 'not_a_number'\n"
        )
        policy = load_policy("sports", genlab_root=fake_root)
        # Falls back to safe default (disabled) — never raises
        assert policy.enabled is False

    def test_unknown_niche_returns_disabled_default(self):
        policy = load_policy("nonexistent_niche")
        assert policy.enabled is False

    def test_ai_creators_loads_policy_from_blackboxbrief_yaml(self):
        """AUTO #2 S7 (2026-06-15): BlackboxBrief/config/publishing.yaml
        must exist + must expose the auto_publish block, so a future
        operator who wants to flip enabled=true has a concrete file to
        edit. Without this file, load_policy falls through every
        candidate path and returns the default disabled policy with
        no way to override.

        Pin checks the file exists in the real repo (NOT a fixture)
        and that load_policy reads it correctly."""
        # Use the real repo root, not a fixture — we're asserting on
        # the shipped state of BlackboxBrief/config/publishing.yaml.
        policy = load_policy("ai_creators")
        # The file ships with enabled=false (safe default); changing
        # to true is the AUTO #2 Day-8 flip step.
        assert policy.enabled is False, (
            "BlackboxBrief publishing.yaml must ship with enabled=false. "
            "Operator flips to true via PR — never hand-edit on prod."
        )
        # The other two fields must be loaded from the yaml — if they're
        # at AutoApprovalPolicy() defaults, the yaml wasn't found.
        # 0.85 + 3 are also the dataclass defaults so this isn't a
        # bulletproof check, but a future operator who tunes the yaml
        # to non-default values would break this pin if the load path
        # regressed.
        assert policy.min_confidence == 0.85
        assert policy.max_approvals_per_pass == 3


# ── P1 (Showstopper #1, 2026-06-15): the gate's `extra` wrapper ───────────


class TestGateExtraWrapper:
    """Pin the showstopper #1 fix.

    Bug: ``auto_approver._execute_approval`` built ``blueprint = {"id":
    ..., **fields}`` and passed it straight to ``gate_evaluate``. But
    the gate's ``evaluate()`` reads ``composite_score`` etc. from
    ``blueprint["extra"]``. On the Postgres path those fields are
    top-level, not under ``extra``, so the gate saw None for every
    score and aggregated confidence to ~0.5 regardless of blueprint
    quality. The dashboard preview endpoint already builds an ``extra``
    wrapper; the worker didn't. Round-3 audit caught this 2026-06-15.

    The fix: build the same wrapper before ``gate_evaluate(blueprint)``.
    """

    def _enabled_policy(self):
        return AutoApprovalPolicy(enabled=True, min_confidence=0.7, max_approvals_per_pass=10)

    def test_top_level_scores_wrapped_into_extra(self, monkeypatch):
        """The bug case: flat blueprint with top-level composite_score +
        virality_score (no ``extra`` key). The gate must SEE these values
        via the wrapper, not None."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)

        seen_blueprints = []

        def _capturing_gate(bp: dict) -> AutoApprovalDecision:
            seen_blueprints.append(bp)
            return _decision(approved=True, confidence=0.9)

        # Top-level scores, NO extra key — the Postgres path's shape
        flat_record = {
            "id": "bp1",
            "fields": {
                "hook_text": "h",
                "composite_score": 0.85,
                "virality_score": 0.12,
                "visual_paths": '["/path/a.mp4"]',
                "validation_status": {"qc_passed": True},
            },
        }

        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._enabled_policy(),
        ):
            run_pass(
                "gaming",
                backlog_client=_stub_client([flat_record]),
                gate_evaluate=_capturing_gate,
            )

        assert len(seen_blueprints) == 1
        seen = seen_blueprints[0]
        assert isinstance(seen.get("extra"), dict), (
            "auto_approver MUST build an `extra` wrapper before calling the gate; "
            "without it the gate sees None for every score (Showstopper #1)"
        )
        extra = seen["extra"]
        assert extra["composite_score"] == 0.85
        assert extra["virality_score"] == 0.12
        assert extra["visual_paths"] == ["/path/a.mp4"], (
            "visual_paths JSON string must be decoded to a list"
        )
        assert extra["validation_status"] == {"qc_passed": True}

    def test_existing_extra_dict_preserved_not_overwritten(self, monkeypatch):
        """SharePoint path already has ``extra`` as a dict. The wrapper
        must NOT replace it — only build one when missing."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)

        seen_blueprints = []

        def _capturing_gate(bp: dict) -> AutoApprovalDecision:
            seen_blueprints.append(bp)
            return _decision(approved=True, confidence=0.9)

        existing_extra = {
            "composite_score": 0.99,
            "virality_score": 0.99,
            "visual_paths": ["/already/here.mp4"],
            "custom_field": "do not delete me",
        }
        record_with_extra = {
            "id": "bp2",
            "fields": {
                "hook_text": "h",
                # These top-level values should be IGNORED — extra wins
                "composite_score": 0.1,
                "virality_score": 0.1,
                "extra": existing_extra,
            },
        }

        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._enabled_policy(),
        ):
            run_pass(
                "gaming",
                backlog_client=_stub_client([record_with_extra]),
                gate_evaluate=_capturing_gate,
            )

        assert seen_blueprints[0]["extra"] is existing_extra, (
            "Existing extra dict must be preserved untouched"
        )
        assert seen_blueprints[0]["extra"]["custom_field"] == "do not delete me"

    def test_malformed_visual_paths_string_falls_back_to_empty_list(self, monkeypatch):
        """A garbled visual_paths string mustn't raise — fall back to []."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)

        seen_blueprints = []

        def _capturing_gate(bp: dict) -> AutoApprovalDecision:
            seen_blueprints.append(bp)
            return _decision(approved=True, confidence=0.9)

        record = {
            "id": "bp3",
            "fields": {
                "hook_text": "h",
                "visual_paths": "not-valid-json!!!",
                "composite_score": 0.5,
                "virality_score": 0.5,
            },
        }

        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._enabled_policy(),
        ):
            run_pass(
                "gaming",
                backlog_client=_stub_client([record]),
                gate_evaluate=_capturing_gate,
            )

        # Must not have raised — and visual_paths is [] in the wrapper
        assert seen_blueprints[0]["extra"]["visual_paths"] == []


class TestPickNextAvailableSlot:
    """Pin the 2026-06-15 audit T#56 fix: worker writes scheduled_for
    via cap-aware slot picker. Without this, auto-approved blueprints
    have no scheduled_for, and either:
    - publish immediately at uncontrolled time (worst case), OR
    - hit publisher's _schedule_gate and stay stranded approved-but-
      unpublished
    """

    def _stub_with_existing(self, scheduled_isos: list[str]):
        """Mock backlog client whose get_blueprints_by_status returns
        rows with the given scheduled_for values (all niche=gaming
        VISUAL_READY approved)."""
        from unittest.mock import MagicMock

        client = MagicMock()
        rows = [
            {
                "id": f"bp{i}",
                "fields": {
                    "niche_id": "gaming",
                    "scheduled_for": iso,
                    "action_taken": "approved",
                    "status": "VISUAL_READY",
                },
            }
            for i, iso in enumerate(scheduled_isos)
        ]

        def _get_by_status(status, **kwargs):
            if status == "VISUAL_READY":
                return rows
            return []

        client.get_blueprints_by_status.side_effect = _get_by_status
        return client

    def test_picks_today_when_no_existing_posts(self):
        from genlab_core.scheduling.auto_approver import _pick_next_available_slot

        client = self._stub_with_existing([])
        slot = _pick_next_available_slot(backlog_client=client, niche_id="gaming")
        assert slot is not None
        # Returns ISO Z format
        assert slot.endswith("Z") or "+00:00" in slot

    def test_skips_day_at_cap_picks_next(self):
        """With cap=1 (default) and today already at cap, must roll to
        tomorrow."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from genlab_core.scheduling.auto_approver import _pick_next_available_slot

        ist = ZoneInfo("Asia/Kolkata")
        # Existing post at today 23:00 IST (always future relative to typical CI)
        today_late_ist = datetime.now(ist).replace(hour=23, minute=0, second=0, microsecond=0)
        client = self._stub_with_existing([today_late_ist.isoformat()])

        slot = _pick_next_available_slot(backlog_client=client, niche_id="gaming")
        assert slot is not None
        picked_dt = datetime.fromisoformat(slot.replace("Z", "+00:00"))
        picked_ist_date = picked_dt.astimezone(ist).strftime("%Y-%m-%d")
        today_ist_date = today_late_ist.strftime("%Y-%m-%d")
        assert picked_ist_date > today_ist_date, (
            f"day at cap=1 — must roll forward, got {picked_ist_date} vs today {today_ist_date}"
        )

    def test_self_record_excluded_from_count(self):
        """The blueprint being approved must not count itself toward
        the cap (mirrors dashboard's exclude_record_id semantics)."""
        from datetime import datetime
        from unittest.mock import MagicMock
        from zoneinfo import ZoneInfo

        from genlab_core.scheduling.auto_approver import _pick_next_available_slot

        ist = ZoneInfo("Asia/Kolkata")
        # Existing post = the same record we're approving
        today_late_ist = datetime.now(ist).replace(hour=23, minute=0, second=0, microsecond=0)
        client = MagicMock()
        client.get_blueprints_by_status.side_effect = lambda status, **kw: (
            [
                {
                    "id": "self_bp",
                    "fields": {
                        "niche_id": "gaming",
                        "scheduled_for": today_late_ist.isoformat(),
                        "status": "VISUAL_READY",
                    },
                }
            ]
            if status == "VISUAL_READY"
            else []
        )

        slot = _pick_next_available_slot(
            backlog_client=client,
            niche_id="gaming",
            exclude_record_id="self_bp",
        )
        # With self excluded, day count = 0, today should be available
        assert slot is not None

    def test_no_capacity_in_window_returns_none(self):
        """If all 8 days ahead are at cap, return None — caller skips
        the approval rather than over-schedule."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        from genlab_core.scheduling.auto_approver import _pick_next_available_slot

        ist = ZoneInfo("Asia/Kolkata")
        now_ist = datetime.now(ist)
        # 9 posts, one per day starting today
        iso_list = [
            (now_ist + timedelta(days=d))
            .replace(hour=12, minute=0, second=0, microsecond=0)
            .isoformat()
            for d in range(9)
        ]
        client = self._stub_with_existing(iso_list)

        slot = _pick_next_available_slot(backlog_client=client, niche_id="gaming")
        assert slot is None, (
            "all 7 forward days at cap → must return None, NOT silently "
            "pick a slot beyond the safe window"
        )


# ── D2.7a: strategies wired into the worker (integration) ─────────────


class TestStrategyLayerIntegration:
    """Pins for D2.7a — the worker invokes apply_strategies AFTER the
    base gate when policy.strategies opts in.

    These tests use the strategy_b / strategy_e lookups in their fake
    form (no DB) and verify the worker reaches the strategy code path
    only when the per-niche policy enables it.
    """

    def _enabled_policy_with_strategies(self, strategies=None):
        """Build a policy that the worker would normally use to run."""
        from genlab_core.scheduling.gate_strategies import StrategyConfig

        return AutoApprovalPolicy(
            enabled=True,
            min_confidence=0.0,  # gate any approved
            max_approvals_per_pass=10,
            strategies=strategies or StrategyConfig(),
        )

    def test_strategies_off_by_default_means_no_lookup(self, monkeypatch):
        """Default StrategyConfig should never trigger the lookup callbacks."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        # If a lookup fires, this MagicMock would record the call.
        called = []

        def boom_lookup(*args, **kwargs):
            called.append(args)
            return None

        # Patch the module-level helpers so even if the wiring fires
        # they record + return None.
        monkeypatch.setattr(
            "genlab_core.scheduling.auto_approver._lookup_bandit_arm", boom_lookup
        )
        monkeypatch.setattr(
            "genlab_core.scheduling.auto_approver._lookup_calibration_stats",
            boom_lookup,
        )
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._enabled_policy_with_strategies(),
        ):
            client = _stub_client(
                [{"id": "bp1", "niche_id": "gaming", "arm_id": "x", "action_taken": ""}]
            )
            run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=0.9),
                dry_run=True,
            )
        assert called == [], "strategies disabled but lookups still fired"

    def test_strategy_e_blocks_when_calibration_thin(self, monkeypatch):
        """Worker honours Strategy E: thin calibration → blueprint blocked."""
        from genlab_core.scheduling.gate_strategies import StrategyConfig

        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        # Force calibration lookup to return "thin"
        monkeypatch.setattr(
            "genlab_core.scheduling.auto_approver._lookup_calibration_stats",
            lambda n, w: {"sample_count": 1, "agreement_rate": 0.0},
        )
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._enabled_policy_with_strategies(
                StrategyConfig(agreement_floor_enabled=True)
            ),
        ):
            client = _stub_client(
                [{"id": "bp1", "niche_id": "gaming", "arm_id": "x", "action_taken": ""}]
            )
            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=0.9),
                dry_run=True,
            )
        # E flipped the gate's approval to False → bucketed as rejected
        assert result.auto_approved == []
        assert "bp1" in result.skipped_gate_rejected

    def test_strategy_b_lookup_failure_preserves_base_decision(self, monkeypatch):
        """If the strategy layer crashes, the worker logs and proceeds
        with the BASE decision — never silently drops blueprints."""
        from genlab_core.scheduling.gate_strategies import StrategyConfig

        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)

        def boom(*args, **kwargs):
            raise RuntimeError("DB down")

        monkeypatch.setattr(
            "genlab_core.scheduling.auto_approver._lookup_bandit_arm", boom
        )
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._enabled_policy_with_strategies(
                StrategyConfig(bandit_boost_enabled=True)
            ),
        ):
            client = _stub_client(
                [{"id": "bp1", "niche_id": "gaming", "arm_id": "x", "action_taken": ""}]
            )
            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=0.9),
                dry_run=True,
            )
        # B failure is fail-open per design — base gate's approval stands
        assert "bp1" in result.auto_approved
