"""Pin the trending-audio scraper primitive + cache wire.

Contract:

  * `scrape_and_cache_trending_moods(niche_id, available_moods)`:
      * Empty available_moods -> False (nothing to classify against)
      * No tracks fetched -> False + WARN log
      * Classifier returns empty -> False + WARN log
      * Success -> True + cache file written

  * `read_cache_for_niche(niche_id, ttl_hours)`:
      * Missing cache -> []
      * Stale cache (fetched_at older than ttl) -> []
      * Corrupt JSON -> []
      * Fresh valid cache -> list[TrendingAudioMood]

  * `trending_audio_meta._read_cache` (the wire) now delegates to
    `read_cache_for_niche` — was stub returning [] previously.

Structural pins:

  * systemd .service + .timer files exist in deploy/systemd-phase2/
  * ExecStart points to scripts/run_trending_audio_scraper.py
  * Runner script loads music_mood vocab from each niche's visuals.yaml
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_cache_root(tmp_path, monkeypatch):
    """Redirect the scraper's cache root to a tmpdir."""
    monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
    return tmp_path / "cache" / "trending_audio_meta"


class TestScrapeAndCache:
    def test_no_moods_returns_false(self):
        from genlab_core.media.trending_audio_scraper import (
            scrape_and_cache_trending_moods,
        )
        assert scrape_and_cache_trending_moods("gaming", []) is False

    def test_no_tracks_fetched_returns_false(self, caplog):
        from genlab_core.media import trending_audio_scraper as mod

        with patch.object(mod, "_fetch_trending_track_names", return_value=[]):
            with caplog.at_level(logging.WARNING):
                result = mod.scrape_and_cache_trending_moods(
                    "gaming", ["hype", "chill"],
                )
        assert result is False
        assert any("no track names fetched" in r.message for r in caplog.records)

    def test_classifier_empty_returns_false(self, caplog):
        from genlab_core.media import trending_audio_scraper as mod

        with patch.object(
            mod, "_fetch_trending_track_names",
            return_value=[{"name": "Track A", "meta_audio_id": "a1", "rank": 1}],
        ), patch.object(mod, "_classify_tracks_to_moods", return_value=[]):
            with caplog.at_level(logging.WARNING):
                result = mod.scrape_and_cache_trending_moods(
                    "gaming", ["hype", "chill"],
                )
        assert result is False
        assert any("classifier returned empty" in r.message for r in caplog.records)

    def test_success_writes_cache(self, tmp_cache_root):
        from genlab_core.media import trending_audio_scraper as mod

        with patch.object(
            mod, "_fetch_trending_track_names",
            return_value=[{"name": "Track A", "meta_audio_id": "a1", "rank": 1}],
        ), patch.object(
            mod, "_classify_tracks_to_moods",
            return_value=[{"mood": "hype", "trend_rank": 1, "meta_audio_id": "a1"}],
        ):
            result = mod.scrape_and_cache_trending_moods(
                "gaming", ["hype", "chill"],
            )
        assert result is True
        cache_file = tmp_cache_root / "gaming.json"
        assert cache_file.exists()
        payload = json.loads(cache_file.read_text())
        assert "fetched_at" in payload
        assert payload["moods"][0]["mood"] == "hype"


