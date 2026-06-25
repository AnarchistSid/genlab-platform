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
        """AUTO #2 S7 (2026-06-15) — guard rail on the ramp ladder.

        2026-06-15 origin: BlackboxBrief/config/publishing.yaml must
        exist + expose the auto_publish block, so a future operator
        who wants to flip enabled=true has a concrete file to edit.
        Without this file, load_policy falls through every candidate
        path and returns the default disabled policy with no way to
        override.

        2026-06-22 update (PR #462 controlled ramp): the original
        ``enabled is False`` assertion was the right guard while the
        yaml was a placeholder; once we begin the staged AUTO #2
        ramp (Week 1 = rollout_pct 0.1 → Week 4 = 1.0) the file MUST
        be allowed to ship with enabled=true. The new contract:

        - enabled=False is always OK (the original safe state)
        - enabled=True requires rollout_pct ≤ 0.5 (staged-ramp
          contract from BlackboxBrief/config/publishing.yaml header
          docstring; pre-Week-4 PRs must NEVER set rollout_pct=1.0
          without explicitly bumping this pin)

        This enforces: a future hand-edit that flips enabled=true
        without setting a conservative rollout_pct fails the pin.
        Each ramp PR (Week 2 0.25, Week 3 0.5) passes the same
        check. The Week 4 PR (rollout_pct: 1.0) MUST also update
        this assertion as a deliberate checkpoint.
        """
        policy = load_policy("ai_creators")
        if policy.enabled:
            # Staged-ramp invariant — pre-Week-4 PRs cap at 0.5.
            assert policy.rollout_pct is not None, (
                "BlackboxBrief publishing.yaml has enabled=true but no "
                "rollout_pct set. Per the staged-ramp contract, "
                "enabling auto-publish requires a rollout_pct ≤ 0.5 "
                "(W1: 0.1, W2: 0.25, W3: 0.5). Bump this pin when "
                "Week 4 ships rollout_pct=1.0."
            )
            assert policy.rollout_pct <= 0.5, (
                f"BlackboxBrief publishing.yaml has rollout_pct="
                f"{policy.rollout_pct}, which violates the staged-ramp "
                f"contract (must be ≤ 0.5 until the explicit Week-4 "
                f"flip PR bumps this pin)."
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
        monkeypatch.setattr("genlab_core.scheduling.auto_approver._lookup_bandit_arm", boom_lookup)
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

        monkeypatch.setattr("genlab_core.scheduling.auto_approver._lookup_bandit_arm", boom)
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


# ── W4.3: graduated rollout dice ───────────────────────────────────────────


class TestRolloutPct:
    """W4.3: rollout_pct throttles approval VOLUME among gate+confidence
    qualifiers. Lets operators ramp niches 10% → 50% → 100% to observe
    real-world outcomes at increasing scale without changing min_confidence.

    Default is 1.0 (full traffic) — pre-W4.3 behaviour. 0.0 acts as a
    soft per-niche kill (vs the global kill switch).
    """

    def _policy(self, rollout_pct: float = 1.0, **overrides):
        kwargs = dict(
            enabled=True,
            min_confidence=0.5,
            max_approvals_per_pass=100,
            rollout_pct=rollout_pct,
        )
        kwargs.update(overrides)
        return AutoApprovalPolicy(**kwargs)

    def test_default_rollout_is_1_0_preserves_pre_w43_behaviour(self):
        """The dataclass default — verifies we didn't accidentally
        introduce a behavioural change for niches that haven't opted in."""
        policy = AutoApprovalPolicy()
        assert policy.rollout_pct == 1.0

    def test_rollout_1_0_approves_every_qualifier(self, monkeypatch):
        """At full rollout the dice roll is a no-op (skipped via
        fast-path). All 10 qualifiers get approved."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._policy(rollout_pct=1.0),
        ):
            client = _stub_client(
                [{"id": f"bp{i}", "fields": {"hook_text": "h", "extra": {}}} for i in range(10)]
            )
            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=0.9),
                dry_run=True,
            )
            assert len(result.auto_approved) == 10
            assert result.skipped_rollout == []

    def test_rollout_0_0_blocks_every_qualifier(self, monkeypatch):
        """Soft per-niche kill — gate+confidence pass but nothing
        approves. Distinct from enabled=false (worker still runs,
        candidates still examined, calibration still logged)."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._policy(rollout_pct=0.0),
        ):
            client = _stub_client(
                [{"id": f"bp{i}", "fields": {"hook_text": "h", "extra": {}}} for i in range(5)]
            )
            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=0.9),
                dry_run=True,
            )
            assert result.auto_approved == []
            assert len(result.skipped_rollout) == 5
            assert result.candidates_examined == 5

    def test_rollout_pct_throttles_volume_when_dice_loses(self, monkeypatch):
        """Force the dice to ALWAYS lose by patching _dice_value to
        return ≥ rollout_pct. Verifies the rollout path filters
        approvals without altering gate/confidence semantics."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        # _dice_value returns 0.9 → 0.9 >= 0.5 → dice lost
        monkeypatch.setattr(
            "genlab_core.scheduling.auto_approver._dice_value",
            lambda record_id: 0.9,
        )
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._policy(rollout_pct=0.5),
        ):
            client = _stub_client(
                [{"id": f"bp{i}", "fields": {"hook_text": "h", "extra": {}}} for i in range(3)]
            )
            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=0.9),
                dry_run=True,
            )
            assert result.auto_approved == []
            assert result.skipped_rollout == ["bp0", "bp1", "bp2"]

    def test_rollout_pct_approves_when_dice_wins(self, monkeypatch):
        """Force the dice to ALWAYS win by patching _dice_value to
        return < rollout_pct. Approval path runs end-to-end."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        # _dice_value returns 0.1 → 0.1 < 0.5 → dice won
        monkeypatch.setattr(
            "genlab_core.scheduling.auto_approver._dice_value",
            lambda record_id: 0.1,
        )
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._policy(rollout_pct=0.5),
        ):
            client = _stub_client(
                [{"id": f"bp{i}", "fields": {"hook_text": "h", "extra": {}}} for i in range(3)]
            )
            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=lambda bp: _decision(approved=True, confidence=0.9),
                dry_run=True,
            )
            assert result.auto_approved == ["bp0", "bp1", "bp2"]
            assert result.skipped_rollout == []

    def test_rollout_does_not_consume_idempotent_or_gate_rejected(self, monkeypatch):
        """The dice roll runs AFTER idempotency + gate + confidence
        filters. A blueprint stopped earlier shouldn't appear in the
        rollout bucket — it'd skew operator's read of the throttle's
        actual effect."""
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        # Force dice to always lose so rollout would catch anything that
        # reaches it
        monkeypatch.setattr(
            "genlab_core.scheduling.auto_approver._dice_value",
            lambda record_id: 0.99,
        )
        with patch(
            "genlab_core.scheduling.auto_approver.load_policy",
            return_value=self._policy(rollout_pct=0.5),
        ):
            client = _stub_client(
                [
                    # already actioned — must skip idempotent
                    {
                        "id": "bp_idem",
                        "fields": {"hook_text": "h", "action_taken": "approved", "extra": {}},
                    },
                    # gate rejects — must skip gate-rejected
                    {"id": "bp_gate", "fields": {"hook_text": "h", "extra": {}}},
                    # passes everything — dice catches it
                    {"id": "bp_dice", "fields": {"hook_text": "h", "extra": {}}},
                ]
            )

            # gate approves only bp_dice (and bp_idem, which is filtered earlier)
            def gate(bp):
                return _decision(
                    approved=bp.get("id") != "bp_gate",
                    confidence=0.9,
                )

            result = run_pass(
                "gaming",
                backlog_client=client,
                gate_evaluate=gate,
                dry_run=True,
            )
            assert result.skipped_idempotent == ["bp_idem"]
            assert result.skipped_gate_rejected == ["bp_gate"]
            assert result.skipped_rollout == ["bp_dice"]
            assert result.auto_approved == []

    def test_rollout_pct_in_yaml_loaded_correctly(self, tmp_path):
        """End-to-end: a publishing.yaml with rollout_pct gets loaded
        into the policy's rollout_pct field."""
        from genlab_core.pipeline.cli import NICHE_DIR_NAMES as _NICHE_ROOT_NAMES

        niche_root = tmp_path / _NICHE_ROOT_NAMES["gaming"] / "niches" / "gaming"
        (niche_root / "config").mkdir(parents=True)
        (niche_root / "config" / "publishing.yaml").write_text(
            "auto_publish:\n"
            "  enabled: true\n"
            "  min_confidence: 0.85\n"
            "  max_approvals_per_pass: 3\n"
            "  rollout_pct: 0.1\n"
        )
        policy = load_policy("gaming", genlab_root=tmp_path)
        assert policy.enabled is True
        assert policy.rollout_pct == 0.1

    def test_rollout_pct_out_of_range_clamps_to_1_0(self, tmp_path, caplog):
        """A yaml typo (50 instead of 0.5) must NOT silently block all
        approvals — degrade to 1.0 with a WARN log."""
        from genlab_core.pipeline.cli import NICHE_DIR_NAMES as _NICHE_ROOT_NAMES

        niche_root = tmp_path / _NICHE_ROOT_NAMES["gaming"] / "niches" / "gaming"
        (niche_root / "config").mkdir(parents=True)
        (niche_root / "config" / "publishing.yaml").write_text(
            "auto_publish:\n  enabled: true\n  rollout_pct: 50\n"  # typo: meant 0.5
        )
        with caplog.at_level("WARNING"):
            policy = load_policy("gaming", genlab_root=tmp_path)
        assert policy.rollout_pct == 1.0
        assert any("rollout_pct=50" in r.message for r in caplog.records)

    def test_rollout_pct_negative_clamps_to_1_0(self, tmp_path):
        """Negative rollout is also a config error — same clamp."""
        from genlab_core.pipeline.cli import NICHE_DIR_NAMES as _NICHE_ROOT_NAMES

        niche_root = tmp_path / _NICHE_ROOT_NAMES["gaming"] / "niches" / "gaming"
        (niche_root / "config").mkdir(parents=True)
        (niche_root / "config" / "publishing.yaml").write_text(
            "auto_publish:\n  enabled: true\n  rollout_pct: -0.1\n"
        )
        policy = load_policy("gaming", genlab_root=tmp_path)
        assert policy.rollout_pct == 1.0

    def test_rollout_pct_default_when_omitted_in_yaml(self, tmp_path):
        """Existing publishing.yaml files (no rollout_pct key) must
        default to 1.0 — no behaviour change on deploy."""
        from genlab_core.pipeline.cli import NICHE_DIR_NAMES as _NICHE_ROOT_NAMES

        niche_root = tmp_path / _NICHE_ROOT_NAMES["gaming"] / "niches" / "gaming"
        (niche_root / "config").mkdir(parents=True)
        (niche_root / "config" / "publishing.yaml").write_text(
            "auto_publish:\n  enabled: true\n  min_confidence: 0.85\n"
        )
        policy = load_policy("gaming", genlab_root=tmp_path)
        assert policy.rollout_pct == 1.0


