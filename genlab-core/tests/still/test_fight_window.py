"""Pins for the window locator (ANIME-PEAK-04 §1)."""
from __future__ import annotations

from genlab_core.still import fight_window as FW


class TestTheLocatorNeverTouchesFootage:
    def test_metadata_only_by_construction(self):
        """Fan clips are a locator, never a source. The separation has to be
        structural, not a rule someone remembers."""
        import inspect

        src = inspect.getsource(FW.fan_clip_metadata)
        assert "--flat-playlist" in src
        for forbidden in ("-o ", "--output", "download_pv", "-f best"):
            assert forbidden not in src, forbidden


class TestEpisodeConsensus:
    """The locator's most useful output, and not what it was built for."""

    def test_the_community_corrects_a_hand_entered_number(self):
        titles = ["Sukuna vs. Mahoraga Part 1 | Jujutsu Kaisen Season 2 Episode 17",
                  "Sukuna vs. Mahoraga Part 2 | Jujutsu Kaisen Season 2 Episode 17",
                  "Sukuna vs Mahoraga [BLU-RAY] Fight in Hindi"]
        c = FW.episode_consensus(titles)
        assert (c["season"], c["episode"]) == (2, 17)
        assert c["support"] == 2

    def test_a_bare_episode_number_still_counts(self):
        assert FW.episode_consensus(["Mob vs Toichiro Episode 10"])["episode"] == 10

    def test_no_episode_anywhere_reports_none(self):
        c = FW.episode_consensus(["Sukuna vs Mahoraga AMV", "best fight ever"])
        assert c["episode"] is None and c["support"] == 0

    def test_it_reports_rather_than_overwrites(self):
        """A disagreement between catalog and community is for a human to
        look at; silently rewriting the catalog would hide it."""
        import inspect

        assert "never silently overwrites" in (FW.episode_consensus.__doc__ or "")
        assert "yaml" not in inspect.getsource(FW.episode_consensus).lower()


class TestRegion:
    def test_timestamps_win_when_present(self):
        r = FW.locate(["fight starts at 14:20", "great scene 14:35"],
                      episode_runtime_s=1440.0)
        assert "timestamps" in r.basis
        assert r.start_s < 862.0 < r.end_s

    def test_the_fallback_says_it_is_a_convention(self):
        r = FW.locate(["no times here"], episode_runtime_s=1440.0,
                      clip_durations=[300.0, 310.0, 295.0])
        assert "convention" in r.basis
        assert FW.REGION_MIN_S <= r.duration_s <= FW.REGION_MAX_S

    def test_a_timestamp_outside_the_runtime_is_ignored(self):
        r = FW.locate(["bogus 99:99", "real 10:00"], episode_runtime_s=1440.0)
        assert r.start_s < 600.0 < r.end_s


class TestWindowGate:
    def _w(self, a, b):
        return FW.Window(a, b, score=1.0)

    def test_overlap_is_measured_against_the_MARK(self):
        pick, mark = self._w(100.0, 110.0), self._w(102.0, 108.0)
        assert pick.overlap(mark) == 1.0

    def test_a_partial_cover_below_the_bar_is_a_miss(self):
        r = FW.window_recall([self._w(100.0, 104.0)], [self._w(100.0, 110.0)])
        assert r["recall"] == 0.0

    def test_a_good_cover_is_a_hit(self):
        r = FW.window_recall([self._w(99.0, 111.0)], [self._w(100.0, 110.0)])
        assert r["recall"] == 1.0 and r["precision"] == 1.0

    def test_precision_falls_when_extra_windows_are_proposed(self):
        """25 picks for 2 marks is a firing rate, not a recall."""
        r = FW.window_recall([self._w(99.0, 111.0), self._w(500.0, 510.0)],
                             [self._w(100.0, 110.0)])
        assert r["recall"] == 1.0 and r["precision"] == 0.5

    def test_the_cap_exists(self):
        assert FW.MAX_WINDOWS_PER_FIGHT <= 2
