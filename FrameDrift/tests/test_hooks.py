"""Tests for FrameDrift hook strategy — trend-cycle-aware routing."""

import pytest
from fd_strategies.hooks import AnimeHookStrategy


@pytest.fixture
def strategy():
    return AnimeHookStrategy()


class TestHookTemplateRendering:
    def test_all_formulas_are_strings(self, strategy):
        strategy._ensure_config()
        hooks_config = strategy._templates.get("hooks", {})
        for formula in hooks_config.get("formulas", []):
            assert isinstance(formula, str)

    def test_placeholder_substitution(self, strategy):
        result = strategy._substitute_placeholders(
            "The {trend_name} trend is taking over",
            {"trend_name": "isekai revival"},
        )
        assert "isekai revival" in result

    def test_title_placeholder_renders(self, strategy):
        result = strategy._substitute_placeholders(
            "{title} just dropped",
            {"is_new_release": True, "title": "Jujutsu Kaisen Season 3"},
        )
        assert "{title}" not in result

    def test_no_forbidden_styles_in_formulas(self, strategy):
        strategy._ensure_config()
        hooks = strategy._templates.get("hooks", {})
        forbidden = hooks.get("forbidden_styles", [])
        for cat_name, cat_data in hooks.get("story_categories", {}).items():
            for formula in cat_data.get("formulas", []):
                for f in forbidden:
                    assert not formula.upper().startswith(f.upper()), (
                        f"Formula '{formula}' in '{cat_name}' starts with forbidden '{f}'"
                    )
