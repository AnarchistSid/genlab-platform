"""2026-08-11 DDL audit: pin no-DDL-in-runtime-code invariant.

Discovered 2026-08-11 during honest audit of "have all bugs been
fixed" question. Found 4 sites with DDL (CREATE TABLE / CREATE INDEX /
ALTER TABLE) in runtime code paths:

1. late_reward.py:_persist_delta_row       — Bug 1b (fixed 4649aa3c)
2. health_monitor.py:write_alerts_to_db    — Bug 5  (fixed 21f3698b)
3. run_outbound_reply_engine.py:_ensure_history_table — this audit
4. collect_audience_metrics.py:main        — this audit

Each hard-crashed or silent-failed under the Audit A credential
rotation (2026-07-30) that removed CREATE privilege from
genlab_app role. `psycopg.errors.InsufficientPrivilege: permission
denied for schema public` was the error signature every time.

## The rule this file pins

DDL (CREATE TABLE, CREATE INDEX, ALTER TABLE, DROP TABLE) belongs
in alembic migrations run by the `genlab` superuser role — NOT in
runtime code that connects as genlab_app.

## Why source-inspection instead of runtime test

DDL that fails silently doesn't produce a test failure — the code
"runs" and returns. Only a source-inspection test can pin the
architectural invariant "no DDL keywords in runtime files."

Fresh-install tables/constraints should be created via alembic
migration, not lazy CREATE-IF-NOT-EXISTS.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# DDL keywords that should NEVER appear in a runtime code path.
# CREATE INDEX + ALTER TABLE included because they hit the same
# genlab_app permission wall.
_DDL_PATTERNS = (
    "CREATE TABLE IF NOT EXISTS",
    "CREATE INDEX IF NOT EXISTS",
    "CREATE UNIQUE INDEX IF NOT EXISTS",
    "ALTER TABLE",
    "DROP TABLE",
)


def _files_with_ddl_active() -> list[tuple[Path, str, int]]:
    """Scan runtime code paths for DDL statements. Returns list of
    (file, matched_pattern, line_number). Excludes migrations, tests,
    and pure documentation."""
    hits: list[tuple[Path, str, int]] = []
    roots = [
        _REPO_ROOT / "genlab-core" / "src",
        _REPO_ROOT / "scripts",
        _REPO_ROOT / "dashboard" / "server",
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            # Skip migrations (proper DDL home) and tests
            parts_set = set(path.parts)
            if "migrations" in parts_set or "tests" in parts_set:
                continue
            if path.name.endswith("_test.py") or path.name.startswith("test_"):
                continue
            try:
                text = path.read_text()
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                # Skip comment-only lines (documenting a removed DDL is fine)
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""'):
                    continue
                for pat in _DDL_PATTERNS:
                    if pat in line:
                        # Simple heuristic: comment on same line means
                        # the DDL is in a docstring or comment. Skip.
                        if "#" in line[: line.find(pat)]:
                            continue
                        # Ignore string literals that document the pattern
                        # (only-fire on cur.execute style calls). Check
                        # for a nearby cur.execute or conn.execute — if
                        # not present, likely documentation.
                        # Cheap check: scan 5 lines above for "execute("
                        window = text.splitlines()[max(0, i - 6): i]
                        if not any("execute(" in w for w in window):
                            continue
                        hits.append((path.relative_to(_REPO_ROOT), pat, i))
                        break  # one hit per line is enough
    return hits


class TestNoRuntimeDdl:
    def test_no_ddl_in_runtime_code_paths(self):
        """The invariant: no CREATE TABLE / CREATE INDEX / ALTER TABLE
        in genlab-core/src/, scripts/, or dashboard/server/. All 4
        Aug 2026 discoveries mapped to this pattern. Regression: adding
        DDL to a runtime file re-introduces the silent-fail class.

        If this test fires, the fix is: move the DDL to an alembic
        migration in genlab-core/migrations/versions/.
        """
        hits = _files_with_ddl_active()
        if hits:
            report = "\n".join(
                f"  {f}:{ln} — {pat}" for f, pat, ln in hits
            )
            raise AssertionError(
                f"Found {len(hits)} DDL statement(s) in runtime code paths:\n"
                f"{report}\n\n"
                f"DDL belongs in alembic migrations, not runtime code. "
                f"Runtime code connects as genlab_app which lacks CREATE "
                f"privilege on schema public (per Audit A hardening) — "
                f"DDL either hard-crashes or silent-fails via poisoned "
                f"transactions. Move each DDL to a new alembic revision "
                f"in genlab-core/migrations/versions/."
            )

    def test_helper_correctly_ignores_comments(self):
        """Sanity: the source-scan helper doesn't false-positive on
        docstring or comment mentions of DDL patterns."""
        # A file with only comments/docstrings mentioning CREATE TABLE
        # should not produce hits. Verify indirectly by checking that
        # the previously-fixed files (which now contain WORDS like
        # "CREATE TABLE IF NOT EXISTS" in their fix-note comments)
        # do NOT show up in the hits.
        hits = _files_with_ddl_active()
        forbidden_paths = {
            "genlab-core/src/genlab_core/learning/late_reward.py",
            "genlab-core/src/genlab_core/monitoring/health_monitor.py",
        }
        hit_paths = {str(f) for f, _, _ in hits}
        false_positives = forbidden_paths & hit_paths
        assert not false_positives, (
            f"Helper falsely matched documentation-only mentions: "
            f"{false_positives}. This means legitimate 'we removed a DDL "
            f"and left a comment' cases are flagged as hits — the "
            f"heuristic needs tightening."
        )
