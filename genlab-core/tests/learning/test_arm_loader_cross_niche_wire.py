"""Intervention 2 consumer-wire pins (2026-07-02).

The one-line adopter at ``arm_loader.save_arm`` calls
``get_transferred_prior`` when creating a new arm. Pins:

* Flag off → alpha/beta stay at whatever the caller passed
  (behaviour byte-identical to today; observation-only rollout
  preserved for operators who haven't validated the priors card)
* Flag on but no transferred prior → same as flag off
* Flag on + transferred prior + caller had ~Beta(1,1) baseline →
  transfer applied, first-observation contribution preserved
* Flag on + caller had bigger prior (α+β ≥ 3) → transfer NOT
  applied — respects meta_prior.py's niche-to-niche warm start,
  never overrides callers that did their own prior work
* Update-path (existing arm) never invokes the transfer — cold-
  start acceleration only, no steady-state override
"""

from __future__ import annotations

import pytest


class _FakeProxy:
    """Minimal drop-in for the BacklogClient bandit_arms proxy.

    Tracks the fields dict passed to `create` so tests can assert on
    what the transferred prior did.
    """

    def __init__(self, existing: list | None = None):
        self.existing_rows = existing or []
        self.create_calls: list[dict] = []
        self.update_calls: list[tuple[str, dict]] = []

    def all(self, formula: str = "", max_records: int = 100):
        return self.existing_rows

    def create(self, fields: dict) -> dict:
        self.create_calls.append(dict(fields))
        return {"id": "new"}

    def update(self, record_id: str, fields: dict) -> dict:
        self.update_calls.append((record_id, dict(fields)))
        return {"id": record_id}


def test_flag_off_no_override(monkeypatch, tmp_path):
    """No env flag → save_arm behaves identically to pre-wire code."""
    monkeypatch.delenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", raising=False)
    monkeypatch.setenv("GENLAB_TMP", str(tmp_path))

    from genlab_core.learning.arm_loader import save_arm

    proxy = _FakeProxy(existing=[])
    save_arm(
        proxy,
        arm_id="style:gaming:revelation",
        alpha=1.5,
        beta=1.5,
        niche_id="gaming",
    )
    assert len(proxy.create_calls) == 1
    written = proxy.create_calls[0]
    assert written["alpha"] == 1.5
    assert written["beta"] == 1.5


def test_flag_on_no_priors_artifact_no_override(monkeypatch, tmp_path):
    """Flag on but no priors artifact yet → still no override.

    Same contract as flag-off: the consumer only fires when the
    upstream runner has produced a prior AND the flag is on."""
    monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")
    monkeypatch.setenv("GENLAB_TMP", str(tmp_path))

    from genlab_core.learning.arm_loader import save_arm

    proxy = _FakeProxy(existing=[])
    save_arm(
        proxy,
        arm_id="style:gaming:revelation",
        alpha=1.7,
        beta=1.3,
        niche_id="gaming",
    )
    written = proxy.create_calls[0]
    assert written["alpha"] == 1.7
    assert written["beta"] == 1.3


def test_flag_on_with_prior_applies_transfer(monkeypatch, tmp_path):
    """Happy path: flag on + priors artifact present + caller has
    ~Beta(1,1)+one-obs baseline → transfer applied, caller's
    observation preserved on top of transferred (α, β)."""
    monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")
    monkeypatch.setenv("GENLAB_TMP", str(tmp_path))

    # Patch get_transferred_prior directly so we don't need a real
    # artifact file. Verifies the wire calls the right function.
    from genlab_core.learning import arm_loader
    from genlab_core.learning import cross_niche_transfer as cnt

    monkeypatch.setattr(
        cnt,
        "get_transferred_prior",
        lambda niche_id, arm_id, **_: (3.0, 12.0),
    )

    proxy = _FakeProxy(existing=[])
    # Caller passes α=1.5, β=1.5 — that's "Beta(1,1) prior + one
    # reward of 0.5" pattern. Transfer should replace baseline
    # while preserving the +0.5 / +0.5 contribution.
    arm_loader.save_arm(
        proxy,
        arm_id="style:gaming:revelation",
        alpha=1.5,
        beta=1.5,
        niche_id="gaming",
    )
    written = proxy.create_calls[0]
    # Expected: α = 3.0 + (1.5 - 1.0) = 3.5
    # Expected: β = 12.0 + (1.5 - 1.0) = 12.5
    assert written["alpha"] == pytest.approx(3.5)
    assert written["beta"] == pytest.approx(12.5)


def test_bigger_caller_prior_not_overridden(monkeypatch, tmp_path):
    """When caller passes α+β ≥ 3 (e.g. from meta_prior.py's own
    warm-start transfer), the cross-niche wire deliberately does
    NOT override. This preserves independent transfer mechanisms."""
    monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")
    monkeypatch.setenv("GENLAB_TMP", str(tmp_path))

    from genlab_core.learning import arm_loader
    from genlab_core.learning import cross_niche_transfer as cnt

    monkeypatch.setattr(
        cnt,
        "get_transferred_prior",
        lambda niche_id, arm_id, **_: (3.0, 12.0),
    )

    proxy = _FakeProxy(existing=[])
    # meta_prior-style warm-start: α=5.5, β=3.5 (encodes real
    # transferred posterior from another niche + inflation)
    arm_loader.save_arm(
        proxy,
        arm_id="style:gaming:revelation",
        alpha=5.5,
        beta=3.5,
        niche_id="gaming",
    )
    written = proxy.create_calls[0]
    # Should be UNCHANGED — cross-niche wire respects meta_prior's
    # decision.
    assert written["alpha"] == 5.5
    assert written["beta"] == 3.5


def test_update_path_never_invokes_transfer(monkeypatch, tmp_path):
    """Existing arm being UPDATED (not created) — the wire must not
    fire. This is a steady-state reward update, not a cold-start
    initialisation."""
    monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")
    monkeypatch.setenv("GENLAB_TMP", str(tmp_path))

    from genlab_core.learning import arm_loader
    from genlab_core.learning import cross_niche_transfer as cnt

    called_count = {"n": 0}

    def _spy(*args, **kwargs):
        called_count["n"] += 1
        return (3.0, 12.0)

    monkeypatch.setattr(cnt, "get_transferred_prior", _spy)

    # Pre-populate the "existing" list so the arm matches an
    # existing row → save_arm goes down the update path.
    proxy = _FakeProxy(
        existing=[
            {
                "id": "existing-1",
                "fields": {
                    "arm_id": "style:gaming:revelation",
                    "niche_id": "gaming",
                    "alpha": 2.5,
                    "beta": 4.5,
                },
            }
        ]
    )

    arm_loader.save_arm(
        proxy,
        arm_id="style:gaming:revelation",
        alpha=2.7,
        beta=4.6,
        niche_id="gaming",
    )
    # Update path taken, not create — and the transfer function was
    # not called.
    assert len(proxy.update_calls) == 1
    assert len(proxy.create_calls) == 0
    assert called_count["n"] == 0
