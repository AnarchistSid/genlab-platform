"""Pin the top-creator prior refit runner (B.2, 2026-07-08).

Contract:
  1. Flag off → no-op exit 0 (no HTTP calls made)
  2. YOUTUBE_API_KEY missing → exit 1
  3. All niches fetch failure → exit 2
  4. Successful refit → writes per-niche JSON artifact with
     ``correlations`` dict + ``video_count`` + ``generated_at``
  5. < 3 videos returned → niche skipped as low-volume (not failed)
  6. Empty correlation dict from B.1 → niche skipped as low-volume
  7. Dry-run → no artifacts written but stats still counted
  8. Artifact filename includes UTC date + niche_id

Uses mock ``requests.get`` — no live YouTube API.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "refit_top_creator_priors.py"


def _load_script_module():
    """Load the script as a module. ``scripts/`` is not a Python
    package (matches the A.2 convention)."""
    import sys

    spec = importlib.util.spec_from_file_location("refit_top_creator_priors", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["refit_top_creator_priors"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def script_module():
    return _load_script_module()


def _make_videos_response(count: int = 24) -> MagicMock:
    """Realistic mostPopular response with N items. Each item has
    the ``snippet`` + ``statistics`` sub-dicts that B.1's extractor
    accepts natively.

    View counts are deliberately varied (10k..1M) + title lengths
    varied so Spearman finds a signal — a flat set would return
    zero correlation and get skipped as low-volume.
    """
    items = []
    for i in range(count):
        items.append(
            {
                "snippet": {
                    "title": f"Video {i} " + ("Q?" if i % 3 == 0 else "Statement"),
                    "description": ("A" * (100 + i * 5)) + f" desc{i}",
                    "tags": ["tag1", "tag2"],
                    "publishedAt": "2026-07-06T14:00:00Z",
                },
                "statistics": {"viewCount": str(10000 + i * 40000)},
            }
        )
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"items": items}
    return resp


def _make_empty_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"items": []}
    return resp


def _make_error_response(code: int = 403) -> MagicMock:
    resp = MagicMock()
    resp.status_code = code
    return resp


class TestFlagAndKeyGuards:
    def test_flag_off_is_noop(self, script_module, monkeypatch):
        """Flag off → exit 0, no HTTP calls."""
        monkeypatch.setattr("sys.argv", ["refit_top_creator_priors.py"])
        monkeypatch.delenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", raising=False)
        with patch("requests.get") as mock_get:
            result = script_module.main()
        assert result == 0
        assert mock_get.call_count == 0

    def test_missing_api_key_exits_1(self, script_module, monkeypatch):
        monkeypatch.setattr("sys.argv", ["refit_top_creator_priors.py"])
        monkeypatch.setenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", "true")
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        with patch("requests.get") as mock_get:
            result = script_module.main()
        assert result == 1
        assert mock_get.call_count == 0

    def test_flag_only_matches_lowercase_true(self, script_module, monkeypatch):
        """``"1"`` / ``"yes"`` do NOT enable — same rule as every
        other intelligence runner."""
        monkeypatch.setattr("sys.argv", ["refit_top_creator_priors.py"])
        monkeypatch.setenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", "1")
        with patch("requests.get") as mock_get:
            result = script_module.main()
        assert result == 0  # flag not activated
        assert mock_get.call_count == 0


class TestArtifactWriting:
    def test_successful_refit_writes_artifact(self, script_module, monkeypatch, tmp_path):
        """Full flow: mocked mostPopular per niche → per-niche
        artifact with correlations + video_count."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        with patch("requests.get", return_value=_make_videos_response(24)):
            stats = script_module.run(
                "fake-key",
                niches={"gaming": "20"},
                dry_run=False,
            )

        assert stats["niches_processed"] == 1
        assert stats["videos_fetched"] == 24
        assert stats["niches_failed"] == 0

        # Artifact exists with today's UTC date + niche_id
        today = datetime.now(UTC).strftime("%Y%m%d")
        art_path = tmp_path / "top-creator-priors" / f"{today}-gaming.json"
        assert art_path.is_file()

        payload = json.loads(art_path.read_text())
        assert payload["niche_id"] == "gaming"
        assert payload["video_count"] == 24
        assert "generated_at" in payload
        assert "correlations" in payload
        assert len(payload["correlations"]) > 0

    def test_dry_run_writes_nothing(self, script_module, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        with patch("requests.get", return_value=_make_videos_response(24)):
            stats = script_module.run(
                "fake-key",
                niches={"gaming": "20"},
                dry_run=True,
            )
        # Stats still incremented — dry-run doesn't skip the compute
        assert stats["niches_processed"] == 1
        # But no artifact written
        art_dir = tmp_path / "top-creator-priors"
        if art_dir.exists():
            assert list(art_dir.glob("*.json")) == []

    def test_multi_niche_writes_one_per_niche(self, script_module, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        with patch("requests.get", return_value=_make_videos_response(24)):
            stats = script_module.run(
                "fake-key",
                niches={"gaming": "20", "sports": "17", "movies": "1"},
                dry_run=False,
            )
        assert stats["niches_processed"] == 3
        today = datetime.now(UTC).strftime("%Y%m%d")
        art_dir = tmp_path / "top-creator-priors"
        assert (art_dir / f"{today}-gaming.json").is_file()
        assert (art_dir / f"{today}-sports.json").is_file()
        assert (art_dir / f"{today}-movies.json").is_file()


class TestLowVolumeAndFailureHandling:
    def test_empty_response_marks_niche_failed(self, script_module, monkeypatch, tmp_path):
        """Empty items list = API failure (not low-volume). YouTube
        returning 0 mostPopular videos never happens in practice —
        treat as failure so it surfaces in the run summary."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        with patch("requests.get", return_value=_make_empty_response()):
            stats = script_module.run(
                "fake-key",
                niches={"gaming": "20"},
                dry_run=False,
            )
        assert stats["niches_failed"] == 1
        assert stats["niches_processed"] == 0

    def test_two_videos_marked_low_volume(self, script_module, monkeypatch, tmp_path):
        """Fewer than 3 videos → skip as low-volume (Spearman
        undefined). Distinguished from failure so the summary is
        accurate."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        with patch("requests.get", return_value=_make_videos_response(2)):
            stats = script_module.run(
                "fake-key",
                niches={"gaming": "20"},
                dry_run=False,
            )
        assert stats["niches_skipped_low_volume"] == 1
        assert stats["niches_failed"] == 0

    def test_http_error_marks_niche_failed(self, script_module, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        with patch("requests.get", return_value=_make_error_response(403)):
            stats = script_module.run(
                "fake-key",
                niches={"gaming": "20"},
                dry_run=False,
            )
        assert stats["niches_failed"] == 1

    def test_exception_marks_niche_failed(self, script_module, monkeypatch, tmp_path):
        """Any transport-level exception → warning + failure count,
        never re-raised. Fail-open discipline."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        with patch("requests.get", side_effect=ConnectionError("network down")):
            stats = script_module.run(
                "fake-key",
                niches={"gaming": "20"},
                dry_run=False,
            )
        assert stats["niches_failed"] == 1


class TestNicheSet:
    def test_default_niches_are_4_with_category(self, script_module):
        """Anime is intentionally NOT in the default niche list —
        no YouTube category, keyword search costs 100 quota units."""
        default = script_module._NICHES_WITH_CATEGORY
        assert set(default.keys()) == {"gaming", "sports", "movies", "ai_creators"}
        assert "anime" not in default

    def test_niche_categories_match_youtube_docs(self, script_module):
        """The mapping must stay in sync with the trending_video_
        fetcher's YOUTUBE_CATEGORIES. Both files must be updated
        together if a category ID changes."""
        expected = {
            "gaming": "20",
            "sports": "17",
            "movies": "1",
            "ai_creators": "28",
        }
        assert script_module._NICHES_WITH_CATEGORY == expected


class TestArtifactShape:
    def test_correlation_values_are_finite(self, script_module, monkeypatch, tmp_path):
        """Every correlation must be a finite float in [-1, 1]. NaN /
        inf would break B.3's prior computation."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        with patch("requests.get", return_value=_make_videos_response(24)):
            script_module.run("fake-key", niches={"gaming": "20"}, dry_run=False)

        today = datetime.now(UTC).strftime("%Y%m%d")
        payload = json.loads((tmp_path / "top-creator-priors" / f"{today}-gaming.json").read_text())
        for feat, corr in payload["correlations"].items():
            assert isinstance(corr, (int, float)), feat
            assert -1.0 <= corr <= 1.0, f"{feat}={corr}"

    def test_artifact_is_stable_utc_dated(self, script_module, monkeypatch, tmp_path):
        """Filename is ``YYYYMMDD-<niche>.json`` in UTC. Running twice
        on the same UTC day overwrites (idempotent)."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        with patch("requests.get", return_value=_make_videos_response(24)):
            script_module.run("fake-key", niches={"gaming": "20"}, dry_run=False)
            first_stamp = (tmp_path / "top-creator-priors").iterdir().__next__().name

            # Second run — same filename (idempotent overwrite)
            script_module.run("fake-key", niches={"gaming": "20"}, dry_run=False)
            all_files = list((tmp_path / "top-creator-priors").iterdir())
        assert len(all_files) == 1
        assert all_files[0].name == first_stamp
