"""Pins for the footage plan gate (ANIME-17 §3)."""

from __future__ import annotations

import pytest
from genlab_core.still import plan_gate as G


class TestTheV5Failure:
    def test_the_v5_A_bundle_is_rejected(self):
        """One PV, three usable windows, one cover. v5 rendered a 32s reel
        that was 67% generated stills and passed 28 of 37 gates."""
        with pytest.raises(G.InsufficientFootage) as e:
            G.decide(3, 1, allow_teaser=False)
        assert "insufficient_footage" in str(e.value)

    def test_the_v4_A_bundle_passes(self):
        """v4 reel A had 10 usable windows across the PV plus cover, banner
        and four character images."""
        v = G.decide(10, 6)
        assert v.format == G.FULL and v.ok

    def test_a_thin_bundle_gets_a_teaser_not_a_padded_reel(self):
        v = G.decide(4, 2)
        assert v.format == G.TEASER
        assert "teaser" in v.reason and "padded" in v.reason

    def test_below_the_teaser_floor_there_is_no_reel(self):
        with pytest.raises(G.InsufficientFootage):
            G.decide(2, 1)

    def test_eight_windows_from_one_source_is_not_enough(self):
        """Eight windows from one PV is one location and one grade."""
        v = G.decide(8, 1)
        assert v.format == G.TEASER, "a single source must not qualify as full-length"


class TestKeyArtCannotCarryTheReel:
    def _shots(self, art_s, pv_s):
        return [{"index": 0, "origin": "generated_with_reference", "duration_s": art_s}] + [
            {"index": 1, "origin": "pv_peak", "duration_s": pv_s}
        ]

    def test_the_v5_share_is_caught(self):
        problems = G.check_key_art_share(self._shots(21.8, 10.7), 32.5)
        assert problems and "67%" in problems[0]

    def test_a_healthy_share_passes(self):
        """Two key-art shots inside the 4s hold cap, 18% of a 32s reel.
        A single 6s shot at the same SHARE still fails — the hold cap and the
        share cap catch different things."""
        shots = [
            {"index": 0, "origin": "cover", "duration_s": 3.0},
            {"index": 1, "origin": "character", "duration_s": 3.0},
            {"index": 2, "origin": "pv_peak", "duration_s": 26.0},
        ]
        assert G.check_key_art_share(shots, 32.0) == []
        one_long = [
            {"index": 0, "origin": "cover", "duration_s": 6.0},
            {"index": 1, "origin": "pv_peak", "duration_s": 26.0},
        ]
        assert G.check_key_art_share(one_long, 32.0)

    def test_a_long_hold_is_caught_even_at_a_small_share(self):
        problems = G.check_key_art_share(
            [
                {"index": 3, "origin": "cover", "duration_s": 5.0},
                {"index": 4, "origin": "pv_peak", "duration_s": 40.0},
            ],
            45.0,
        )
        assert any("holds key art" in p for p in problems)
