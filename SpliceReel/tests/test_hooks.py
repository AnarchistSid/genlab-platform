"""Tests for SpliceReel hook template rendering."""

from pathlib import Path

import pytest
import yaml

NICHE_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = NICHE_ROOT / "config"


@pytest.fixture
def templates():
    with open(CONFIG_DIR / "templates.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def context_dict():
    return {
        "film": "Oppenheimer",
        "director": "Christopher Nolan",
        "scene": "the Trinity test",
    }


class TestHookTemplateRendering:
    def test_all_formulas_are_strings(self, templates):
        formulas = templates["hooks"]["formulas"]
        assert all(isinstance(f, str) for f in formulas)
        assert len(formulas) >= 3

    def test_placeholder_substitution(self, templates, context_dict):
        formulas = templates["hooks"]["formulas"]
        for formula in formulas:
            rendered = formula
            for key, value in context_dict.items():
                rendered = rendered.replace(f"{{{key}}}", value)
            assert len(rendered) > 0

    def test_film_placeholder_renders(self, templates, context_dict):
        film_formulas = [f for f in templates["hooks"]["formulas"] if "{film}" in f]
        for formula in film_formulas:
            rendered = formula.replace("{film}", context_dict["film"])
            assert context_dict["film"] in rendered
            assert "{film}" not in rendered

    def test_director_placeholder_renders(self, templates, context_dict):
        dir_formulas = [f for f in templates["hooks"]["formulas"] if "{director}" in f]
        for formula in dir_formulas:
            rendered = formula.replace("{director}", context_dict["director"])
            assert context_dict["director"] in rendered

    def test_no_forbidden_styles_in_formulas(self, templates):
        formulas = templates["hooks"]["formulas"]
        forbidden = templates["hooks"]["forbidden_styles"]
        for formula in formulas:
            for style in forbidden:
                assert style not in formula
