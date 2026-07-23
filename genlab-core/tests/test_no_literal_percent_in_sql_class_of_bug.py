"""Codebase-wide pin test for the psycopg literal-% class-of-bug.

Motivating incident (2026-07-23, fix commit `7ad2aad1`): a comment
inside a triple-quoted SQL string in
``genlab_core/media/trending_video_fetcher.py`` contained the literal
``(87% waste)``. psycopg parses the ENTIRE query string looking for
parameter placeholders — including comment blocks — and raised
``IncompletePlaceholder`` on every call. The exception was
WARNING-swallowed by the outer handler and journal-rotated within
days. Discovery required a direct probe execution against prod DB.

This test walks every Python source file in the ``genlab_core``
package and any ``execute()`` call taking a triple-quoted string arg,
then applies the refined ``%(?![sbt%(])`` rule:

* Only fires when the SQL has at least one placeholder (``%s``/``%b``/
  ``%t`` / ``%(name)s``). psycopg3 tolerates bare ``%`` in queries with
  no placeholders, so an untainted ``LIKE 'foo%'`` is safe.
* An offending ``%`` is one NOT preceded by another ``%`` (i.e. not
  the second half of the ``%%`` escape) AND NOT followed by a valid
  placeholder character.

Real cases surveyed:
* nightly_schedule_remediate.py uses ``LIKE 'stop%%'`` correctly.
* backfill_analytics_double_prefix.py has no ``%s`` — bare ``%`` safe.
* dashboard/server/api/auto_approval.py, bandit_engagement.py: all
  ``%`` sites are valid placeholders or escapes.

See [[class-of-bug-psycopg-percent-in-sql-comment]] for the full
detection heuristic + fix pattern.
"""

from __future__ import annotations

import re
from pathlib import Path

# Match cur.execute() or conn.execute() with a triple-quoted string arg.
# Matches both """...""" and '''...''' forms across all call-shapes
# (cur/conn/cursor/c).
_EXECUTE_TRIPLE_DQ = re.compile(
    r'(?:cur|conn|cursor|c)\.execute\(\s*"""(.*?)"""',
    re.DOTALL,
)
_EXECUTE_TRIPLE_SQ = re.compile(
    r"(?:cur|conn|cursor|c)\.execute\(\s*'''(.*?)'''",
    re.DOTALL,
)

# A SQL string uses psycopg parameter binding if it contains any
# %s/%b/%t placeholder OR a named placeholder like %(name)s.
_HAS_PLACEHOLDER = re.compile(r"%[sbt]|%\(")


def _find_illegal_percent_positions(sql: str) -> list[int]:
    """Return the character positions of illegal ``%`` characters.

    Illegal := a ``%`` that is NOT the second half of ``%%`` AND NOT
    followed by ``s``/``b``/``t``/``%``/``(``.
    """
    illegal: list[int] = []
    for i, ch in enumerate(sql):
        if ch != "%":
            continue
        # Skip if this is the SECOND-% of a %% escape.
        if i > 0 and sql[i - 1] == "%":
            continue
        # Skip if next char is s/b/t/%/(
        next_ch = sql[i + 1] if i + 1 < len(sql) else ""
        if next_ch in "sbt%(":
            continue
        illegal.append(i)
    return illegal


def _scan_source_tree(root: Path) -> list[tuple[Path, int, str]]:
    """Return offenders: (path, line_number, sql_preview)."""
    offenders: list[tuple[Path, int, str]] = []
    for py in root.rglob("*.py"):
        # Skip venvs, caches, third-party.
        if any(
            part in py.parts
            for part in (".venv", "__pycache__", "node_modules", "site-packages")
        ):
            continue
        try:
            src = py.read_text()
        except OSError:
            continue

        for regex in (_EXECUTE_TRIPLE_DQ, _EXECUTE_TRIPLE_SQ):
            for match in regex.finditer(src):
                sql = match.group(1)
                # Only enforce the strict mode when the query uses
                # parameter binding — psycopg3 tolerates ``%`` in
                # placeholder-free queries.
                if not _HAS_PLACEHOLDER.search(sql):
                    continue
                positions = _find_illegal_percent_positions(sql)
                if positions:
                    line_no = src[: match.start()].count("\n") + 1
                    preview = sql[: max(200, positions[0] + 40)].replace("\n", " ")
                    offenders.append((py, line_no, preview))
    return offenders


