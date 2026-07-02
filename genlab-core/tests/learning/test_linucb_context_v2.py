"""Intervention 9 (2026-07-01): cyclical time encoding tests.

Covers:
- ``encode_weekday_cyclical`` and ``encode_hour_cyclical`` correctness
  (unit-circle identity, wrap-around, integer endpoints)
- ``build_content_context_v2`` shape + layout (15-D, cyclical time
  in slots 0-3, non-time dims byte-identical to v1's 2-12)
- Feature-flag helper ``_temporal_context_enabled`` exact-true
- v1 builder (``build_content_context``) is UNCHANGED by the v2
  addition — this is the shipped-contract guarantee
- v2 wire from ``publishing.feedback_registration`` writes
  ``linucb_context_v2`` alongside ``linucb_context`` in the
  bandit_context payload
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from genlab_core.learning import linucb

# ── Feature flag ─────────────────────────────────────────────────────


class TestTemporalContextFlag:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_TEMPORAL_CONTEXT_ENABLED", raising=False)
        assert not linucb._temporal_context_enabled()

    @pytest.mark.parametrize("val", ["true", "TRUE", "True"])
    def test_exact_true_activates(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_TEMPORAL_CONTEXT_ENABLED", val)
        assert linucb._temporal_context_enabled()

    @pytest.mark.parametrize("val", ["1", "yes", "on", "TRUE_", "", "false"])
    def test_non_exact_true_stays_off(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_TEMPORAL_CONTEXT_ENABLED", val)
        assert not linucb._temporal_context_enabled()


# ── Cyclical encoders ───────────────────────────────────────────────


class TestCyclicalEncoders:
    """The key correctness property: sin² + cos² == 1 for every valid
    integer input. This is what gives cyclical encoding its "Monday
    and Sunday are adjacent" property; break this and the whole
    intervention regresses."""

    @pytest.mark.parametrize("weekday", list(range(7)))
    def test_weekday_unit_circle(self, weekday):
        s, c = linucb.encode_weekday_cyclical(weekday)
        assert s * s + c * c == pytest.approx(1.0)

    @pytest.mark.parametrize("hour", list(range(24)))
    def test_hour_unit_circle(self, hour):
        s, c = linucb.encode_hour_cyclical(hour)
        assert s * s + c * c == pytest.approx(1.0)

    def test_weekday_monday_sunday_adjacent(self):
        """The whole point of cyclical encoding: Monday (0) and Sunday
        (6) should be CLOSER on the unit circle than Monday and
        Wednesday (2) — the integer scale gets this backwards."""
        s_mon, c_mon = linucb.encode_weekday_cyclical(0)
        s_sun, c_sun = linucb.encode_weekday_cyclical(6)
        s_wed, c_wed = linucb.encode_weekday_cyclical(2)

        dist_mon_sun = math.hypot(s_mon - s_sun, c_mon - c_sun)
        dist_mon_wed = math.hypot(s_mon - s_wed, c_mon - c_wed)
        assert dist_mon_sun < dist_mon_wed, (
            f"Monday-Sunday distance ({dist_mon_sun:.3f}) should be less than "
            f"Monday-Wednesday distance ({dist_mon_wed:.3f}) — that's the "
            "core property cyclical encoding buys us over integer coding."
        )

    def test_hour_midnight_23_adjacent(self):
        """Hour 23 and hour 0 should be adjacent — the integer scale
        codes them maximally distant even though they're one hour apart."""
        s_0, c_0 = linucb.encode_hour_cyclical(0)
        s_23, c_23 = linucb.encode_hour_cyclical(23)
        s_12, c_12 = linucb.encode_hour_cyclical(12)

        dist_0_23 = math.hypot(s_0 - s_23, c_0 - c_23)
        dist_0_12 = math.hypot(s_0 - s_12, c_0 - c_12)
        assert dist_0_23 < dist_0_12


# ── build_content_context_v2 shape + layout ────────────────────────


