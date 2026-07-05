"""Real-DB EXPLAIN sweep for learning-loop SQL — catches broken column refs at test time.

The 2026-07-02 late_reward.py SQL bug (see [[late-reward-sql-bug-2026-07-02]])
was silent for 7 weeks in production because:

  1. The runner's outer ``except Exception`` returned None on parse errors
  2. ``logger.warning`` went to journal but nowhere an operator would see
  3. Every test used ``unittest.mock.MagicMock`` on ``conn.execute`` so the
     SQL string was never verified against a real schema

This test closes gap (3) for the load-bearing learning-loop modules: it
extracts every raw SQL string from a curated module list, converts psycopg's
``%s`` and ``%(name)s`` placeholders to PostgreSQL's ``$N`` positional
syntax, and issues ``PREPARE tmp AS <sql>`` against a real DB. PREPARE
performs full parse + column resolution but never executes the query —
so this is cheap and catches ``UndefinedColumn`` / ``UndefinedTable`` /
``SyntaxError`` at CI time rather than 7 weeks later in prod.

Skipped when ``POSTGRES_PASSWORD`` unset — the ``test-storage`` CI job on
Hetzner sets it via the ``genlab_app`` role.

## Adding new modules

Append a ``(module_path, expected_min_sql_count)`` tuple to
``_MODULES_TO_SCAN``. The expected-count guards against future refactors
that silently drop a SQL site (the regex extractor is defensive but not
psychic — if it stops finding your queries, the count assertion fires).
"""

from __future__ import annotations

import importlib
import inspect
import os
import re

import pytest

# The DB-touching classes are gated on POSTGRES_PASSWORD; the pure-Python
# unit tests (extractor + placeholder converter) always run so their
# behaviour stays pinned even in local dev without a Postgres.
_needs_pg = pytest.mark.skipif(
    "not os.environ.get('POSTGRES_PASSWORD')",
    reason="POSTGRES_PASSWORD not set — skip real-DB integration",
)


# ─────────────────────────────────────────────────────────────────────
# Modules to scan. Add new entries as new learning-loop SQL sites land.
# The min-count is a floor, not a target — new SQL sites in a module
# raise the count but never lower it. This guards against a refactor
# silently dropping a query the extractor was covering.
# ─────────────────────────────────────────────────────────────────────
_MODULES_TO_SCAN: list[tuple[str, int]] = [
    # (module_path, min expected SQL string count)
    ("genlab_core.learning.late_reward", 1),
    ("genlab_core.learning.ips_replay", 1),
    ("genlab_core.intelligence.state_collector", 5),
    ("genlab_core.intelligence.persister", 2),
    ("genlab_core.scheduling.strategist_actions", 2),
]


# ─────────────────────────────────────────────────────────────────────
# SQL extraction
# ─────────────────────────────────────────────────────────────────────

# Match a triple-quoted string that starts (after any whitespace) with
# an UPPERCASE SQL keyword. Case-sensitive by design — the repo's real
# SQL uses uppercase keywords (verified across all currently-covered
# modules), whereas docstrings like ``"""Update the (niche, source)
# arm..."""`` use lowercase "Update". Case-insensitive matching pulled
# in that docstring as a false-positive on ``source_performance.py``;
# the case-sensitive form correctly ignores it.
#
# Deliberately misses f-strings and dynamic concatenation — those need
# to be extracted differently or tested via their own runtime paths.
# See ``_MODULES_TO_SCAN`` note.
_SQL_STRING_RE = re.compile(
    r'"""\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b(.+?)"""',
    re.DOTALL,  # case-sensitive; see comment above
)


def _extract_sql_strings(module_path: str) -> list[str]:
    """Return every triple-quoted SQL literal found in the module source.

    We match on the triple-double-quote token (which is the repo
    convention for multi-line SQL). Single-line SQL string literals are
    rare in this codebase and covered separately by their runtime tests.
    """
    module = importlib.import_module(module_path)
    source = inspect.getsource(module)
    matches = _SQL_STRING_RE.findall(source)
    # Reassemble the full SQL — regex captured (keyword, tail); prepend
    # the keyword back so PREPARE sees valid syntax.
    return [f"{kw}{tail}".strip() for kw, tail in matches]


# ─────────────────────────────────────────────────────────────────────
# Placeholder conversion — psycopg %s / %(name)s → PostgreSQL $N
# ─────────────────────────────────────────────────────────────────────