def test_no_literal_percent_in_genlab_core_sql() -> None:
    """The class-of-bug pin. Any regression that adds a literal ``%``
    to a psycopg SQL string with parameters will fail this test with
    a specific file:line + fix suggestion.

    2026-07-23 baseline: after commit ``7ad2aad1`` cleaned up the
    content_pool SQL, this walk finds zero offenders.
    """
    src_root = Path(__file__).resolve().parents[1] / "src" / "genlab_core"
    assert src_root.exists(), (
        f"genlab_core src root not found at {src_root} — this test "
        "assumes the standard src-layout package structure."
    )

    offenders = _scan_source_tree(src_root)
    if offenders:
        lines = [
            f"  {p.relative_to(src_root.parent.parent.parent)}:{ln}"
            for p, ln, _ in offenders[:10]
        ]
        first = offenders[0]
        raise AssertionError(
            "psycopg literal-% class-of-bug detected. See "
            "[[class-of-bug-psycopg-percent-in-sql-comment]] for the fix "
            "pattern.\n\n"
            f"{len(offenders)} offender(s) — first at "
            f"{first[0].relative_to(src_root.parent.parent.parent)}:{first[1]}\n"
            f"SQL preview: {first[2][:200]!r}\n\n"
            f"All offenders:\n" + "\n".join(lines)
        )


def test_illegal_percent_positions_catches_87pct_waste_case():
    """Inverse-verification pin: the detection regex must actually
    catch the exact prod bug it was written for. If this test starts
    passing with zero positions returned, the regex has silently
    degraded — the main test above becomes a false-negative.
    """
    # Exact shape of the offending SQL comment from
    # trending_video_fetcher.py before commit 7ad2aad1.
    sql_with_bug = """
        SELECT * FROM content_pool
        WHERE %s = ANY(routed_niches)
          AND status = 'available'
        -- Prod audit found 695 of 795 pool items rot
        -- per week (87% waste). Gaming/movies/sports
        LIMIT 60
    """
    positions = _find_illegal_percent_positions(sql_with_bug)
    assert positions, (
        "The '87% waste' bug SQL must produce ≥1 illegal-% position — "
        "if this fails, the regex logic has silently degraded."
    )
    # And confirm HAS_PLACEHOLDER matches (so the main test wouldn't
    # skip this file under the placeholder-free-tolerance rule).
    assert _HAS_PLACEHOLDER.search(sql_with_bug), (
        "The sample SQL has a %s placeholder — the placeholder detector "
        "must match it, otherwise the main test skips real offenders."
    )


def test_double_percent_escape_is_not_flagged():
    """Regression guard: a properly-escaped %% (SQL LIKE wildcard when
    the query has %s bindings) must NOT be flagged as illegal.
    """
    # Real shape from nightly_schedule_remediate.py.
    legit_sql = """
        SELECT id FROM blueprints
        WHERE niche_id = %s
          AND hook NOT ILIKE 'I need to stop%%'
    """
    positions = _find_illegal_percent_positions(legit_sql)
    assert positions == [], (
        f"%% escape must not be flagged as illegal, got positions={positions}. "
        "Regression of the refined 'not preceded by %' skip rule."
    )


def test_placeholder_free_query_with_percent_is_skipped():
    """Regression guard: a query WITHOUT any placeholder that contains
    a bare % (LIKE literal) must NOT be flagged. psycopg3 is smart
    enough to tolerate this shape.
    """
    # Real shape from backfill_analytics_double_prefix.py.
    legit_sql = """
        SELECT id, post_id
        FROM analytics
        WHERE post_id LIKE 'facebook:facebook:%'
    """
    assert not _HAS_PLACEHOLDER.search(legit_sql), (
        "Placeholder detector should return False for a placeholder-free "
        "query — regression of the has-placeholder gate."
    )
