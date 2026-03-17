"""Tests for OData formula -> SQL WHERE translation."""
from __future__ import annotations

import pytest

from genlab_core.storage.formula_sql import formula_to_sql


class TestFormulaToSQL:
    """Test the formula_to_sql translator."""

    def test_simple_equality(self):
        sql, params = formula_to_sql("{status}='DRAFTED'")
        assert "status" in sql
        assert params == ["DRAFTED"]
        assert "$1" in sql

    def test_and_condition(self):
        sql, params = formula_to_sql(
            "AND({status}='DRAFTED', {niche_id}='gaming')"
        )
        assert "AND" in sql
        assert len(params) == 2
        assert "DRAFTED" in params
        assert "gaming" in params

    def test_empty_formula(self):
        sql, params = formula_to_sql("")
        assert sql == ""
        assert params == []

    def test_none_formula(self):
        sql, params = formula_to_sql(None)
        assert sql == ""
        assert params == []

    def test_single_field_equality_format(self):
        """Output should be 'field = $1' with positional params."""
        sql, params = formula_to_sql("{candidate_id}='abc_123'")
        assert sql == "candidate_id = $1"
        assert params == ["abc_123"]

    def test_and_two_conditions_format(self):
        """AND() should produce 'field1 = $1 AND field2 = $2'."""
        sql, params = formula_to_sql(
            "AND({status}='PUBLISHED', {niche_id}='sports')"
        )
        assert sql == "status = $1 AND niche_id = $2"
        assert params == ["PUBLISHED", "sports"]

    def test_and_three_conditions(self):
        """Three conditions inside AND()."""
        sql, params = formula_to_sql(
            "AND({status}='DRAFTED', {niche_id}='gaming', {hook}='test')"
        )
        assert sql == "status = $1 AND niche_id = $2 AND hook = $3"
        assert params == ["DRAFTED", "gaming", "test"]

    def test_or_condition(self):
        """OR() should produce 'field1 = $1 OR field2 = $2'."""
        sql, params = formula_to_sql(
            "OR({status}='DRAFTED', {status}='PUBLISHED')"
        )
        assert "OR" in sql
        assert len(params) == 2

    def test_whitespace_handling(self):
        """Leading/trailing whitespace should be stripped."""
        sql, params = formula_to_sql("  {status}='DRAFTED'  ")
        assert sql == "status = $1"
        assert params == ["DRAFTED"]

    def test_value_with_spaces(self):
        """Values containing spaces should be preserved."""
        sql, params = formula_to_sql("{title}='My Cool Title'")
        assert params == ["My Cool Title"]

    def test_empty_value(self):
        """Empty string value should be handled."""
        sql, params = formula_to_sql("{status}=''")
        assert sql == "status = $1"
        assert params == [""]

    def test_param_numbering_increments(self):
        """Each parameter should get a unique positional number."""
        sql, params = formula_to_sql(
            "AND({a}='1', {b}='2', {c}='3')"
        )
        assert "$1" in sql
        assert "$2" in sql
        assert "$3" in sql
        assert len(params) == 3
