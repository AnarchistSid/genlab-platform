"""Tests for backlog formula → OData $filter translator.

Validates all 7 formula patterns used across the codebase.
"""

from genlab_core.http.graph_proxy import formula_to_odata as _formula_to_odata


class TestSimpleEquality:
    """Pattern 1: {field}='value' → fields/field eq 'value'"""

    def test_simple_string(self):
        assert _formula_to_odata("{story_id}='abc123'") == "fields/story_id eq 'abc123'"

    def test_status_field(self):
        assert _formula_to_odata("{status}='DRAFTED'") == "fields/status eq 'DRAFTED'"

    def test_single_quotes_in_value(self):
        result = _formula_to_odata("{title}='it\\'s a test'")
        assert "fields/title eq" in result


class TestAND:
    """Pattern 2: AND({f1}='v1', {f2}='v2') → f1 eq 'v1' and f2 eq 'v2'"""

    def test_two_conditions(self):
        result = _formula_to_odata("AND({status}='DRAFTED', {format}='carousel')")
        assert "fields/status eq 'DRAFTED'" in result
        assert "fields/format eq 'carousel'" in result
        assert " and " in result

    def test_three_conditions(self):
        result = _formula_to_odata(
            "AND({status}='DRAFTED', {format}='carousel', {platform}='instagram')"
        )
        assert result.count(" and ") == 2


class TestOR:
    """Pattern 3: OR({f1}='v1', {f2}='v2') → (f1 eq 'v1' or f2 eq 'v2')"""

    def test_two_conditions(self):
        result = _formula_to_odata("OR({status}='DRAFTED', {status}='VISUAL_READY')")
        assert "fields/status eq 'DRAFTED'" in result
        assert "fields/status eq 'VISUAL_READY'" in result
        assert " or " in result
        assert result.startswith("(")
        assert result.endswith(")")


class TestBoolean:
    """Pattern 4: {enabled}=TRUE() → fields/enabled eq 1 (SharePoint uses 1/0)"""

    def test_true(self):
        assert _formula_to_odata("{enabled}=TRUE()") == "fields/enabled eq 1"

    def test_false(self):
        assert _formula_to_odata("{enabled}=FALSE()") == "fields/enabled eq 0"


class TestBlank:
    """Pattern 8: {field}=BLANK() → fields/field eq null"""

    def test_blank(self):
        assert _formula_to_odata("{action_taken}=BLANK()") == "fields/action_taken eq null"

    def test_blank_in_or(self):
        result = _formula_to_odata("OR({action_taken}='', {action_taken}=BLANK())")
        assert "fields/action_taken eq ''" in result
        assert "fields/action_taken eq null" in result
        assert " or " in result


class TestNestedLogic:
    """Pattern 5: AND(OR(...), {x}='y') → nested"""

    def test_and_with_or(self):
        result = _formula_to_odata(
            "AND(OR({status}='DRAFTED', {status}='VISUAL_READY'), {format}='carousel')"
        )
        assert "fields/format eq 'carousel'" in result
        assert " and " in result
        assert " or " in result


class TestComparison:
    """Pattern 6: {date}>='2026-02-27' → fields/date ge '2026-02-27'"""

    def test_greater_equal(self):
        result = _formula_to_odata("{date}>='2026-02-27'")
        assert result == "fields/date ge '2026-02-27'"

    def test_less_than(self):
        result = _formula_to_odata("{priority}<'0.5'")
        # Numeric values don't need quotes in OData
        assert result == "fields/priority lt 0.5"


class TestFIND:
    """Pattern 7: FIND('x', ARRAYJOIN({link})) → contains filter"""

    def test_find_arrayjoin(self):
        result = _formula_to_odata("FIND('rec123', ARRAYJOIN({story}))")
        assert "contains" in result
        assert "rec123" in result

    def test_find_arrayjoin_uses_esc(self):
        """FIND with ARRAYJOIN passes value through _esc() for OData safety."""
        result = _formula_to_odata("FIND('rec123', ARRAYJOIN({story}))")
        assert result == "contains(fields/story_text, 'rec123')"

    def test_find_bare_uses_esc(self):
        """Bare FIND (without ARRAYJOIN) also passes value through _esc()."""
        result = _formula_to_odata("FIND('rec456', {title})")
        assert result == "contains(fields/title, 'rec456')"

    def test_find_arrayjoin_calls_esc(self):
        """Verify _esc() is invoked in the FIND+ARRAYJOIN code path."""
        from unittest.mock import patch
        with patch("genlab_core.http.graph_proxy._esc", side_effect=lambda v: v) as mock_esc:
            _formula_to_odata("FIND('inject_test', ARRAYJOIN({story}))")
            mock_esc.assert_called_once_with("inject_test")

    def test_find_bare_calls_esc(self):
        """Verify _esc() is invoked in the bare FIND code path."""
        from unittest.mock import patch
        with patch("genlab_core.http.graph_proxy._esc", side_effect=lambda v: v) as mock_esc:
            _formula_to_odata("FIND('inject_test', {title})")
            mock_esc.assert_called_once_with("inject_test")


class TestEdgeCases:
    def test_none(self):
        assert _formula_to_odata(None) is None

    def test_empty(self):
        assert _formula_to_odata("") is None

    def test_whitespace(self):
        assert _formula_to_odata("   ") is None
