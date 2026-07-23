"""2026-07-24: pin tests for the belt-and-suspenders niche_id filter
in ``PostgresBackend.find()``.

Discovery: the ``genlab`` role has ``Bypass RLS`` attribute in prod, so
the ``SET set_config('app.niche_id', ...)`` machinery in find() is
SILENTLY SKIPPED at query time. Result: auto-approver's per-niche
blueprint query returns blueprints from OTHER niches — gaming
blueprints being gate-evaluated under ai_creators / sports policy.

The fix injects ``AND niche_id = %s`` into the WHERE clause when the
caller passes ``niche_id=<non-empty>`` AND the table's promoted
columns include ``niche_id``. Handles both RLS-on and RLS-bypassed
callers correctly. Admin-mode (niche_id="") preserves historical
"no filter" behavior for cross-niche queries.

Pins:
  - Belt-and-suspenders WHERE clause exists in source
  - Empty niche_id skips the filter (admin mode)
  - Only tables with niche_id in PROMOTED_COLUMNS get the filter
  - Uses parameterized query (no SQL injection risk)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def postgres_source() -> str:
    import genlab_core.storage.postgres as mod

    return Path(mod.__file__).read_text()


def test_niche_filter_belt_and_suspenders_exists(postgres_source):
    """The explicit ``AND niche_id = %s`` clause MUST exist in the
    source. Removing it re-opens the RLS-bypass vulnerability."""
    # There should be at least one occurrence of the belt-and-
    # suspenders SQL fragment.
    assert (
        "AND niche_id = %s" in postgres_source
        or "WHERE niche_id = %s" in postgres_source
    ), (
        "PostgresBackend.find must inject explicit niche_id filter — "
        "RLS is bypassed for genlab role in prod, so app.niche_id "
        "config alone is not enforcing isolation. If this pin fails, "
        "auto-approver + other schedulers will start examining "
        "cross-niche blueprints again."
    )


def test_niche_filter_gated_on_promoted_columns(postgres_source):
    """Only tables whose PROMOTED_COLUMNS contain 'niche_id' get the
    filter. Applying it blindly to bandit_arms / config_updates would
    500 on 'column niche_id does not exist'."""
    # Check the exact gating pattern exists in source.
    assert (
        'PROMOTED_COLUMNS.get(table, set())' in postgres_source
        and '"niche_id" in PROMOTED_COLUMNS.get(table, set())' in postgres_source
    ), (
        "niche filter must gate on PROMOTED_COLUMNS[table] to avoid "
        "500ing on niche-blind tables (bandit_arms, config_updates)."
    )


def test_niche_filter_admin_mode_bypass(postgres_source):
    """Empty niche_id string (admin mode) must bypass the filter.
    Otherwise cross-niche queries (dashboard overview, cleanup ops)
    break."""
    # The gate `if niche_id and ...` handles falsy niche_id — empty
    # string is falsy, so the filter is skipped. Pin the shape.
    assert re.search(
        r"if\s*\(\s*niche_id\s*\n?\s*and\s*\n?\s*", postgres_source
    ), (
        "niche filter must have `if niche_id and ...` gate — empty "
        "string means admin mode and must skip the filter."
    )


def test_niche_filter_is_parameterized(postgres_source):
    """Explicit AND niche_id = %s uses parameterized query — never
    f-strings the niche_id into SQL. Pins against future refactor
    that might interpolate."""
    # The safe pattern uses %s placeholder + params.append(niche_id).
    assert "params.append(niche_id)" in postgres_source, (
        "niche filter must use parameterized query (params.append), "
        "not f-string interpolation."
    )


def test_niche_filter_blueprints_table_included(postgres_source):
    """blueprints table has niche_id in PROMOTED_COLUMNS — so the
    auto-approver's query gets the filter. This is THE reason we're
    here. Pin the table's presence."""
    # PROMOTED_COLUMNS is a dict literal at module top. Find the
    # blueprints entry and check niche_id is in its set.
    m = re.search(
        r'"blueprints"\s*:\s*\{([^}]+)\}',
        postgres_source,
        re.DOTALL,
    )
    assert m is not None, "PROMOTED_COLUMNS['blueprints'] set missing"
    assert '"niche_id"' in m.group(1), (
        "blueprints table must have niche_id in PROMOTED_COLUMNS — "
        "otherwise the fix doesn't apply and auto-approver is still "
        "cross-niche-leaky."
    )