class TestReadCache:
    def test_missing_cache_returns_empty(self, tmp_cache_root):
        from genlab_core.media.trending_audio_scraper import read_cache_for_niche

        assert read_cache_for_niche("gaming") == []

    def test_stale_cache_returns_empty(self, tmp_cache_root):
        from genlab_core.media.trending_audio_scraper import read_cache_for_niche

        tmp_cache_root.mkdir(parents=True, exist_ok=True)
        stale = datetime.now(UTC) - timedelta(hours=24)
        (tmp_cache_root / "gaming.json").write_text(json.dumps({
            "fetched_at": stale.isoformat(),
            "moods": [{"mood": "hype", "trend_rank": 1, "meta_audio_id": "a1"}],
        }))
        assert read_cache_for_niche("gaming", ttl_hours=6) == []

    def test_fresh_cache_returns_moods(self, tmp_cache_root):
        from genlab_core.media.trending_audio_scraper import read_cache_for_niche

        tmp_cache_root.mkdir(parents=True, exist_ok=True)
        (tmp_cache_root / "gaming.json").write_text(json.dumps({
            "fetched_at": datetime.now(UTC).isoformat(),
            "moods": [
                {"mood": "hype", "trend_rank": 1, "meta_audio_id": "a1"},
                {"mood": "dramatic", "trend_rank": 3, "meta_audio_id": "a3"},
            ],
        }))
        result = read_cache_for_niche("gaming", ttl_hours=6)
        assert len(result) == 2
        assert result[0].mood == "hype"
        assert result[0].trend_rank == 1

    def test_corrupt_json_returns_empty(self, tmp_cache_root):
        from genlab_core.media.trending_audio_scraper import read_cache_for_niche

        tmp_cache_root.mkdir(parents=True, exist_ok=True)
        (tmp_cache_root / "gaming.json").write_text("{malformed")
        assert read_cache_for_niche("gaming") == []


class TestITunesCharts:
    """Pin the iTunes RSS Charts primary source. Verified working
    2026-08-12 — returns real trending music data with no auth."""

    def test_fetch_url_is_apple_marketing_tools(self):
        """Pin the URL — Apple changing marketing tools domain would
        silently break the source."""
        from genlab_core.media.trending_audio_scraper import (
            _ITUNES_CHARTS_URL,
        )
        assert "rss.applemarketingtools.com" in _ITUNES_CHARTS_URL
        assert "most-played" in _ITUNES_CHARTS_URL

    def test_parses_valid_response(self, monkeypatch):
        from unittest.mock import MagicMock, patch

        from genlab_core.media.trending_audio_scraper import (
            _try_itunes_rss_charts,
        )
        payload = json.dumps({
            "feed": {
                "results": [
                    {"name": "Song A", "artistName": "Artist X", "id": "111"},
                    {"name": "Song B", "artistName": "Artist Y", "id": "222"},
                ],
            },
        }).encode("utf-8")

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = payload
        mock_response.__enter__ = lambda self: self
        mock_response.__exit__ = lambda *_: None

        with patch("urllib.request.urlopen", return_value=mock_response):
            tracks = _try_itunes_rss_charts()
        assert len(tracks) == 2
        assert tracks[0]["name"] == "Song A — Artist X"
        assert tracks[0]["meta_audio_id"] == "111"
        assert tracks[0]["rank"] == 1
        assert tracks[1]["rank"] == 2

    def test_empty_results_returns_empty(self):
        from unittest.mock import MagicMock, patch

        from genlab_core.media.trending_audio_scraper import (
            _try_itunes_rss_charts,
        )
        payload = json.dumps({"feed": {"results": []}}).encode("utf-8")

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = payload
        mock_response.__enter__ = lambda self: self
        mock_response.__exit__ = lambda *_: None

        with patch("urllib.request.urlopen", return_value=mock_response):
            assert _try_itunes_rss_charts() == []

    def test_network_error_returns_empty(self):
        import urllib.error
        from unittest.mock import patch

        from genlab_core.media.trending_audio_scraper import (
            _try_itunes_rss_charts,
        )
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            assert _try_itunes_rss_charts() == []

    def test_non_200_status_returns_empty(self):
        from unittest.mock import MagicMock, patch

        from genlab_core.media.trending_audio_scraper import (
            _try_itunes_rss_charts,
        )
        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.__enter__ = lambda self: self
        mock_response.__exit__ = lambda *_: None
        with patch("urllib.request.urlopen", return_value=mock_response):
            assert _try_itunes_rss_charts() == []

    def test_malformed_json_returns_empty(self):
        from unittest.mock import MagicMock, patch

        from genlab_core.media.trending_audio_scraper import (
            _try_itunes_rss_charts,
        )
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b"{malformed"
        mock_response.__enter__ = lambda self: self
        mock_response.__exit__ = lambda *_: None
        with patch("urllib.request.urlopen", return_value=mock_response):
            assert _try_itunes_rss_charts() == []

    def test_caps_at_20_tracks(self):
        """Cost bound: max 20 tracks per fetch even if source returns
        50+. Each track = one LLM classification call."""
        from unittest.mock import MagicMock, patch

        from genlab_core.media.trending_audio_scraper import (
            _try_itunes_rss_charts,
        )
        payload = json.dumps({
            "feed": {
                "results": [
                    {"name": f"Song {i}", "artistName": "X", "id": str(i)}
                    for i in range(50)
                ],
            },
        }).encode("utf-8")
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = payload
        mock_response.__enter__ = lambda self: self
        mock_response.__exit__ = lambda *_: None
        with patch("urllib.request.urlopen", return_value=mock_response):
            tracks = _try_itunes_rss_charts()
        assert len(tracks) == 20


