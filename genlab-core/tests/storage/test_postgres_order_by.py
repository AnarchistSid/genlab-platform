"""Batch B (2026-07-01): pin tests for ``order_by`` threading.

The Mission Control disk-cleanup cascade debug session (2026-06-30 →
07-01) surfaced that ``PostgresBackend.find()`` had no ``ORDER BY``
clause at all — when ``max_records`` truncated, callers got an
arbitrary slice. The dashboard's overview endpoint was hit hardest:
1414 ARCHIVED rows filled the 500-cap, silently dropping actively-
scheduled VISUAL_READY blueprints in the tail.

The Batch B structural fix threads ``order_by`` from the dashboard
call site all the way to ``PostgresBackend.find()``:

    overview.py
      → client.blueprints.all(order_by="updated_at DESC")
        (ScheduleGuardedProxy.__getattr__ forwards)
        → PostgresTableProxy.all(order_by=...)
          → PostgresTableProxy._backend.find(order_by=...)
            → PostgresBackend.find(order_by=...) [whitelisted]

Any Edit that drops the kwarg from ANY layer resurrects the silent-
truncation bug. These pins catch that at test time.

The whitelist regex is the safety guarantee: ``order_by`` gets f-
stringed into the SQL, so it MUST reject anything that could inject
another statement or clause. Pins here also exercise the fallback-
to-default path so a bad ORDER BY doesn't crash the query.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ── source pins (regression-proof against silent kwarg-drop) ──────


@pytest.fixture(scope="module")
def postgres_source() -> str:
    import genlab_core.storage.postgres as mod

    return Path(mod.__file__).read_text()


def test_backend_find_accepts_order_by(postgres_source):
    """PostgresBackend.find must expose ``order_by: str | None``."""
    # Find the backend's find() — it's the FIRST occurrence in the
    # module (defined on PostgresBackend, ~line 576).
    match = re.search(
        r"class PostgresBackend[\s\S]+?    def find\(\s*self,[\s\S]+?\) -> list\[dict\[str, Any\]\]:",
        postgres_source,
    )
    assert match, "PostgresBackend.find() signature not found"
    assert "order_by: str | None = None" in match.group(0), (
        "PostgresBackend.find() must expose ``order_by: str | None = None`` "
        "so callers can override the default ORDER BY. Dropping this parameter "
        "resurrects the silent-truncation bug that the disk-cleanup cascade "
        "(2026-06-30) surfaced."
    )


def test_backend_all_forwards_order_by(postgres_source):
    """PostgresBackend.all() must forward ``order_by`` to find()."""
    match = re.search(
        r"    def all\(\s*self,[\s\S]+?return self\.find\(\s*table,[\s\S]+?\)",
        postgres_source,
    )
    assert match, "PostgresBackend.all() signature not found"
    body = match.group(0)
    assert "order_by: str | None = None" in body, (
        "PostgresBackend.all() must accept ``order_by`` and pass it to find()"
    )
    assert "order_by=order_by" in body, (
        "PostgresBackend.all() must forward ``order_by=order_by`` to find(). "
        "Dropping this line re-introduces the silent kwarg swallow."
    )


def test_table_proxy_find_forwards_order_by(postgres_source):
    """PostgresTableProxy.find() must forward ``order_by`` (middle layer)."""
    match = re.search(
        r"class PostgresTableProxy[\s\S]+?    def find\([\s\S]+?self\._backend\.find\([\s\S]+?\)",
        postgres_source,
    )
    assert match, "PostgresTableProxy.find() not found"
    body = match.group(0)
    assert "order_by: str | None = None" in body, (
        "PostgresTableProxy.find() must accept ``order_by``"
    )
    assert "order_by=order_by" in body, (
        "PostgresTableProxy.find() must forward ``order_by`` to the backend. "
        "Without it, dashboard's ``client.blueprints.find(order_by=…)`` gets "
        "silently swallowed at the wrapper layer."
    )


def test_table_proxy_all_forwards_order_by(postgres_source):
    """PostgresTableProxy.all() must forward ``order_by`` (the path
    the dashboard overview endpoint actually uses)."""
    # This is the SECOND ``def all(`` in the file — the first is
    # PostgresBackend.all().
    all_positions = [
        i for i in range(len(postgres_source)) if postgres_source.startswith("    def all(", i)
    ]
    assert len(all_positions) >= 2, "expected 2+ ``def all(`` in postgres.py"
    proxy_all = postgres_source[all_positions[1] : all_positions[1] + 800]
    assert "order_by: str | None = None" in proxy_all, (
        "PostgresTableProxy.all() must accept ``order_by``"
    )
    assert "order_by=order_by" in proxy_all, (
        "PostgresTableProxy.all() must forward ``order_by`` to the backend"
    )


def test_whitelist_regex_present(postgres_source):
    """The whitelist regex that gates ``order_by`` string injection MUST
    remain in place. Without it, an attacker (or a bug in a caller)
    could smuggle arbitrary SQL through this parameter."""
    assert r"^[a-z_]+(_at|_for|_id)?" in postgres_source, (
        "postgres.find() must whitelist-validate ``order_by`` against the "
        "regex ``^[a-z_]+(_at|_for|_id)?\\s+(ASC|DESC)(\\s+NULLS\\s+(FIRST|LAST))?$``. "
        "This prevents SQL injection through the f-stringed clause."
    )
    assert "falling back to created_at DESC" in postgres_source, (
        "Malformed ``order_by`` must fall back to the default (``created_at DESC``) "
        "with a warning log — never crash the query, never trust the input."
    )


# ── dashboard call-site pin ──────────────────────────────────────


def test_overview_passes_order_by_updated_at_desc():
    """The dashboard's overview endpoint MUST pass
    ``order_by='updated_at DESC'``. Without it, the 3000 max_records
    cap truncates the ARCHIVED tail (1400+ rows) at some arbitrary
    point, silently invisible-ing actively-managed VISUAL_READY
    blueprints that the operator IS working with."""
    src = (
        Path(__file__).resolve().parents[3] / "dashboard" / "server" / "api" / "overview.py"
    ).read_text()

    assert 'order_by="updated_at DESC"' in src or "order_by='updated_at DESC'" in src, (
        "dashboard/server/api/overview.py must call ``client.blueprints.all("
        "..., order_by='updated_at DESC')``. Dropping this kwarg silently "
        "reverts to the legacy ``created_at DESC`` sort — actively-managed "
        "blueprints get dropped from Mission Control when max_records "
        "truncates. See the 2026-06-30 disk-cleanup cascade session."
    )
