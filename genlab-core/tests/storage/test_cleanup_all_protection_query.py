"""Pin: ``scripts/cleanup_all.sh``'s protection query must stay in sync
with ``genlab_core.storage.disk_quota._get_pending_publish_run_ids``.

Both are prod cleanup paths — the shell script fires DAILY via
``genlab-cleanup.timer``, the Python function is called from
``DiskQuotaManager.enforce()`` (which is invoked by other paths).
If the two protection filters diverge, one path can evict media the
other considers active.

## 2026-07-08 live-fire — task #573

The shell script's filter was:

    WHERE status = 'VISUAL_READY'
      AND scheduled_for >= CURRENT_DATE

which missed 3 cases the Python filter catches:

  1. DRAFTED blueprints (rendered, not yet approved)
  2. VISUAL_READY with ``scheduled_for IS NULL`` (rendered, not scheduled)
  3. VISUAL_READY with past ``scheduled_for`` (previously failed to
     publish, awaiting operator retry)

Case 3 hit yesterday's sports blueprint. Its run dir was 8 days old.
The shell cleanup's filter excluded it (past scheduled_for) →
evicted the media → publisher failed at fire time with
"No valid media files for video publish". Anime `83a3f4ad` may have
been affected by the same class.

Fix: shell query broadened to match the Python version's
terminal-state-only exclusion. This pin catches regressions where
either side narrows independently.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from genlab_core.storage.disk_quota import _get_pending_publish_run_ids

REPO_ROOT = Path(__file__).resolve().parents[3]
SHELL_SCRIPT = REPO_ROOT / "scripts" / "cleanup_all.sh"

TERMINAL_STATUSES = frozenset(
    {"PUBLISHED", "ARCHIVED", "PUBLISH_FAILED", "REJECTED"}
)


class TestShellCleanupMatchesPythonProtection:
    def test_shell_script_present(self):
        assert SHELL_SCRIPT.is_file()

    def test_shell_query_uses_terminal_status_exclusion(self):
        """The shell version must use ``status NOT IN (terminal states)``
        — NOT ``status = 'VISUAL_READY'``. The latter misses DRAFTED
        + VISUAL_READY-without-scheduled-for cases that the Python
        version catches."""
        content = SHELL_SCRIPT.read_text()
        # Must contain the terminal-state exclusion.
        assert "status NOT IN" in content, (
            "cleanup_all.sh's protection query must use 'status NOT IN "
            "(terminal states)' to match the Python version. Task #573 "
            "regression."
        )
        for status in TERMINAL_STATUSES:
            assert f"'{status}'" in content, (
                f"cleanup_all.sh must exclude '{status}' from the "
                f"protection query to match the Python version. See "
                f"disk_quota._get_pending_publish_run_ids."
            )

    def _sql_region(self) -> str:
        """Extract only the SQL heredoc between the ``cur.execute``
        opening triple-quote and its closing triple-quote. Comments
        and docstrings elsewhere in the script legitimately mention
        the buggy strings while explaining the fix — those must NOT
        trip the anti-narrowing pins."""
        content = SHELL_SCRIPT.read_text()
        # Delimiter in the shell script's Python-c body is
        # ``cur.execute(\"\"\"...\"\"\")`` — the quotes are backslash-
        # escaped for the outer bash context.
        opener = 'cur.execute(' + '\\"' * 3
        closer = '\\"' * 3 + ")"
        start = content.find(opener)
        assert start >= 0, "SQL region not found — script structure changed"
        end = content.find(closer, start)
        assert end > start, "SQL region closing not found"
        return content[start:end]

    def test_shell_query_does_not_re_narrow_by_status(self):
        """A regression back to ``status = 'VISUAL_READY'`` (or any
        single-status equality) inside the SQL region would silently
        re-expose the class-of-bug. Ban that exact pattern in the SQL
        heredoc only — the surrounding doc-comment references the
        buggy string legitimately as an explanation."""
        sql = self._sql_region()
        assert "status = 'VISUAL_READY'" not in sql, (
            "cleanup_all.sh's SQL must NOT narrow to status='VISUAL_READY' "
            "— that misses DRAFTED + VISUAL_READY-without-scheduled-for, "
            "the exact class-of-bug from task #573."
        )

    def test_shell_query_does_not_re_narrow_by_scheduled_for(self):
        """A regression to ``scheduled_for >= CURRENT_DATE`` in the SQL
        region misses past-scheduled blueprints awaiting retry (the
        2026-07-07 sports case). Ban that SQL filter in the heredoc
        only."""
        sql = self._sql_region()
        assert "scheduled_for >= CURRENT_DATE" not in sql, (
            "cleanup_all.sh's SQL must NOT filter by scheduled_for "
            "date — that misses blueprints with past scheduled_for "
            "awaiting retry (2026-07-07 sports live-fire, task #573)."
        )

    def test_python_protection_names_same_terminal_states(self):
        """The Python side is the reference. If someone changes the
        set of terminal statuses in Python (e.g. add ``BLOCKED``),
        the shell script must be updated too — this test names all
        4 canonical terminal statuses and asserts BOTH sides carry
        them."""
        py_source = inspect.getsource(_get_pending_publish_run_ids)
        for status in TERMINAL_STATUSES:
            assert f"'{status}'" in py_source, (
                f"disk_quota._get_pending_publish_run_ids must exclude "
                f"'{status}' — this is a canonical terminal status. If "
                f"you're intentionally reclassifying it, update "
                f"TERMINAL_STATUSES in this pin test AND the shell "
                f"script's SQL query too."
            )

    def test_shell_and_python_use_jsonb_membership_test(self):
        """Both queries must use ``extra ? 'visual_paths'`` (JSONB
        membership) not ``extra->>'visual_paths' IS NOT NULL`` (which
        also matches SQL NULL that JSON serialization can round-trip
        differently). The previous shell version used the latter."""
        shell_content = SHELL_SCRIPT.read_text()
        py_source = inspect.getsource(_get_pending_publish_run_ids)
        assert "extra ? 'visual_paths'" in shell_content, (
            "cleanup_all.sh must use 'extra ? \\'visual_paths\\'' "
            "(JSONB membership) to match the Python version's safer "
            "existence check."
        )
        assert "extra ? 'visual_paths'" in py_source, (
            "disk_quota._get_pending_publish_run_ids must use JSONB "
            "membership 'extra ? \\'visual_paths\\''. If Python moves "
            "away from this, update the shell script + this test."
        )