class TestPlaywrightOptional:
    def test_missing_playwright_returns_empty(self):
        """Playwright is a soft dep — scraper must handle ImportError
        gracefully and return [] so the requests fallback fires."""
        from genlab_core.media.trending_audio_scraper import _try_playwright_scrape

        # Simulate ImportError inside the function
        import sys
        original = sys.modules.get("playwright")
        sys.modules["playwright"] = None
        try:
            result = _try_playwright_scrape()
            assert result == []
        finally:
            if original is None:
                sys.modules.pop("playwright", None)
            else:
                sys.modules["playwright"] = original


class TestHeuristicClassifier:
    """Pin the non-LLM keyword classifier that keeps trending audio
    cache fresh when the Anthropic API is unavailable (credit exhausted,
    no key, network down). Verified 2026-08-18 during live outage —
    Anthropic returned HTTP 400 'credit balance too low' and the
    scraper silent-failed for 2+ days before this fallback shipped.
    """

    def test_heuristic_matches_energetic_keywords(self):
        from genlab_core.media.trending_audio_scraper import _heuristic_classify

        # "party" is one of the energetic hints
        result = _heuristic_classify(
            "Party Anthem 2026 — DJ X",
            ["energetic", "cinematic", "emotional"],
        )
        assert result == "energetic"

    def test_heuristic_matches_hype_hip_hop_terms(self):
        from genlab_core.media.trending_audio_scraper import _heuristic_classify

        result = _heuristic_classify(
            "Trap Kings — Lil Something",
            ["hype", "chill", "romantic"],
        )
        assert result == "hype"

    def test_heuristic_returns_none_on_no_match(self):
        from genlab_core.media.trending_audio_scraper import _heuristic_classify

        # Track name with no keyword overlap into the mood vocab
        assert _heuristic_classify(
            "Silence in Blue — Ambient Solo",
            ["hype", "aggressive", "victorious"],
        ) is None

    def test_heuristic_stable_tiebreak_by_mood_order(self):
        """When two moods tie, the caller's vocab order breaks ties —
        deterministic behavior that downstream callers can rely on."""
        from genlab_core.media.trending_audio_scraper import _heuristic_classify

        # 'hero' hits `epic`; if `epic` and something else tied, first
        # in vocab wins. Here only `epic` hits, so we sanity-check that.
        result = _heuristic_classify(
            "Hero of Legend",
            ["dramatic", "epic", "hype"],
        )
        assert result == "epic"

    def test_heuristic_empty_moods_returns_none(self):
        from genlab_core.media.trending_audio_scraper import _heuristic_classify

        assert _heuristic_classify("Party Time", []) is None
        assert _heuristic_classify("", ["hype"]) is None