# ── W4.3 deterministic dice (post-audit semantic fix) ──────────────────────


class TestDiceValue:
    """The dice MUST be a property of the blueprint, not the pass.

    Post-Wave-4 audit (2026-06-17) found the original ``random.random()``
    impl produced wrong semantic: at rollout_pct=0.1, blueprints
    weren't 10%-approved-ever — they were 100%-approved-eventually
    after enough passes (each pass re-rolled). The fix is a
    deterministic per-blueprint hash so the same blueprint gets the
    same outcome on every pass.
    """

    def test_same_id_same_value(self):
        from genlab_core.scheduling.auto_approver import _dice_value

        v1 = _dice_value("bp_abc123")
        v2 = _dice_value("bp_abc123")
        assert v1 == v2, "dice MUST be deterministic per record_id"

    def test_different_ids_likely_different_values(self):
        from genlab_core.scheduling.auto_approver import _dice_value

        # Not a strict property (hash collisions exist) but on 100
        # distinct IDs we expect ≥95 distinct dice values.
        ids = [f"bp_{i:08d}" for i in range(100)]
        values = {_dice_value(i) for i in ids}
        assert len(values) >= 95

    def test_dice_in_unit_range(self):
        from genlab_core.scheduling.auto_approver import _dice_value

        for i in range(50):
            v = _dice_value(f"bp_{i}")
            assert 0.0 <= v < 1.0

    def test_distribution_is_roughly_uniform_at_threshold(self):
        """At rollout_pct=0.5, half of ~1000 distinct ids should pass.

        This is the property operators rely on for graduated rollout:
        at 0.5 they get *roughly* half the volume, not "first half"
        or "last half". 10% tolerance is generous — sha256 distribution
        is excellent.
        """
        from genlab_core.scheduling.auto_approver import _dice_value

        passed = sum(1 for i in range(1000) if _dice_value(f"bp_{i:08d}") < 0.5)
        assert 400 <= passed <= 600, f"expected ~500, got {passed}"

    def test_rollout_is_per_blueprint_not_per_pass(self, monkeypatch):
        """The fix's reason for being: two passes over the SAME
        blueprint at rollout_pct=0.5 must produce the SAME decision.

        Pre-fix (random.random()) failed this — each pass would re-roll
        and a blueprint that lost pass 1 could win pass 2. That's not
        rollout, it's latency.
        """
        from genlab_core.scheduling.auto_approver import _dice_value

        rec_id = "deterministic_bp"
        # If dice is < 0.5 it would approve at rollout_pct=0.5; if ≥0.5
        # it would skip. Either way, the SAME blueprint must produce
        # the SAME decision across N passes.
        decisions = {_dice_value(rec_id) < 0.5 for _ in range(20)}
        assert len(decisions) == 1, (
            "the same blueprint MUST produce the same dice decision on every pass — "
            "this is the operator's mental model of graduated rollout"
        )
