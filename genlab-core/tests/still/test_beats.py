"""Storyboard from the script's beats, with Firefly Wedding as the fixture.

Three bugs were found building this, each by running it rather than reading
it, and each is pinned below:

1. The kit's max_shot_s (5.0) exceeded its own longest_static gate (4.0), so
   every reel would have failed the gate it shipped with.
2. Clamping a split shot up to min_shot_s made 19.95 s of narration into
   24.0 s of video — each still drifting away from the sentence it
   illustrates. Shot duration must FOLLOW the narration.
3. min 3.0 / max 4.0 is infeasible: a 4.53 s sentence fits neither n=1
   (over max) nor n=2 (under min), and the splitter broke the minimum
   silently instead of refusing.
"""

from __future__ import annotations

import copy

import pytest
from genlab_core.still.beats import Storyboard, effective_wpm, load_kit, plan_beats

# The real script generated for blueprint 7bb1d5ae (Firefly Wedding).
FIREFLY = [
    "A dying noblewoman meets her match.",
    "Satoko's got beauty, status, and months to live before illness claims her forever.",
    "When a mysterious assassin named Shinpei targets her, she makes him an insane proposal.",
    "She'll marry him if he protects her, but Shinpei means every word of forever.",
    "Firefly Wedding premieres October ninth, twenty twenty-six.",
]

ANIME_WPM = 141.0  # measured for the inworld tier
ANIME_RATE = 1.22  # delivery multiplier for the 165-180 register


@pytest.fixture
def board() -> Storyboard:
    return plan_beats(FIREFLY, wpm=ANIME_WPM, speaking_rate=ANIME_RATE)


class TestPredictionVsDelivery:
    def test_effective_wpm_multiplies_the_measured_rate(self):
        assert effective_wpm(141, 1.22) == pytest.approx(172.0, abs=0.5)

    def test_speaking_rate_of_one_leaves_the_measured_rate_alone(self):
        assert effective_wpm(141, 1.0) == 141

    @pytest.mark.parametrize(("wpm", "rate"), [(0, 1.0), (-5, 1.0), (141, 0), (141, -1)])
    def test_nonsense_rates_are_refused(self, wpm, rate):
        with pytest.raises(ValueError):
            effective_wpm(wpm, rate)


class TestAudioVideoSync:
    def test_video_length_matches_the_narration(self, board):
        """The bug: clamping made 19.95 s of speech into 24.0 s of video."""
        narration = sum(b.spoken_s for b in board.beats)
        assert board.total_s == pytest.approx(narration, abs=0.01), (
            f"video {board.total_s}s vs narration {narration}s — the stills "
            "would drift away from the sentences they illustrate"
        )

    def test_shots_are_contiguous(self, board):
        for prev, nxt in zip(board.beats, board.beats[1:], strict=False):
            assert nxt.start_s == pytest.approx(prev.end_s, abs=0.01)

    def test_a_split_sentence_keeps_its_text_on_every_shot(self, board):
        for b in board.beats:
            if b.shot_of[1] > 1:
                same = [x for x in board.beats if x.text == b.text]
                assert len(same) == b.shot_of[1]


class TestGates:
    def test_longest_static_is_inside_the_kit_gate(self, board):
        assert board.longest_static_s <= board.kit["gates"]["longest_static_s"]

    def test_no_shot_is_shorter_than_the_kit_minimum(self, board):
        assert min(b.shot_s for b in board.beats) >= board.kit["beats"]["min_shot_s"]

    def test_shot_count_is_in_range(self, board):
        b = board.kit["beats"]
        assert b["count_min"] <= len(board.beats) <= b["count_max"]

    def test_the_hook_is_the_first_shot_and_lands_by_1_5s(self, board):
        hook = board.beats[0]
        assert hook.is_hook
        assert hook.start_s == 0.0
        assert hook.start_s <= board.kit["gates"]["hook_on_screen_by_s"]


class TestKitIsRefusedWhenInfeasible:
    def test_max_below_twice_min_is_refused(self, monkeypatch):
        """min 3.0 / max 4.0 leaves 4.0-6.0 s sentences with no valid split."""
        import genlab_core.still.beats as mod

        bad = copy.deepcopy(load_kit("anime"))
        bad["beats"]["min_shot_s"] = 3.0
        bad["beats"]["max_shot_s"] = 4.0
        monkeypatch.setattr(mod.yaml, "safe_load", lambda _t: bad)
        with pytest.raises(ValueError, match="at least"):
            mod.load_kit("anime")

    def test_max_shot_above_the_longest_static_gate_is_refused(self, monkeypatch):
        import genlab_core.still.beats as mod

        bad = copy.deepcopy(load_kit("anime"))
        bad["beats"]["max_shot_s"] = 5.0
        bad["gates"]["longest_static_s"] = 4.0
        monkeypatch.setattr(mod.yaml, "safe_load", lambda _t: bad)
        with pytest.raises(ValueError, match="longest_static"):
            mod.load_kit("anime")

    def test_a_missing_kit_names_itself(self):
        with pytest.raises(FileNotFoundError, match="no STILL kit"):
            load_kit("no_such_niche")

    def test_the_shipped_kit_satisfies_its_own_invariants(self):
        k = load_kit("anime")
        assert k["beats"]["max_shot_s"] >= 2 * k["beats"]["min_shot_s"]
        assert k["beats"]["max_shot_s"] <= k["gates"]["longest_static_s"]
        assert -10 <= k["audio"]["music_duck_db"] <= -6, "duck must be 6-10 dB"


class TestCraft:
    def test_ken_burns_direction_alternates(self, board):
        pats = [board.pattern_for(b.index) for b in board.beats]
        assert all(a != b for a, b in zip(pats, pats[1:], strict=False)), pats

    def test_the_hero_is_the_beat_that_names_the_subject(self, board):
        assert "Shinpei" in board.hero.names

    def test_numbers_are_found_for_kinetic_type(self, board):
        last = board.beats[-1]
        assert last.numbers, "the premiere date must trigger kinetic type"

    def test_a_sentence_initial_capital_is_not_a_name(self):
        b = plan_beats(["Satoko waits.", "Winter arrives soon."], wpm=141, speaking_rate=1.0)
        assert "Winter" not in b.beats[-1].names

    def test_an_empty_script_is_refused(self):
        with pytest.raises(ValueError, match="no sentences"):
            plan_beats(["", "   "], wpm=141, speaking_rate=1.0)