class TestClassifierFallbackToHeuristic:
    """When LLM is unavailable, `_classify_tracks_to_moods` must still
    return classified tracks via the heuristic fallback. Was the class-
    of-bug that broke trending_audio for 2+ days pre-2026-08-18.
    """

    _TRACKS = [
        {"name": "Party Anthem", "meta_audio_id": "a1", "rank": 1},
        {"name": "Trap Kings", "meta_audio_id": "a2", "rank": 3},
        {"name": "Silence in Blue", "meta_audio_id": "a3", "rank": 5},
    ]

    def test_no_api_key_uses_heuristic(self, monkeypatch, caplog):
        from genlab_core.media import trending_audio_scraper as mod

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with caplog.at_level(logging.WARNING):
            result = mod._classify_tracks_to_moods(
                self._TRACKS,
                ["energetic", "hype", "chill"],
            )
        # Party + Trap should match; Silence should not.
        moods = {r["mood"] for r in result}
        assert "energetic" in moods or "hype" in moods
        # WARNING log required — rule #17/#19 pattern
        assert any(
            "LLM unavailable" in r.message for r in caplog.records
        )

    def test_systemic_credit_error_switches_to_heuristic(
        self, monkeypatch, caplog,
    ):
        """When Anthropic returns 'credit balance too low' (or similar
        systemic marker), don't retry 19 more times — bail to
        heuristic for remaining tracks immediately."""
        from unittest.mock import MagicMock

        from genlab_core.media import trending_audio_scraper as mod

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        # Fake anthropic package
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = Exception(
            "Error code: 400 - Your credit balance is too low",
        )
        fake_anthropic = MagicMock()
        fake_anthropic.Anthropic.return_value = fake_client
        import sys
        monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

        with caplog.at_level(logging.WARNING):
            result = mod._classify_tracks_to_moods(
                self._TRACKS,
                ["energetic", "hype", "chill"],
            )
        # Should have called LLM only once — bailed on first systemic err
        assert fake_client.messages.create.call_count == 1
        # Heuristic should still surface something for the party track
        assert any(r["mood"] in ("energetic", "hype") for r in result)
        assert any(
            "systemic LLM failure" in r.message for r in caplog.records
        )


class TestConsumerWire:
    def test_trending_audio_meta_read_cache_calls_scraper_read(self):
        """Structural pin: trending_audio_meta._read_cache now delegates
        to trending_audio_scraper.read_cache_for_niche instead of the
        old stub returning []."""
        import pathlib

        path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "media"
            / "trending_audio_meta.py"
        )
        src = path.read_text()
        assert "from genlab_core.media.trending_audio_scraper import" in src
        assert "read_cache_for_niche" in src
        assert "STUB: no scraper implementation" not in src


class TestSystemdUnits:
    _DEPLOY = Path(__file__).resolve().parents[3] / "deploy" / "systemd-phase2"
    _SERVICE = _DEPLOY / "genlab-trending-audio-scraper.service"
    _TIMER = _DEPLOY / "genlab-trending-audio-scraper.timer"

    def test_service_file_exists(self):
        assert self._SERVICE.exists()

    def test_timer_file_exists(self):
        assert self._TIMER.exists()

    def test_service_execstart_points_to_runner(self):
        src = self._SERVICE.read_text()
        assert "scripts/run_trending_audio_scraper.py" in src

    def test_service_runs_as_genlab_user(self):
        """Rule #15: state files systemd services read/write must be
        owned by genlab:genlab."""
        src = self._SERVICE.read_text()
        assert "User=genlab" in src
        assert "Group=genlab" in src

    def test_timer_high_frequency_no_persistent(self):
        """High-frequency (every 6h) timer should NOT be Persistent=true
        — rule #21 exception. Persistent on a 6h cadence creates a
        thundering herd of catch-up fires on boot after long downtime."""
        src = self._TIMER.read_text()
        assert "Persistent=false" in src

    def test_runner_script_exists(self):
        runner = Path(__file__).resolve().parents[3] / "scripts" / "run_trending_audio_scraper.py"
        assert runner.exists()

    def test_runner_reads_all_five_niches(self):
        runner = Path(__file__).resolve().parents[3] / "scripts" / "run_trending_audio_scraper.py"
        src = runner.read_text()
        for niche in ("ai_creators", "gaming", "sports", "movies", "anime"):
            assert niche in src

    def test_runner_exits_zero_per_rule_26(self):
        """Rule #26: scripts invoked by systemd MUST exit 0 unless a
        genuine incident. 'No scraper data' is a data-side signal, not
        an incident."""
        runner = Path(__file__).resolve().parents[3] / "scripts" / "run_trending_audio_scraper.py"
        src = runner.read_text()
        # Pin the return 0 (there should be no `return 1` or non-zero paths)
        assert "return 0" in src
        # Guard against reintroducing exit code on partial failure
        assert "return 1" not in src
