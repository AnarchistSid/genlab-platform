"""Pins for ``scripts/seed_pixabay_music.py``.

The script is the codified path for re-seeding the Pixabay music
library (originally ad-hoc via Playwright, then codified 2026-07-05).
Since it downloads to real filesystem paths that the AudioReplacer
reads, correctness of ``NICHE_ROOTS`` and target-path resolution is
load-bearing.

If these pins regress:

* A wrong NICHE_ROOTS mapping puts music beds in a dir the
  AudioReplacer will never look in (silent skip on prod).
* A slugger that lets ``..`` through would let a malicious manifest
  write outside the target dir (defense in depth — manifest is
  operator-controlled but still).
* Removing dry-run would silently write on the first run when the
  operator was just probing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "seed_pixabay_music.py"


@pytest.fixture(scope="module")
def script_module():
    """Load the CLI script as a module — same pattern as
    ``test_verify_intelligent_transform.py``."""
    spec = importlib.util.spec_from_file_location("seed_pixabay_music", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── NICHE_ROOTS mapping pins ─────────────────────────────────────────


def test_niche_roots_covers_4_activated_niches(script_module):
    """The 4 niches whose ``visuals.yaml.intelligent_transform.enabled``
    is true as of 2026-07-05 must all be seeded. FrameDrift/anime is
    deliberately absent — flipping it on requires updating both this
    map AND the manifest."""
    assert set(script_module.NICHE_ROOTS) == {
        "ai_creators",
        "gaming",
        "sports",
        "movies",
    }


def test_niche_roots_paths_match_visualsyaml_locations(script_module):
    """The paths must match where each niche's ``visuals.yaml`` lives —
    otherwise the AudioReplacer looks somewhere else than the seeder
    wrote."""
    assert script_module.NICHE_ROOTS == {
        "ai_creators": "BlackboxBrief",
        "gaming": "CriticalRush/niches/gaming",
        "sports": "ClutchWire",
        "movies": "SpliceReel",
    }


# ── slug + target path pins ─────────────────────────────────────────


def test_slug_strips_unsafe_characters(script_module):
    """Slug must be filesystem-safe — path-separator, dot, whitespace,
    quotes all normalized away. This is defense-in-depth against a
    malicious manifest (unlikely, but cheap)."""
    assert script_module._slug("../../etc/passwd") == "etcpasswd"
    assert script_module._slug("Track / Something") == "track__something"
    assert script_module._slug("!!!") == "track"
    assert script_module._slug("") == "track"


def test_slug_caps_length_at_40(script_module):
    """Long titles get truncated so filenames don't explode."""
    long = "supercalifragilisticexpialidocious_but_way_longer_than_expected"
    assert len(script_module._slug(long)) == 40


def test_target_path_composes_correctly(script_module, tmp_path):
    """``_target_path`` must produce
    ``<root>/<niche_root>/assets/music_beds/<mood>/<slug>_<id>.mp3``."""
    track = {"id": 12345, "title": "Cinematic Piano"}
    path = script_module._target_path(tmp_path, "ai_creators", "cinematic", track)
    assert (
        path
        == tmp_path
        / "BlackboxBrief"
        / "assets"
        / "music_beds"
        / "cinematic"
        / "cinematic_piano_12345.mp3"
    )


# ── seed() dry-run pin ──────────────────────────────────────────────


def test_seed_dry_run_touches_nothing(script_module, tmp_path, capsys):
    """Dry-run must not create files, must not call urllib. Only prints."""
    manifest = {
        "ai_creators": {
            "cinematic": [
                {
                    "id": 999,
                    "title": "Test",
                    "cdn_url": "https://cdn.pixabay.com/download/audio/x.mp3",
                }
            ]
        }
    }
    d, s, f = script_module.seed(manifest, tmp_path, dry_run=True)
    assert d == 1
    assert s == 0
    assert f == 0
    # No file written
    assert list(tmp_path.rglob("*.mp3")) == [], "Dry-run must not touch disk"
    out = capsys.readouterr().out
    assert "DRY" in out


def test_seed_skips_already_present_files(script_module, tmp_path):
    """Second run over the same manifest must skip, not re-download."""
    manifest = {
        "ai_creators": {
            "cinematic": [
                {
                    "id": 42,
                    "title": "Piano",
                    "cdn_url": "https://cdn.pixabay.com/download/audio/x.mp3",
                }
            ]
        }
    }
    target = script_module._target_path(
        tmp_path, "ai_creators", "cinematic", manifest["ai_creators"]["cinematic"][0]
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"already here")

    d, s, f = script_module.seed(manifest, tmp_path, dry_run=False)
    assert d == 0
    assert s == 1
    assert f == 0
    # Content untouched
    assert target.read_bytes() == b"already here"


def test_seed_ignores_unknown_niche_ids(script_module, tmp_path, capsys):
    """A niche in the manifest but not in NICHE_ROOTS gets warned +
    skipped, not crashed."""
    manifest = {
        "unknown_niche": {
            "cinematic": [
                {
                    "id": 1,
                    "title": "x",
                    "cdn_url": "https://cdn.pixabay.com/download/audio/x.mp3",
                }
            ]
        }
    }
    d, s, f = script_module.seed(manifest, tmp_path, dry_run=True)
    assert d == s == f == 0
    out = capsys.readouterr().out
    assert "skipping unknown niche" in out


# ── manifest presence pin ────────────────────────────────────────────


def test_manifest_ships_in_repo():
    """The committed manifest is the source-of-truth for what gets
    seeded. Removing it makes the script useless out-of-the-box."""
    manifest = SCRIPT_PATH.parent / "seed" / "pixabay_music_manifest.json"
    assert manifest.is_file(), (
        f"Manifest missing at {manifest}. Regenerate per scripts/seed/README.md."
    )


def test_manifest_has_all_4_activated_niches():
    """The manifest must carry moods for every activated niche —
    otherwise the seeder produces an empty library for that niche."""
    import json

    manifest = json.loads((SCRIPT_PATH.parent / "seed" / "pixabay_music_manifest.json").read_text())
    assert set(manifest) == {"ai_creators", "gaming", "sports", "movies"}
