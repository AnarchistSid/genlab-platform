"""Translate OData-like formula strings to PostgreSQL WHERE clauses.

The legacy formula syntax used throughout the codebase (e.g. in
BacklogClient and GraphTableProxy) uses patterns like:
  - ``{field}='value'``
  - ``AND({f1}='v1', {f2}='v2')``
  - ``OR({f1}='v1', {f2}='v2')``

This module translates those to parameterized SQL WHERE clauses with
positional parameters ($1, $2, etc.) for use with asyncpg.
"""
from __future__ import annotations

import re
from typing import Optional


def formula_to_sql(formula: Optional[str]) -> tuple[str, list[str]]:
    """Convert an OData-like formula to a SQL WHERE clause + params.

    Returns:
        A tuple of (sql_clause, params) where sql_clause uses $N positional
        parameters and params is a list of string values.

    Examples:
        >>> formula_to_sql("{status}='DRAFTED'")
        ("status = $1", ["DRAFTED"])

        >>> formula_to_sql("AND({status}='DRAFTED', {niche_id}='gaming')")
        ("status = $1 AND niche_id = $2", ["DRAFTED", "gaming"])

        >>> formula_to_sql("")
        ("", [])

        >>> formula_to_sql(None)
        ("", [])
    """
    if not formula:
        return "", []

    formula = formula.strip()
    if not formula:
        return "", []

    params: list[str] = []
    param_idx = [0]  # mutable counter for closure

    def _replace_expr(match: re.Match) -> str:
        field = match.group(1)
        value = match.group(2)
        param_idx[0] += 1
        params.append(value)
        return f"{field} = ${param_idx[0]}"

    # Replace all {field}='value' patterns with parameterized expressions
    result = re.sub(r"\{(\w+)\}='([^']*)'", _replace_expr, formula)

    # Unwrap AND(...) wrapper -> join with AND
    and_match = re.match(r"^AND\((.+)\)$", result.strip())
    if and_match:
        inner = and_match.group(1)
        # Split on commas that separate top-level AND conditions
        # Be careful with nested parens (not needed for current patterns)
        parts = _split_top_level(inner, ",")
        result = " AND ".join(p.strip() for p in parts)
    else:
        # Unwrap OR(...) wrapper -> join with OR
        or_match = re.match(r"^OR\((.+)\)$", result.strip())
        if or_match:
            inner = or_match.group(1)
            parts = _split_top_level(inner, ",")
            result = " OR ".join(p.strip() for p in parts)

    return result.strip(), params


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split text on separator, respecting parentheses nesting."""
    parts = []
    depth = 0
    current = []
    for char in text:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return parts