_PSYCOPG_NAMED = re.compile(r"%\(([a-zA-Z_][a-zA-Z_0-9]*)\)s")
_PSYCOPG_POSITIONAL = re.compile(r"(?<!%)%s")  # avoid matching %%s literal


def _convert_to_postgres_placeholders(sql: str) -> str:
    """Convert psycopg ``%s`` / ``%(name)s`` placeholders to PostgreSQL
    ``$N`` positional style so PREPARE can parse it.

    Named params come first (unique per name); positional params fill
    from the next available number. This is a light-touch conversion
    that's correct for PREPARE (which only needs the params to have
    valid syntactic form — the actual types are inferred from usage).

    Also unescapes ``%%`` → ``%`` since psycopg's literal-percent escape
    isn't valid PostgreSQL syntax.
    """
    # Track distinct named params
    named_seen: dict[str, int] = {}
    counter = [0]  # mutable holder for nested-fn access

    def _named_replacer(match: re.Match) -> str:
        name = match.group(1)
        if name not in named_seen:
            counter[0] += 1
            named_seen[name] = counter[0]
        return f"${named_seen[name]}"

    converted = _PSYCOPG_NAMED.sub(_named_replacer, sql)

    def _positional_replacer(_match: re.Match) -> str:
        counter[0] += 1
        return f"${counter[0]}"

    converted = _PSYCOPG_POSITIONAL.sub(_positional_replacer, converted)
    # Unescape literal-percent
    converted = converted.replace("%%", "%")
    return converted


# ─────────────────────────────────────────────────────────────────────
# The real-DB EXPLAIN via PREPARE
# ─────────────────────────────────────────────────────────────────────


def _pg_conn():
    """Direct psycopg connection for the SQL-parse check.

    Uses the same env-var defaults as the pg_backend fixture in
    ``conftest.py``. Deliberately NOT using pg_backend because we
    don't need connection pooling or row-cleanup — just a bare
    transactional connection that can PREPARE + ROLLBACK per test.
    """
    import psycopg

    return psycopg.connect(
        host=os.environ.get("GENLAB_TEST_PG_HOST", "localhost"),
        port=int(os.environ.get("GENLAB_TEST_PG_PORT", "5432")),
        dbname=os.environ.get("GENLAB_TEST_PG_DB", "genlab"),
        user=os.environ.get("GENLAB_TEST_PG_USER", "genlab"),
        password=(
            os.environ.get("GENLAB_TEST_PG_PASSWORD") or os.environ.get("POSTGRES_PASSWORD", "")
        ),
        connect_timeout=10,
    )


def _prepare_and_rollback(sql: str, stmt_name: str) -> tuple[bool, str]:
    """PREPARE the SQL, immediately DEALLOCATE + ROLLBACK.

    Returns ``(ok, error_message)``. ``ok=True`` iff PostgreSQL parsed
    the SQL AND resolved every column reference successfully. Any
    UndefinedColumn / UndefinedTable / SyntaxError → ok=False.

    PREPARE is transactional-safe here: we open one connection per
    test, PREPARE, DEALLOCATE the temp name, and roll back to leave
    zero DB state behind. Real bandit / feedback rows are never
    touched.
    """
    try:
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                # RLS bypass so we can PREPARE any niche-filtered query
                cur.execute("SET LOCAL app.niche_id = 'all'")
                # Use a distinct name per prepare so parallel test
                # execution doesn't collide.
                cur.execute(f"PREPARE {stmt_name} AS {sql}")
                cur.execute(f"DEALLOCATE {stmt_name}")
            conn.rollback()
        return True, ""
    except Exception as exc:  # noqa: BLE001 — surfacing everything to the test
        # Truncate long postgres error messages so pytest output stays scannable
        return False, str(exc)[:400]


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────


class TestExtractorCaseSensitivity:
    """The extractor MUST be case-sensitive on the SQL keyword.

    Docstrings starting with lowercase verbs ("Update the ...",
    "Select which arm...") would otherwise get picked up as SQL and
    fail EXPLAIN. Case-insensitive matching caught 1 false-positive on
    ``source_performance.py`` during initial development; the
    case-sensitive form correctly ignores it.

    Real SQL in this codebase uses uppercase keywords everywhere —
    verified across all currently-covered modules.
    """

    def test_uppercase_select_extracted(self):
        # Simulate a module source with real SQL
        src = 'x = conn.execute("""SELECT * FROM t""")'
        matches = _SQL_STRING_RE.findall(src)
        assert len(matches) == 1
        assert matches[0][0] == "SELECT"

    def test_lowercase_docstring_verb_not_extracted(self):
        """The bug we're guarding against — lowercase 'Update' in a
        docstring must NOT be treated as SQL."""
        src = '"""Update the (niche, source) arm with a Bayesian update"""'
        assert _SQL_STRING_RE.findall(src) == []

    def test_mixed_case_extraction(self):
        """Belt-and-suspenders — 'Insert' as a docstring word doesn't
        trip the extractor either."""
        src = '"""Insert into the flow at the right moment"""'
        assert _SQL_STRING_RE.findall(src) == []