class TestBuildContentContextV2:
    def _story(self):
        return {
            "hook_text": "A story hook here that's roughly 60 chars long!",
            "duration_seconds": 30,
            "view_velocity": 1000,
            "composite_score": 0.5,
            "affiliate_product": None,
            "content": {"instagram": {"caption": "A caption here"}},
            "hashtags": ["a", "b", "c"],
        }

    def test_returns_15_dim_array(self):
        now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
        ctx = linucb.build_content_context_v2(self._story(), "gaming", now=now)
        assert ctx.shape == (linucb.EXTENDED_CONTEXT_DIM,)
        assert ctx.shape == (15,)

    def test_time_dims_in_slots_0_to_3(self):
        """Layout contract: [weekday_sin, weekday_cos, hour_sin,
        hour_cos, ...] — dashboards / analysis scripts read positional
        slots, not names."""
        now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)  # Wed 12:00
        ctx = linucb.build_content_context_v2(self._story(), "gaming", now=now)

        exp_wsin, exp_wcos = linucb.encode_weekday_cyclical(now.weekday())
        exp_hsin, exp_hcos = linucb.encode_hour_cyclical(now.hour)

        assert ctx[0] == pytest.approx(exp_wsin)
        assert ctx[1] == pytest.approx(exp_wcos)
        assert ctx[2] == pytest.approx(exp_hsin)
        assert ctx[3] == pytest.approx(exp_hcos)

    def test_non_time_dims_match_v1(self):
        """The v1→v2 migration guarantee: dims 4-14 in v2 are byte-
        identical to v1 dims 2-12. This is what lets a future LinUCB
        retrain carry forward the 11 non-time θ coefficients."""
        now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
        story = self._story()
        v1 = linucb.build_content_context(story, "gaming", now=now)
        v2 = linucb.build_content_context_v2(story, "gaming", now=now)

        assert v1.shape == (linucb.CONTEXT_DIM,)  # sanity: v1 is 13-D
        assert v2.shape == (linucb.EXTENDED_CONTEXT_DIM,)  # sanity: v2 is 15-D

        # v2[4:] should equal v1[2:] element-wise
        for i in range(2, linucb.CONTEXT_DIM):
            assert v2[i + 2] == pytest.approx(v1[i]), (
                f"v2[{i + 2}] ({v2[i + 2]:.4f}) should equal v1[{i}] "
                f"({v1[i]:.4f}) — non-time dims are supposed to be "
                "byte-identical between v1 and v2."
            )

    def test_v2_stable_across_weekday_wrap(self):
        """Sanity check: Monday and Sunday-then-Monday-again produce
        the same weekday_sin/cos, even though the integer weekday
        wraps back to 0. Regression pin for a modulo/off-by-one
        that would silently degrade cyclical encoding."""
        mon = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)  # Mon
        next_mon = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)  # Mon
        v1 = linucb.build_content_context_v2(self._story(), "gaming", now=mon)
        v2 = linucb.build_content_context_v2(self._story(), "gaming", now=next_mon)
        assert v1[0] == pytest.approx(v2[0])
        assert v1[1] == pytest.approx(v2[1])


# ── v1 backward-compat ─────────────────────────────────────────────


class TestV1Unchanged:
    """Intervention 9 must NOT change v1's shape or values — the
    shipped LinUCB decision path (CONTEXT_DIM=13, 40+ live arms with
    13×13 A matrices) must observe zero behavioural change."""

    def test_v1_still_13_dim(self):
        now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
        v1 = linucb.build_content_context({}, "gaming", now=now)
        assert v1.shape == (13,)
        assert linucb.CONTEXT_DIM == 13

    def test_v1_first_two_dims_still_static_integers(self):
        """The v1 vector still uses ``weekday/6.0`` and ``hour/23.0``
        as its first two features. If a future PR accidentally
        replaces these with cyclical time in v1 (not just v2),
        LinUCB's 40+ live arms would suddenly be scored against
        a different feature semantics — this pin catches that."""
        now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
        v1 = linucb.build_content_context({}, "gaming", now=now)
        assert v1[0] == pytest.approx(now.weekday() / 6.0)
        assert v1[1] == pytest.approx(now.hour / 23.0)


# ── Persistence wire (feedback_registration) ──────────────────────


class TestFeedbackRegistrationWire:
    """Intervention 9 wire: the JSON persistence path in
    ``publishing/feedback_registration.py`` must include
    ``linucb_context_v2`` alongside ``linucb_context`` in the
    bandit_context payload. This is the store DR + ensemble
    consumers will read from."""

    def test_bandit_context_carries_v2(self):
        from genlab_core.publishing.feedback_registration import _build_bandit_context

        fields = {
            "hook": "A hook",
            "hook_style": "revelation",
        }
        ctx = _build_bandit_context(fields, "gaming")
        assert "linucb_context" in ctx
        assert "linucb_context_v2" in ctx
        assert isinstance(ctx["linucb_context_v2"], list)
        assert len(ctx["linucb_context_v2"]) == 15
        assert len(ctx["linucb_context"]) == 13

    def test_v2_absence_does_not_break_wire(self, monkeypatch):
        """If the v2 builder ever raises (defensive: any downstream
        integration bug), the v1 path must still populate — we can't
        let a v2 hiccup break the shipping v1 wire."""
        from genlab_core.publishing import feedback_registration as fr

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated v2 build failure")

        monkeypatch.setattr("genlab_core.learning.linucb.build_content_context_v2", _boom)
        ctx = fr._build_bandit_context({"hook": "A hook"}, "gaming")
        assert "linucb_context" in ctx
        assert len(ctx["linucb_context"]) == 13
        # v2 should be absent (or None) — not crash the wire
        assert ctx.get("linucb_context_v2") is None or "linucb_context_v2" not in ctx
