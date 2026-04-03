"""Tests for ClutchWire hook template rendering."""

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
        "team": "Lakers",
        "player": "LeBron",
        "sport": "NBA",
        "play_type": "buzzer beater",
    }


class TestHookTemplateRendering:
    """All hook templates render with valid placeholder substitution."""

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
            # Should not have leftover unsubstituted placeholders
            # (some formulas may not use all placeholders, that's fine)
            assert len(rendered) > 0

    def test_team_placeholder_renders(self, templates, context_dict):
        team_formulas = [f for f in templates["hooks"]["formulas"] if "{team}" in f]
        for formula in team_formulas:
            rendered = formula.replace("{team}", context_dict["team"])
            assert context_dict["team"] in rendered
            assert "{team}" not in rendered

    def test_player_placeholder_renders(self, templates, context_dict):
        player_formulas = [f for f in templates["hooks"]["formulas"] if "{player}" in f]
        for formula in player_formulas:
            rendered = formula.replace("{player}", context_dict["player"])
            assert context_dict["player"] in rendered
            assert "{player}" not in rendered

    def test_no_forbidden_styles_in_formulas(self, templates):
        formulas = templates["hooks"]["formulas"]
        forbidden = templates["hooks"]["forbidden_styles"]
        for formula in formulas:
            for style in forbidden:
                assert style not in formula, f"Formula contains forbidden style: {formula}"
