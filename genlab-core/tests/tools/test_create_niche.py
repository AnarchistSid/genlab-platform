"""Tests for the niche scaffold tool."""
import tempfile
from pathlib import Path

import pytest
from genlab_core.tools.create_niche import scaffold_niche


class TestCreateNiche:
    def test_creates_directory_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "TestNiche"
            scaffold_niche(
                niche_id="fitness",
                brand_name="FitPulse",
                accent_color="#00FF88",
                output_dir=out,
            )
            assert (out / "config" / "niche.yaml").exists()
            assert (out / "config" / "sources.yaml").exists()
            assert (out / "config" / "visuals.yaml").exists()
            assert (out / "config" / "publishing.yaml").exists()
            assert (out / "config" / "schedule.yaml").exists()
            assert (out / "config" / "scoring_weights.yaml").exists()
            assert (out / "strategies").is_dir()

    def test_replaces_placeholders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "FitPulse"
            scaffold_niche(
                niche_id="fitness",
                brand_name="FitPulse",
                accent_color="#00FF88",
                output_dir=out,
            )
            niche_yaml = (out / "config" / "niche.yaml").read_text()
            assert "fitness" in niche_yaml
            assert "FitPulse" in niche_yaml
            assert "{NICHE_ID}" not in niche_yaml
            assert "{BRAND_NAME}" not in niche_yaml
            assert "{ACCENT_COLOR}" not in niche_yaml

    def test_strategy_references_renamed_in_niche_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "FitPulse"
            scaffold_niche(
                niche_id="fitness",
                brand_name="FitPulse",
                accent_color="#00FF88",
                output_dir=out,
            )
            niche_yaml = (out / "config" / "niche.yaml").read_text()
            # fd_strategies references should be replaced with fitness_strategies
            assert "fd_strategies" not in niche_yaml
            assert "fitness_strategies" in niche_yaml
            # Anime class names should be replaced with FitPulse
            assert "AnimeContentResearchStrategy" not in niche_yaml
            assert "FitPulseContentResearchStrategy" in niche_yaml

    def test_raises_on_existing_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "Existing"
            out.mkdir()
            with pytest.raises(FileExistsError):
                scaffold_niche(
                    niche_id="fitness",
                    brand_name="FitPulse",
                    accent_color="#00FF88",
                    output_dir=out,
                )

    def test_creates_assets_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "TestNiche"
            scaffold_niche(
                niche_id="fitness",
                brand_name="FitPulse",
                accent_color="#00FF88",
                output_dir=out,
            )
            assert (out / "assets").is_dir()

    def test_run_pipeline_has_correct_niche_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "FitPulse"
            scaffold_niche(
                niche_id="fitness",
                brand_name="FitPulse",
                accent_color="#00FF88",
                output_dir=out,
            )
            run_pipeline = (out / "run_pipeline.py").read_text()
            assert 'NICHE_ID = "fitness"' in run_pipeline

    def test_accent_color_in_visuals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "FitPulse"
            scaffold_niche(
                niche_id="fitness",
                brand_name="FitPulse",
                accent_color="#00FF88",
                output_dir=out,
            )
            visuals = (out / "config" / "visuals.yaml").read_text()
            assert "#00FF88" in visuals
