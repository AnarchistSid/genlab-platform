"""Pin: review queue sorts by gate-uncertainty (Lever F).

Pre-2026-06-21 the queue sorted by priority_score desc only — a pure
FIFO of highest-engagement candidates. Operator clicks were wasted on
easy cases (gate confidence ~0.95) while borderline cases (most
informative for calibration) sat at the bottom. The queue was
ANTI-active-learning by default.

Lever F switches to uncertainty sampling: primary sort by
``abs(gate_confidence - 0.5)`` ASCENDING so borderline cases bubble
to the top. Secondary tiebreak by ``-priority_score`` so within an
uncertainty band, highest-engagement candidates still rank first.

These pins lock in:
  1. Items closer to confidence 0.5 sort BEFORE items closer to
     0.0 or 1.0 (the core active-learning property).
  2. Tiebreak by priority within an uncertainty band — same gate
     confidence, higher priority sorts first.
  3. The endpoint includes ``auto_approval_confidence`` and
     ``queue_boundary_distance`` in each item so the frontend can
     show operators WHY a blueprint is at this queue position.
  4. Gate evaluation failure for any single blueprint does not crash
     the queue — confidence defaults to 0.5 (treated as borderline)
     so nothing is lost.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    from server import review_server as rs

    monkeypatch.setattr(rs, "_AUTH_ENABLED", False)


def _make_record(
    record_id,
    *,
    niche_id,
    priority_score,
    composite_score,
    virality_score,
    has_video=True,
    has_hook=True,
):
    """Build a SharePoint-shaped blueprint record that produces a known
    gate confidence when run through ``auto_approval_gate.evaluate()``."""
    return {
        "id": record_id,
        "fields": {
            "status": "VISUAL_READY",
            "niche_id": niche_id,
            "hook_text": "A hook" if has_hook else "",
            "priority_score": priority_score,
            "extra": {
                "composite_score": composite_score,
                "virality_score": virality_score,
                "validation_status": {"all_passed": True},
                "visual_paths": ["/x.mp4"] if has_video else [],
            },
        },
    }


def _fetch_queue(records):
    """Helper: call the review_queue endpoint with a stubbed client."""
    fake_client = MagicMock()
    fake_client.blueprints.all.return_value = records
    fake_client.get_blueprints_by_status.return_value = []

    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch("server.api.blueprints._prefetch_story_thumbnails"),
    ):
        from flask import Flask
        from server.api.blueprints import bp as blueprints_bp

        app = Flask(__name__)
        app.register_blueprint(blueprints_bp)

        client = app.test_client()
        resp = client.get("/api/v1/blueprints/review-queue?niche_id=all")
        return resp


class TestActiveLearningSort:
    def test_borderline_blueprint_sorts_before_confident_one(self):
        """Two blueprints: A has high composite (clear approve, conf ~1.0),
        B has medium composite (borderline, conf ~0.5). B should appear
        BEFORE A in the queue — that's the uncertainty-sampling pin."""
        # A: very high composite → very high gate confidence (clear approve)
        record_a = _make_record(
            "rec_a",
            niche_id="gaming",
            priority_score=0.9,
            composite_score=0.95,
            virality_score=0.10,
        )
        # B: borderline composite (just above 0.30 default) → ~0.5 confidence
        record_b = _make_record(
            "rec_b",
            niche_id="gaming",
            priority_score=0.5,
            composite_score=0.32,
            virality_score=0.025,
        )

        resp = _fetch_queue([record_a, record_b])
        assert resp.status_code == 200
        data = resp.get_json()["data"]["data"]
        ids = [item["id"] for item in data]
        # B (borderline) should sort BEFORE A (confident).
        assert ids.index("rec_b") < ids.index("rec_a"), (
            f"Expected rec_b (borderline) before rec_a (confident); got order {ids}. "
            f"Confidences: {[(i['id'], i['auto_approval_confidence']) for i in data]}"
        )

    def test_priority_breaks_uncertainty_ties(self):
        """Two blueprints with IDENTICAL composite/virality (so identical
        gate confidence) — priority_score should be the tiebreak: higher
        priority sorts first within the uncertainty band."""
        record_low_pri = _make_record(
            "rec_low_pri",
            niche_id="gaming",
            priority_score=0.2,
            composite_score=0.5,
            virality_score=0.05,
        )
        record_high_pri = _make_record(
            "rec_high_pri",
            niche_id="gaming",
            priority_score=0.8,
            composite_score=0.5,
            virality_score=0.05,
        )

        resp = _fetch_queue([record_low_pri, record_high_pri])
        data = resp.get_json()["data"]["data"]
        ids = [item["id"] for item in data]
        # Same confidence → higher priority wins.
        assert ids.index("rec_high_pri") < ids.index("rec_low_pri")

    def test_response_includes_confidence_and_boundary_distance(self):
        """Each item exposes gate confidence + boundary distance so the
        frontend can show operators a 'borderline' badge or explain the
        queue position."""
        record = _make_record(
            "rec_x",
            niche_id="gaming",
            priority_score=0.5,
            composite_score=0.40,
            virality_score=0.03,
        )
        resp = _fetch_queue([record])
        data = resp.get_json()["data"]["data"]
        assert len(data) == 1
        item = data[0]
        assert "auto_approval_confidence" in item, (
            f"Expected auto_approval_confidence in item; got keys: {list(item.keys())}"
        )
        assert "queue_boundary_distance" in item
        # boundary_distance MUST be abs(confidence - 0.5)
        assert item["queue_boundary_distance"] == pytest.approx(
            abs(item["auto_approval_confidence"] - 0.5), abs=1e-3
        )


class TestSortRobustness:
    def test_gate_evaluation_failure_does_not_drop_record(self):
        """If gate evaluation raises on a malformed blueprint, the
        record MUST still appear in the queue — we just treat it as
        borderline (0.5) so the operator can decide. Pin against the
        regression where a single bad record empties the queue."""
        # A normal record + a malformed one (no 'fields' key at all)
        # The fallback in _compute_metrics treats it as confidence=0.5
        # so it appears, just bubbled to the borderline position.
        normal = _make_record(
            "rec_normal",
            niche_id="gaming",
            priority_score=0.5,
            composite_score=0.95,  # very confident
            virality_score=0.10,
        )
        # Malformed: fields key missing entirely — gate eval will raise
        # AttributeError on .get() chain.
        malformed = {"id": "rec_bad"}

        resp = _fetch_queue([normal, malformed])
        assert resp.status_code == 200
        data = resp.get_json()["data"]["data"]
        ids = [item["id"] for item in data]
        # Both records present — the malformed one didn't crash the sort.
        assert "rec_bad" in ids
        assert "rec_normal" in ids
        # The malformed one gets confidence=0.5 → sorts FIRST (most uncertain)
        # ahead of the normal high-confidence one.
        assert ids.index("rec_bad") < ids.index("rec_normal")
