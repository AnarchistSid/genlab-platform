"""An empty narration script can never report success.

ANIME-03 §1. `narration_degraded` is a boolean, and a boolean cannot say
"we never tried". Measured across 45 days of production blueprints:

    niche         has key   non-empty script   narration_degraded
    ai_creators        77                 20   mixed (48 script_generation_failed)
    anime              53                  0   false
    gaming             87                  0   false
    movies            102                  0   false
    sports             90                  0   false

Four niches, zero scripts between them, every row reading the same `false` a
healthy narrated row carries. Narration was a BlackboxBrief-only canary, so
the absence was correct behaviour reported as success — false green, and the
same shape as "empty and false" elsewhere in this repo.

`narration_state` carries the third value. The boolean stays and stays
accurate, because downstream readers do `bool(narration_degraded)` and a
truthy "ok" string would invert every one of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from genlab_core.pipeline.stages.push_to_backlog import (
    NARRATION_NOT_ATTEMPTED,
    NARRATION_OK,
    _narration_state,
)

_ROOT = Path(__file__).resolve().parents[2]


class TestTriState:
    @pytest.mark.parametrize(
        "content",
        [
            {},
            {"narration_script": ""},
            {"narration_script": "   "},
            {"narration_script": "", "narration_degraded": False},
            {"narration_script": None, "narration_degraded": False},
        ],
    )
    def test_an_empty_script_is_never_ok(self, content):
        """The exact row 332 anime/gaming/movies/sports blueprints carried."""
        state = _narration_state(content)
        assert state == NARRATION_NOT_ATTEMPTED, state
        assert state != NARRATION_OK
        assert state is not False

    def test_a_real_script_is_ok(self):
        assert _narration_state({"narration_script": "Satoko has months to live."}) == NARRATION_OK

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ({"narration_script": "x", "narration_degraded": True,
              "narration_degraded_reason": "vo_overrun"}, "degraded:vo_overrun"),
            ({"narration_script": "x", "narration_degraded": True,
              "narration_degraded_reason": "storytime_mutex"}, "degraded:storytime_mutex"),
        ],
    )
    def test_degraded_carries_its_reason(self, content, expected):
        assert _narration_state(content) == expected

    def test_degraded_without_a_reason_still_says_degraded(self):
        """Never let a missing reason collapse into a healthy-looking state."""
        assert _narration_state({"narration_script": "x", "narration_degraded": True}).startswith(
            "degraded:"
        )

    def test_a_degraded_row_with_an_empty_script_is_degraded_not_not_attempted(self):
        """Attempted-and-failed is not the same as never-attempted."""
        state = _narration_state(
            {"narration_script": "", "narration_degraded": True,
             "narration_degraded_reason": "script_generation_failed"}
        )
        assert state == "degraded:script_generation_failed"
        assert state != NARRATION_NOT_ATTEMPTED


class TestAnimeEnrolled:
    def test_anime_declares_narration(self):
        cfg = yaml.safe_load((_ROOT / "FrameDrift/config/niche.yaml").read_text())
        assert cfg["narration"]["enabled"] is True

    def test_anime_did_not_raise_the_prediction_rate_to_hit_a_register(self):
        """165-180 WPM is a DELIVERY target and belongs to speaking_rate.

        `wpm` predicts duration from word count and is measured per TTS tier
        (inworld=141, from two full-chain runs). Raising it to 172 to express
        a faster register would under-predict duration by ~22%, so scripts
        would be written long and thrown away as vo_overrun — which is what
        narration_gate's own "do not guess them" note warns against.
        """
        cfg = yaml.safe_load((_ROOT / "FrameDrift/config/niche.yaml").read_text())
        n = cfg["narration"]
        # MEASURED on rendered audio, voice Sarah, the real script:
        #   rate 1.00 -> 157-167 wpm   rate 1.05 -> 177.4   rate 1.22 -> 201.4
        # An earlier version of this pin asserted wpm == 141, the figure
        # narration_gate records. Measurement showed inworld delivers ~162 at
        # rate 1.0 on this voice, so 141 was ~14% low — and 1.22, derived from
        # it to reach 165-180, overshot to 201.
        assert n["wpm"] == 177, "prediction rate must be the MEASURED one"
        assert 165 <= n["wpm"] <= 180, "the register the packet asks for"
        assert n["speaking_rate"] == 1.05
        assert n["voice_id"], (
            "wpm is measured per voice; an unnamed voice makes the number "
            "describe nothing"
        )
