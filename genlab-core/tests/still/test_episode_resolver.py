"""Pins for authoritative episode resolution (ANIME-PEAK-05 §1)."""
from __future__ import annotations

from genlab_core.still import episode_resolver as ER


def _eps(pairs):
    return [ER.Episode(n, t, source="anilist") for n, t in pairs]


class TestMatching:
    def test_an_alias_that_is_the_episode_title_resolves_high(self):
        """The one case that worked across 13 fights: Demon Slayer ep 19 is
        known as "Hinokami", and that is its episode title."""
        r = ER.match("tanjiro_vs_rui",
                     _eps([(18, "Episode 18 - A Forged Bond"),
                           (19, "Episode 19 - Hinokami")]),
                     aka=["Hinokami", "Dance of the Fire God"],
                     combatants=["Tanjiro", "Rui"])
        assert r.episode == 19 and r.confidence == "high"
        assert "alias" in r.evidence

    def test_both_combatants_in_a_title_resolves_medium(self):
        r = ER.match("x", _eps([(5, "Episode 5 - Zoro versus King")]),
                     aka=[], combatants=["Zoro", "King"])
        assert r.episode == 5 and r.confidence == "medium"

    def test_one_combatant_is_not_a_match(self):
        """"Yuji Has Become a War God" was once accepted for Yuta vs Yuji."""
        r = ER.match("x", _eps([(7, "Yuji Has Become a War God")]),
                     aka=[], combatants=["Yuta", "Yuji"])
        assert r.episode is None

    def test_no_match_is_unresolved_not_a_guess(self):
        r = ER.match("x", _eps([(1, "Episode 1 - Cruelty"), (2, "Episode 2 - Trainer")]),
                     aka=[], combatants=["Zoro", "King"])
        assert not r.resolved
        assert "no alias and no two-combatant" in r.evidence

    def test_an_empty_episode_list_is_reported_as_such(self):
        r = ER.match("x", [], aka=["Hinokami"], combatants=["A", "B"])
        assert not r.resolved and "no episode list" in r.evidence

    def test_a_short_alias_cannot_match(self):
        """A three-character alias would match half of every episode list."""
        r = ER.match("x", _eps([(3, "Episode 3 - The Cat")]), aka=["Cat"],
                     combatants=["A", "B"])
        assert r.episode is None


class TestShowLookupIsValidated:
    def test_the_docstring_records_the_onigiri_failure(self):
        """AniList's search returned "Onigiri" for "Demon Slayer" — a
        13-episode show with nothing to do with it. Taking the first hit
        resolves every fight against another show's episode list."""
        doc = ER.find_show.__doc__ or ""
        assert "Onigiri" in doc and "Kimetsu no Yaiba" in doc
