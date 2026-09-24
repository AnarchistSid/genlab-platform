"""Pins for the edit lane's source gate and selector (ANIME-PEAK-01 §1, §2)."""

from __future__ import annotations

from genlab_core.still import fight_moment as FM
from genlab_core.still import fight_source as FS


class TestTheSourceGate:
    def test_licensed_regional_channels_are_allowed(self):
        """Muse Asia and Ani-One are the two that matter for this audience."""
        assert FS.channel_tier("Muse Asia") == "licensed_regional"
        assert FS.channel_tier("Ani-One Asia") == "licensed_regional"
        assert FS.channel_tier("Crunchyroll Collection") == "official"

    def test_a_fan_channel_is_not_allowed(self):
        for name in ("Anime Vibe", "SomeGuy AMV", "Best Anime Fights HD", ""):
            assert FS.channel_tier(name) == ""

    def test_an_unknown_studio_defaults_to_hostile(self):
        """The alternative defaults a show nobody thought about to the
        loosest handling available."""
        assert FS.studio_posture("Some Show We Have Not Seen")[1] == FS.HOSTILE

    def test_the_known_hostile_studios(self):
        assert FS.studio_posture("One Piece") == ("toei", FS.HOSTILE)
        assert FS.studio_posture("Jujutsu Kaisen")[1] == FS.HOSTILE


class TestTheFootageMustBeTheFight:
    """The channel gate says the uploader is official. It says nothing about
    WHICH footage this is — measured on the pilot, three of five 'passes'
    were a Broly clip, a season trailer, and a different scene."""

    def test_the_broly_clip_is_refused_for_goku_vs_frieza(self):
        assert not FS.names_the_fight(
            "Dragon Ball Z: Broly - The Legendary Super Saiyan", "Goku vs Frieza", "Dragon Ball Z"
        )

    def test_a_trailer_is_never_a_fight(self):
        assert not FS.names_the_fight(
            "JUJUTSU KAISEN Shibuya Incident | OFFICIAL TRAILER",
            "Maki vs the Zenin",
            "Jujutsu Kaisen",
        )

    def test_one_combatant_is_not_enough(self):
        assert not FS.names_the_fight(
            "Yuji Has Become a War God | JUJUTSU KAISEN", "Yuta vs Yuji", "Jujutsu Kaisen"
        )

    def test_the_real_fight_passes(self):
        assert FS.names_the_fight("Zoro vs King | One Piece", "Zoro vs King", "One Piece")
        assert FS.names_the_fight(
            "Snake-Man Luffy vs Kaido | One Piece", "Luffy vs Kaido", "One Piece"
        )


class TestTheSequence:
    def test_the_window_stays_inside_the_band(self):
        b = FM.Beat(t=60.0, luma_jump=90.0, audio_step=6.0, motion=100.0, score=2.4)
        s = FM.sequence_for(b, duration_s=120.0)
        assert FM.WINDOW_MIN_S <= s.duration_s <= FM.WINDOW_MAX_S
        assert s.start_s < b.t < s.end_s

    def test_the_windup_is_bounded(self):
        b = FM.Beat(t=60.0, luma_jump=90.0, audio_step=6.0, motion=100.0, score=2.4)
        s = FM.sequence_for(b, 120.0, windup_s=30.0)
        assert b.t - s.start_s <= FM.WINDUP_MAX_S

    def test_an_impact_near_the_start_does_not_run_negative(self):
        b = FM.Beat(t=1.0, luma_jump=90.0, audio_step=6.0, motion=100.0, score=2.4)
        s = FM.sequence_for(b, 60.0)
        assert s.start_s >= 0.0


class TestCalibrationHonesty:
    def test_marks_taken_from_the_picks_are_flagged_circular(self):
        """The first calibration run reported a 100% hit rate at a median
        error of 0.002s — because the marks were the picks."""
        picks = [57.43, 62.52, 67.03]
        cal = FM.calibrate(picks, list(picks))
        assert cal["circular"] is True
        assert "precision only" in cal["established"]

    def test_independent_marks_are_not_flagged(self):
        cal = FM.calibrate([57.43, 62.52], [57.30, 62.70])
        assert cal["circular"] is False
        assert cal["hit_rate"] == 1.0

    def test_a_missed_impact_shows_as_a_miss_not_an_average(self):
        """A mean error hides one catastrophic miss among good hits, and one
        catastrophic miss is a reel built on the wrong second."""
        cal = FM.calibrate([57.4, 62.5], [57.4, 62.5, 91.0])
        assert cal["within_tolerance"] == 2 and cal["marks"] == 3
        assert cal["worst_s"] > 1.0