class TestSqlExtractionSanity:
    """Meta-tests for the extractor itself. If _extract_sql_strings
    stops finding queries in modules we know contain queries, this
    test suite's coverage silently drops to zero.
    """

    @pytest.mark.parametrize("module_path,min_count", _MODULES_TO_SCAN)
    def test_extractor_finds_expected_sql_count(self, module_path: str, min_count: int):
        sqls = _extract_sql_strings(module_path)
        assert len(sqls) >= min_count, (
            f"Expected at least {min_count} SQL string(s) in {module_path}, "
            f"got {len(sqls)}. Either the module lost queries in a refactor "
            f"or the extractor regex needs to be widened."
        )


class TestPlaceholderConversion:
    """Unit tests for the psycopg → PostgreSQL placeholder converter.
    These run WITHOUT the DB — they're pure text transformation."""

    def test_positional_single(self):
        assert (
            _convert_to_postgres_placeholders("SELECT * FROM t WHERE id = %s")
            == "SELECT * FROM t WHERE id = $1"
        )

    def test_positional_multiple(self):
        assert (
            _convert_to_postgres_placeholders("SELECT * FROM t WHERE a = %s AND b = %s")
            == "SELECT * FROM t WHERE a = $1 AND b = $2"
        )

    def test_named_deduped(self):
        """Repeated names get the SAME $N — matches psycopg's %(name)s
        semantics where each unique name binds one value."""
        out = _convert_to_postgres_placeholders("SELECT * FROM t WHERE a = %(x)s OR b = %(x)s")
        # Both %(x)s → $1 (same value bound twice)
        assert out == "SELECT * FROM t WHERE a = $1 OR b = $1"

    def test_named_multiple_distinct(self):
        out = _convert_to_postgres_placeholders("SELECT * FROM t WHERE a = %(x)s AND b = %(y)s")
        assert out == "SELECT * FROM t WHERE a = $1 AND b = $2"

    def test_literal_percent_percent_unescaped(self):
        """psycopg's ``%%`` for literal ``%`` (as in LIKE patterns)
        must become bare ``%`` for PostgreSQL."""
        out = _convert_to_postgres_placeholders("SELECT * FROM t WHERE post_id LIKE '%%' || %s")
        assert out == "SELECT * FROM t WHERE post_id LIKE '%' || $1"


@_needs_pg
class TestRealDbSqlIntegrity:
    """The main event — extract every SQL string from every scanned
    module, PREPARE it against real Postgres, assert all pass.

    A parameterized test per (module, SQL-index) pair so pytest's
    failure output pinpoints the exact broken query."""

    # Build the parameter list at collection time so pytest can show
    # each SQL separately in `pytest -v` output.
    _params: list[tuple[str, int, str]] = []
    for _module_path, _ in _MODULES_TO_SCAN:
        for _idx, _sql in enumerate(_extract_sql_strings(_module_path)):
            _params.append((_module_path, _idx, _sql))

    @pytest.mark.parametrize("module_path,sql_idx,sql", _params, ids=lambda p: str(p)[:60])
    def test_sql_parses_and_resolves_columns(self, module_path: str, sql_idx: int, sql: str):
        converted = _convert_to_postgres_placeholders(sql)
        # Statement name must be a valid identifier, short + unique per test.
        # Replace dots in module path with underscores.
        stmt = f"tmp_sql_{module_path.replace('.', '_')}_{sql_idx}"[:60]
        ok, err = _prepare_and_rollback(converted, stmt)
        assert ok, (
            f"\n\n{module_path} SQL #{sql_idx} failed PREPARE:\n"
            f"  Error: {err}\n"
            f"  Original SQL (first 200 chars):\n    {sql[:200]!r}\n"
            f"  Converted SQL (first 200 chars):\n    {converted[:200]!r}\n"
        )
