"""Where a still comes from, and when a card must not exist."""

from __future__ import annotations

import pytest
from genlab_core.still.beats import plan_beats
from genlab_core.still.card import CARD_FIELDS, Card, NoCardData, build_card
from genlab_core.still.sourcing import CARRIED, GENERATED, build_prompt, carried_art, plan_stills

SCRIPT = [
    "A dying noblewoman meets her match.",
    "Satoko's got beauty, status, and months to live before illness claims her forever.",
    "When a mysterious assassin named Shinpei targets her, she makes him an insane proposal.",
    "She'll marry him if he protects her, but Shinpei means every word of forever.",
    "Firefly Wedding premieres October ninth, twenty twenty-six.",
]
ANIME_WPM, ANIME_RATE = 177.0, 1.05

FACTS = {
    "studio": "david production",
    "season": "FALL 2026",
    "premiere": "9 October 2026",
    "genres": ["Drama", "Romance"],
    "following": 17851,
}


@pytest.fixture
def board():
    return plan_beats(SCRIPT, wpm=ANIME_WPM, speaking_rate=ANIME_RATE)


class TestSourcing:
    def test_a_story_without_art_generates_every_shot(self, board):
        """Firefly Wedding's row holds title/summary/url and nothing else."""
        stills = plan_stills(board, {"title": "Firefly Wedding", "source": "anilist"})
        assert len(stills) == len(board.beats)
        assert {s.origin for s in stills} == {GENERATED}

    def test_carried_art_is_only_what_the_story_record_holds(self):
        """Cover art exists on AniList; the story row does not hold it.

        Fetching it and calling it "carried" would launder new material under
        someone else's licence into a path that claims the pipeline already
        had it. carried_art reads the record and nothing else.
        """
        assert carried_art({"title": "X", "source": "anilist"}) == ("", "")
        url, attrib = carried_art(
            {"key_art_url": "https://example.test/a.jpg", "source": "viz"}
        )
        assert url.startswith("http") and attrib == "viz"

    def test_carried_art_goes_to_the_hero_and_is_not_repeated(self, board):
        stills = plan_stills(
            board, {"key_art_url": "https://example.test/a.jpg", "source": "viz"}
        )
        carried = [s for s in stills if s.origin == CARRIED]
        assert len(carried) == 1, "repeating one image under every beat is a slideshow"
        assert carried[0].is_hero
        assert carried[0].needs_attribution_slate
        assert carried[0].cost_usd == 0.0

    def test_generated_stills_carry_the_measured_unit_cost(self, board):
        stills = plan_stills(board, {"title": "X"})
        assert all(s.cost_usd > 0 for s in stills)
        assert sum(s.cost_usd for s in stills) == pytest.approx(0.001 * len(stills))

    def test_the_prompt_comes_from_the_kit_template(self, board):
        p = build_prompt(board.beats[0], board.kit)
        assert board.kit["palette"]["description"].strip()[:30] in p
        assert "No text of any kind" in p

    def test_a_generated_still_never_claims_attribution(self, board):
        for s in plan_stills(board, {"title": "X"}):
            assert not s.needs_attribution_slate


class TestCardRefusesToInvent:
    def test_real_facts_make_a_card(self):
        c = build_card("Firefly Wedding", FACTS, provenance="AniList")
        assert isinstance(c, Card)
        assert len(c.rows) == 5
        assert ("Studio", "david production") in c.rows
        assert ("Following", "17,851") in c.rows, "counts are thousands-separated"

    def test_thin_data_means_no_card(self):
        with pytest.raises(NoCardData, match="at least two real facts"):
            build_card("X", {"studio": "david production"}, provenance="AniList")

    def test_no_data_at_all_means_no_card(self):
        with pytest.raises(NoCardData):
            build_card("X", {}, provenance="AniList")

    def test_facts_without_provenance_are_refused(self):
        """A card states things as fact and must say where they came from."""
        with pytest.raises(NoCardData, match="provenance"):
            build_card("X", FACTS, provenance="")

    def test_only_allowlisted_fields_reach_the_card(self):
        c = build_card(
            "X", {**FACTS, "average_score": 91, "rank": 3}, provenance="AniList"
        )
        rendered = {k for k, _ in c.rows}
        assert "Average Score" not in rendered and "Rank" not in rendered, (
            "scores and ranks move; a stale one on a card reads as a wrong one"
        )
        assert rendered <= {f.replace("_", " ").title() for f in CARD_FIELDS}

    def test_empty_values_do_not_count_toward_the_two_field_floor(self):
        with pytest.raises(NoCardData):
            build_card(
                "X", {"studio": "d", "season": "", "genres": [], "premiere": None},
                provenance="AniList",
            )


class TestReelCost:
    def test_the_whole_reel_prices_under_the_anime_cap(self, board):
        """Sum the real plan against FrameDrift's declared cap."""
        from pathlib import Path

        import yaml
        from genlab_core.capabilities.plan_cost import PlannedCall, check_plan_budget

        stills = plan_stills(board, {"title": "Firefly Wedding"})
        plan = [
            PlannedCall("tts", 1),
            PlannedCall("still", sum(1 for s in stills if s.origin == GENERATED)),
            PlannedCall("music", 1),
        ]
        root = Path(__file__).resolve().parents[3]
        cap = yaml.safe_load(
            (root / "FrameDrift/config/niche.yaml").read_text()
        )["capabilities"]["max_generation_cost_usd"]
        cost = check_plan_budget(plan, cap_usd=cap)
        assert cost.total_usd < cap
        assert cost.total_usd == pytest.approx(0.02204, abs=1e-4), cost
