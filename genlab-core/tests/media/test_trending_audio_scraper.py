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
