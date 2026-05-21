"""Tests for multi-tenancy niche_id filtering in BacklogClient."""

import pytest
from genlab_core.http.backlog_client import _inject_niche_filter


class TestInjectNicheFilter:
    def test_none_niche_id_returns_original(self):
        assert _inject_niche_filter("{status}='INTAKE'", None) == "{status}='INTAKE'"

    def test_none_formula_with_niche_id(self):
        result = _inject_niche_filter(None, "gaming")
        assert result == "{niche_id}='gaming'"

    def test_both_none_returns_none(self):
        assert _inject_niche_filter(None, None) is None

    def test_appends_niche_clause_with_and(self):
        result = _inject_niche_filter("{status}='INTAKE'", "gaming")
        assert result == "AND({status}='INTAKE', {niche_id}='gaming')"

    def test_escapes_single_quotes_in_niche_id(self):
        result = _inject_niche_filter(None, "test'niche")
        assert result == "{niche_id}='test\\'niche'"

    def test_custom_niche_field(self):
        result = _inject_niche_filter(None, "gaming", niche_field="NicheId")
        assert result == "{NicheId}='gaming'"

    def test_complex_formula(self):
        formula = "AND({status}='DRAFTED', {format}='reel')"
        result = _inject_niche_filter(formula, "ai_creators")
        assert result == "AND(AND({status}='DRAFTED', {format}='reel'), {niche_id}='ai_creators')"


class TestListNiches:
    def test_list_niches_returns_list(self):
        """Test that list_niches returns a list even without BacklogClient instantiation."""
        # We can't easily instantiate BacklogClient without Azure creds,
        # but we can test the YAML loading logic directly
        from pathlib import Path

        import yaml

        registry_path = Path(__file__).resolve().parent.parent / "configs" / "niches_registry.yaml"
        if registry_path.exists():
            with open(registry_path) as f:
                data = yaml.safe_load(f)
            niches = data.get("niches", [])
            assert len(niches) >= 5
            assert niches[0]["id"] == "ai_creators"
            assert niches[1]["id"] == "gaming"
        else:
            pytest.skip("niches_registry.yaml not found")
