# PostgreSQL RLS Migration (Phase 0 + 1) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a StorageBackend abstraction and PostgresBackend implementation, then migrate the Blueprints table from SharePoint to PostgreSQL with RLS.

**Architecture:** StorageBackend protocol defines CRUD interface. SharePointBackend wraps existing GraphTableProxy. PostgresBackend uses asyncpg with RLS via `SET LOCAL app.niche_id`. BacklogClient delegates to the appropriate backend per table via config.

**Tech Stack:** Python 3.14, asyncpg, Alembic, PostgreSQL 14 (local Homebrew)

**Spec:** `docs/superpowers/specs/2026-03-17-postgresql-rls-migration-design.md`

---

## Chunk 1: Phase 0 — StorageBackend Protocol + Setup

### Task 1: Install dependencies + create database

**Files:**
- Modify: `genlab-core/pyproject.toml`

- [ ] **Step 1: Add asyncpg + alembic to genlab-core**

```bash
cd /Users/anarchistsid/GenLab && uv add asyncpg alembic --package genlab-core
```

- [ ] **Step 2: Create PostgreSQL database + user**

```bash
createdb genlab 2>/dev/null || echo "DB already exists"
psql genlab -c "CREATE ROLE genlab WITH LOGIN PASSWORD 'genlab_dev';" 2>/dev/null || echo "Role exists"
psql genlab -c "GRANT ALL PRIVILEGES ON DATABASE genlab TO genlab;"
psql genlab -c "ALTER DATABASE genlab OWNER TO genlab;"
psql genlab -c "SELECT version();"
```

- [ ] **Step 3: Add POSTGRES_PASSWORD to .env**

```bash
echo 'POSTGRES_PASSWORD=genlab_dev' >> /Users/anarchistsid/GenLab/.env
```

- [ ] **Step 4: Commit**

```bash
git add genlab-core/pyproject.toml uv.lock
git commit -m "deps: add asyncpg + alembic for PostgreSQL migration"
```

---

### Task 2: Create StorageBackend protocol

**Files:**
- Create: `genlab-core/src/genlab_core/storage/__init__.py`
- Create: `genlab-core/src/genlab_core/storage/protocol.py`
- Test: `genlab-core/tests/storage/test_protocol.py`

- [ ] **Step 1: Write failing test**

```python
# genlab-core/tests/storage/test_protocol.py
"""Tests for StorageBackend protocol compliance."""
from genlab_core.storage.protocol import StorageBackend


def test_protocol_exists():
    assert hasattr(StorageBackend, 'create')
    assert hasattr(StorageBackend, 'get')
    assert hasattr(StorageBackend, 'find')
    assert hasattr(StorageBackend, 'update')
    assert hasattr(StorageBackend, 'delete')
    assert hasattr(StorageBackend, 'batch_create')


def test_protocol_is_runtime_checkable():
    from typing import runtime_checkable, Protocol
    assert issubclass(type(StorageBackend), type(Protocol))
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run --package genlab-core pytest genlab-core/tests/storage/test_protocol.py -v --tb=short
```

- [ ] **Step 3: Implement protocol**

```python
# genlab-core/src/genlab_core/storage/__init__.py
"""Storage abstraction layer — repository pattern for multi-backend support."""
from genlab_core.storage.protocol import StorageBackend

__all__ = ["StorageBackend"]
```

```python
# genlab-core/src/genlab_core/storage/protocol.py
"""StorageBackend protocol — the interface all backends must implement."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Abstract storage backend for GenLab data tables.

    Implementations: SharePointBackend (existing), PostgresBackend (new).
    BacklogClient delegates to the appropriate backend per table.
    """

    def create(self, table: str, record: Dict[str, Any]) -> str:
        """Create a record. Returns the new record ID."""
        ...

    def get(self, table: str, record_id: str) -> Optional[Dict[str, Any]]:
        """Get a single record by ID. Returns None if not found."""
        ...

    def find(
        self, table: str, *, formula: str = "", niche_id: str = "",
    ) -> List[Dict[str, Any]]:
        """Find records matching a formula filter. Returns list of records."""
        ...

    def update(
        self, table: str, record_id: str, fields: Dict[str, Any],
    ) -> None:
        """Update fields on an existing record."""
        ...

    def delete(self, table: str, record_id: str) -> None:
        """Delete a record by ID."""
        ...

    def batch_create(
        self, table: str, records: List[Dict[str, Any]],
    ) -> List[str]:
        """Create multiple records. Returns list of new record IDs."""
        ...
```

- [ ] **Step 4: Run tests**

```bash
uv run --package genlab-core pytest genlab-core/tests/storage/test_protocol.py -v
```

- [ ] **Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/storage/ genlab-core/tests/storage/
git commit -m "feat(storage): add StorageBackend protocol for multi-backend support"
```

---

### Task 3: Create SharePointBackend wrapper

**Files:**
- Create: `genlab-core/src/genlab_core/storage/sharepoint.py`
- Test: `genlab-core/tests/storage/test_sharepoint_backend.py`

- [ ] **Step 1: Write failing test**

```python
# genlab-core/tests/storage/test_sharepoint_backend.py
"""Tests for SharePointBackend wrapping GraphTableProxy."""
from unittest.mock import MagicMock
from genlab_core.storage.sharepoint import SharePointBackend
from genlab_core.storage.protocol import StorageBackend


def test_implements_protocol():
    backend = SharePointBackend.__new__(SharePointBackend)
    assert isinstance(backend, StorageBackend)


def test_create_delegates_to_proxy():
    mock_proxy = MagicMock()
    mock_proxy.create.return_value = {"id": "rec_123", "fields": {"title": "test"}}
    backend = SharePointBackend({"blueprints": mock_proxy})
    result = backend.create("blueprints", {"title": "test"})
    assert result == "rec_123"
    mock_proxy.create.assert_called_once()


def test_find_delegates_with_formula():
    mock_proxy = MagicMock()
    mock_proxy.all.return_value = [
        {"id": "rec_1", "fields": {"status": "DRAFTED"}},
    ]
    backend = SharePointBackend({"blueprints": mock_proxy})
    results = backend.find("blueprints", formula="{status}='DRAFTED'")
    assert len(results) == 1
    mock_proxy.all.assert_called_once()
```

- [ ] **Step 2: Implement SharePointBackend**

```python
# genlab-core/src/genlab_core/storage/sharepoint.py
"""SharePointBackend — wraps existing GraphTableProxy instances."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from genlab_core.http.graph_proxy import GraphTableProxy

logger = logging.getLogger(__name__)


class SharePointBackend:
    """Storage backend backed by Microsoft SharePoint Lists via Graph API.

    Wraps the existing GraphTableProxy objects. This is the default backend
    and preserves all existing behavior.
    """

    def __init__(self, proxies: Dict[str, GraphTableProxy]) -> None:
        self._proxies = proxies

    def _proxy(self, table: str) -> GraphTableProxy:
        proxy = self._proxies.get(table)
        if not proxy:
            raise KeyError(f"No SharePoint proxy configured for table '{table}'")
        return proxy

    def create(self, table: str, record: Dict[str, Any]) -> str:
        result = self._proxy(table).create(record)
        return result.get("id", "")

    def get(self, table: str, record_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self._proxy(table).get(record_id)
        except Exception:
            return None

    def find(
        self, table: str, *, formula: str = "", niche_id: str = "",
    ) -> List[Dict[str, Any]]:
        from genlab_core.http.backlog_client import _inject_niche_filter
        effective_formula = _inject_niche_filter(formula, niche_id) if niche_id else formula
        return self._proxy(table).all(formula=effective_formula)

    def update(
        self, table: str, record_id: str, fields: Dict[str, Any],
    ) -> None:
        self._proxy(table).update(record_id, fields)

    def delete(self, table: str, record_id: str) -> None:
        self._proxy(table).delete(record_id)

    def batch_create(
        self, table: str, records: List[Dict[str, Any]],
    ) -> List[str]:
        results = self._proxy(table).batch_create(records)
        return [r.get("id", "") for r in results]
```

- [ ] **Step 3: Run tests + commit**

```bash
uv run --package genlab-core pytest genlab-core/tests/storage/ -v --tb=short
git add genlab-core/src/genlab_core/storage/sharepoint.py genlab-core/tests/storage/
git commit -m "feat(storage): add SharePointBackend wrapping existing GraphTableProxy"
```

---

### Task 4: Create storage config + factory

**Files:**
- Create: `genlab-core/config/storage.yaml`
- Create: `genlab-core/src/genlab_core/storage/factory.py`
- Test: `genlab-core/tests/storage/test_factory.py`

- [ ] **Step 1: Create storage.yaml**

```yaml
# genlab-core/config/storage.yaml
# Per-table backend routing. Change a table from "sharepoint" to "postgres"
# to migrate it. Both backends can run simultaneously.

default_backend: sharepoint

table_backends:
  blueprints: sharepoint
  stories: sharepoint
  assets: sharepoint
  templates: sharepoint
  sources: sharepoint
  publishing_analytics: sharepoint
  analytics: sharepoint
  content_memory: sharepoint
  bandit_arms: sharepoint
  pending_engagement: sharepoint
  pending_feedback: sharepoint

postgres:
  host: localhost
  port: 5432
  database: genlab
  user: genlab
  password_env: POSTGRES_PASSWORD
  min_connections: 2
  max_connections: 10
```

- [ ] **Step 2: Write tests for factory**

```python
# genlab-core/tests/storage/test_factory.py
"""Tests for storage backend factory."""
from unittest.mock import patch, MagicMock
from genlab_core.storage.factory import get_backend_for_table, load_storage_config


def test_load_config():
    config = load_storage_config()
    assert config["default_backend"] == "sharepoint"
    assert "blueprints" in config["table_backends"]


def test_default_returns_sharepoint():
    with patch("genlab_core.storage.factory._sharepoint_backend") as mock:
        mock.return_value = MagicMock()
        backend = get_backend_for_table("blueprints")
        assert backend is not None
```

- [ ] **Step 3: Implement factory**

```python
# genlab-core/src/genlab_core/storage/factory.py
"""Backend factory — routes tables to the correct StorageBackend."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

from genlab_core.storage.protocol import StorageBackend

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "storage.yaml"


@lru_cache(maxsize=1)
def load_storage_config() -> dict:
    if _CONFIG_PATH.exists():
        return yaml.safe_load(_CONFIG_PATH.read_text()) or {}
    logger.warning("storage.yaml not found at %s, using defaults", _CONFIG_PATH)
    return {"default_backend": "sharepoint", "table_backends": {}}


def get_backend_for_table(table: str) -> StorageBackend:
    """Return the configured StorageBackend for a given table name."""
    config = load_storage_config()
    backend_type = config.get("table_backends", {}).get(
        table, config.get("default_backend", "sharepoint")
    )

    if backend_type == "postgres":
        return _postgres_backend(config.get("postgres", {}))
    return _sharepoint_backend()


def _sharepoint_backend() -> StorageBackend:
    """Return the shared SharePointBackend singleton."""
    # Lazy import to avoid circular dependency
    from genlab_core.storage.sharepoint import SharePointBackend
    from genlab_core.http.backlog_client import BacklogClient

    client = BacklogClient()
    proxies = {}
    for attr in ("stories", "blueprints", "templates", "assets", "sources"):
        proxy = getattr(client, attr, None)
        if proxy is not None:
            # Unwrap ScheduleGuardedProxy if needed
            actual = getattr(proxy, "_proxy", proxy)
            proxies[attr] = actual
    return SharePointBackend(proxies)


def _postgres_backend(pg_config: dict) -> StorageBackend:
    """Return the shared PostgresBackend singleton."""
    from genlab_core.storage.postgres import PostgresBackend

    password = os.environ.get(pg_config.get("password_env", "POSTGRES_PASSWORD"), "")
    return PostgresBackend(
        host=pg_config.get("host", "localhost"),
        port=pg_config.get("port", 5432),
        database=pg_config.get("database", "genlab"),
        user=pg_config.get("user", "genlab"),
        password=password,
    )
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run --package genlab-core pytest genlab-core/tests/storage/ -v --tb=short
git add genlab-core/src/genlab_core/storage/factory.py genlab-core/config/storage.yaml genlab-core/tests/storage/
git commit -m "feat(storage): add factory + storage.yaml for per-table backend routing"
```

---

## Chunk 2: Phase 1 — PostgresBackend + Blueprints Table

### Task 5: Create Alembic migrations directory

- [ ] **Step 1: Initialize Alembic**

```bash
cd /Users/anarchistsid/GenLab/genlab-core && uv run alembic init migrations
```

- [ ] **Step 2: Configure alembic.ini to use env var for DB URL**

Edit `genlab-core/alembic.ini`:
```ini
sqlalchemy.url = postgresql://genlab:%(POSTGRES_PASSWORD)s@localhost:5432/genlab
```

Edit `genlab-core/migrations/env.py` to read password from os.environ.

- [ ] **Step 3: Create Blueprints migration**

```bash
cd /Users/anarchistsid/GenLab/genlab-core && uv run alembic revision -m "create blueprints table"
```

Then edit the generated migration file:

```python
def upgrade():
    op.execute("""
    CREATE TABLE IF NOT EXISTS blueprints (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id TEXT NOT NULL,
        candidate_id TEXT UNIQUE NOT NULL,
        title TEXT,
        status TEXT NOT NULL DEFAULT 'DRAFTED',
        hook TEXT,
        scheduled_for TIMESTAMPTZ,
        platform_publish_status JSONB DEFAULT '{}',
        video_id TEXT,
        video_url TEXT,
        source_url TEXT,
        priority_score FLOAT DEFAULT 0.0,
        action_taken TEXT,
        reviewed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        extra JSONB DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_bp_niche_status ON blueprints(niche_id, status);
    CREATE INDEX IF NOT EXISTS idx_bp_candidate ON blueprints(candidate_id);
    CREATE INDEX IF NOT EXISTS idx_bp_scheduled ON blueprints(scheduled_for)
        WHERE scheduled_for IS NOT NULL;

    ALTER TABLE blueprints ENABLE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS niche_isolation ON blueprints;
    CREATE POLICY niche_isolation ON blueprints
        USING (niche_id = current_setting('app.niche_id', true)
               OR current_setting('app.niche_id', true) IN ('', 'all')
               OR current_setting('app.niche_id', true) IS NULL);
    """)

def downgrade():
    op.execute("DROP TABLE IF EXISTS blueprints CASCADE;")
```

- [ ] **Step 4: Run migration**

```bash
cd /Users/anarchistsid/GenLab/genlab-core && POSTGRES_PASSWORD=genlab_dev uv run alembic upgrade head
```

- [ ] **Step 5: Verify table exists**

```bash
psql genlab -c "\d blueprints"
psql genlab -c "SELECT * FROM pg_policies WHERE tablename = 'blueprints';"
```

- [ ] **Step 6: Commit**

```bash
git add genlab-core/alembic.ini genlab-core/migrations/
git commit -m "feat(storage): add Alembic + blueprints table with RLS policy"
```

---

### Task 6: Implement PostgresBackend

**Files:**
- Create: `genlab-core/src/genlab_core/storage/postgres.py`
- Create: `genlab-core/src/genlab_core/storage/formula_sql.py`
- Test: `genlab-core/tests/storage/test_postgres.py`
- Test: `genlab-core/tests/storage/test_formula_sql.py`

- [ ] **Step 1: Write formula_sql translator tests**

```python
# genlab-core/tests/storage/test_formula_sql.py
"""Tests for OData formula → SQL WHERE translation."""
from genlab_core.storage.formula_sql import formula_to_sql


class TestFormulaToSQL:
    def test_simple_equality(self):
        sql, params = formula_to_sql("{status}='DRAFTED'")
        assert "status" in sql
        assert "DRAFTED" in params

    def test_and_condition(self):
        sql, params = formula_to_sql("AND({status}='DRAFTED', {niche_id}='gaming')")
        assert "AND" in sql

    def test_empty_formula(self):
        sql, params = formula_to_sql("")
        assert sql == ""
        assert params == []

    def test_none_formula(self):
        sql, params = formula_to_sql(None)
        assert sql == ""
```

- [ ] **Step 2: Implement formula_sql.py**

```python
# genlab-core/src/genlab_core/storage/formula_sql.py
"""Translate OData-like formula strings to PostgreSQL WHERE clauses."""
from __future__ import annotations

import re
from typing import Optional


def formula_to_sql(formula: Optional[str]) -> tuple[str, list]:
    """Convert an OData-like formula to a SQL WHERE clause + params.

    Input:  "{status}='DRAFTED'"
    Output: ("status = $1", ["DRAFTED"])

    Input:  "AND({status}='DRAFTED', {niche_id}='gaming')"
    Output: ("status = $1 AND niche_id = $2", ["DRAFTED", "gaming"])
    """
    if not formula:
        return "", []

    params: list = []
    param_idx = [0]

    def _replace_expr(match: re.Match) -> str:
        field = match.group(1)
        value = match.group(2)
        param_idx[0] += 1
        params.append(value)
        return f"{field} = ${param_idx[0]}"

    # Replace {field}='value' patterns
    result = re.sub(r"\{(\w+)\}='([^']*)'", _replace_expr, formula)

    # Replace AND(...) wrapper
    result = re.sub(r"^AND\((.+)\)$", r"\1", result.strip())

    # Replace OR(...) wrapper
    result = re.sub(r"^OR\((.+)\)$", lambda m: m.group(1).replace(", ", " OR "), result)

    # Clean up commas to AND
    result = result.replace(", ", " AND ")

    return result.strip(), params
```

- [ ] **Step 3: Write PostgresBackend tests**

```python
# genlab-core/tests/storage/test_postgres.py
"""Tests for PostgresBackend — uses real local PostgreSQL."""
import os
import pytest
import asyncio

# Skip if no PostgreSQL available
pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_PASSWORD"),
    reason="POSTGRES_PASSWORD not set — skip PostgreSQL tests"
)


@pytest.fixture
def pg_backend():
    from genlab_core.storage.postgres import PostgresBackend
    backend = PostgresBackend(
        host="localhost", port=5432, database="genlab",
        user="genlab", password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    yield backend
    # Cleanup test data
    asyncio.get_event_loop().run_until_complete(
        backend._execute("DELETE FROM blueprints WHERE niche_id = 'test_niche'")
    )


class TestPostgresBackend:
    def test_create_and_get(self, pg_backend):
        record_id = pg_backend.create("blueprints", {
            "niche_id": "test_niche",
            "candidate_id": "test_cand_001",
            "title": "Test Blueprint",
            "status": "DRAFTED",
        })
        assert record_id

        record = pg_backend.get("blueprints", record_id)
        assert record is not None
        assert record["fields"]["title"] == "Test Blueprint"

    def test_find_with_formula(self, pg_backend):
        pg_backend.create("blueprints", {
            "niche_id": "test_niche",
            "candidate_id": "test_cand_find",
            "status": "DRAFTED",
        })
        results = pg_backend.find("blueprints",
            formula="{status}='DRAFTED'", niche_id="test_niche")
        assert len(results) >= 1

    def test_rls_isolates_niches(self, pg_backend):
        pg_backend.create("blueprints", {
            "niche_id": "niche_a", "candidate_id": "rls_a", "status": "DRAFTED"
        })
        pg_backend.create("blueprints", {
            "niche_id": "niche_b", "candidate_id": "rls_b", "status": "DRAFTED"
        })
        results_a = pg_backend.find("blueprints", niche_id="niche_a")
        results_b = pg_backend.find("blueprints", niche_id="niche_b")
        assert all(r["fields"]["niche_id"] == "niche_a" for r in results_a)
        assert all(r["fields"]["niche_id"] == "niche_b" for r in results_b)
```

- [ ] **Step 4: Implement PostgresBackend**

```python
# genlab-core/src/genlab_core/storage/postgres.py
"""PostgresBackend — asyncpg-based storage with RLS."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Columns that are promoted to proper SQL columns (not in extra JSONB)
PROMOTED_COLUMNS = {
    "blueprints": {
        "niche_id", "candidate_id", "title", "status", "hook",
        "scheduled_for", "platform_publish_status", "video_id",
        "video_url", "source_url", "priority_score", "action_taken",
        "reviewed_at",
    },
}


class PostgresBackend:
    """Storage backend backed by local PostgreSQL with RLS."""

    def __init__(self, host: str, port: int, database: str,
                 user: str, password: str) -> None:
        self._dsn = f"postgresql://{user}:{password}@{host}:{port}/{database}"
        self._pool = None

    def _get_pool(self):
        if self._pool is None:
            import asyncpg
            loop = asyncio.new_event_loop()
            self._pool = loop.run_until_complete(
                asyncpg.create_pool(self._dsn, min_size=2, max_size=10)
            )
        return self._pool

    async def _execute(self, query: str, *args):
        pool = self._get_pool()
        async with pool.acquire() as conn:
            return await conn.execute(query, *args)

    def _split_fields(self, table: str, record: dict) -> tuple[dict, dict]:
        """Split record into promoted columns + extra JSONB."""
        promoted = PROMOTED_COLUMNS.get(table, set())
        cols = {}
        extra = {}
        for k, v in record.items():
            if k in promoted:
                if isinstance(v, dict):
                    cols[k] = json.dumps(v)
                else:
                    cols[k] = v
            else:
                extra[k] = v
        return cols, extra

    def create(self, table: str, record: Dict[str, Any]) -> str:
        record_id = str(uuid.uuid4())
        cols, extra = self._split_fields(table, record)
        cols["extra"] = json.dumps(extra) if extra else "{}"

        col_names = list(cols.keys())
        placeholders = [f"${i+1}" for i in range(len(col_names))]
        values = [cols[c] for c in col_names]

        sql = f"INSERT INTO {table} (id, {', '.join(col_names)}) VALUES ('{record_id}', {', '.join(placeholders)}) RETURNING id"

        loop = asyncio.new_event_loop()
        pool = self._get_pool()

        async def _do():
            async with pool.acquire() as conn:
                row = await conn.fetchrow(sql, *values)
                return str(row["id"]) if row else record_id

        result = loop.run_until_complete(_do())
        return result

    def get(self, table: str, record_id: str) -> Optional[Dict[str, Any]]:
        loop = asyncio.new_event_loop()
        pool = self._get_pool()

        async def _do():
            async with pool.acquire() as conn:
                await conn.execute("SET LOCAL app.niche_id = ''")
                row = await conn.fetchrow(
                    f"SELECT * FROM {table} WHERE id = $1", record_id
                )
                return dict(row) if row else None

        row = loop.run_until_complete(_do())
        if not row:
            return None
        return self._row_to_record(row)

    def find(
        self, table: str, *, formula: str = "", niche_id: str = "",
    ) -> List[Dict[str, Any]]:
        from genlab_core.storage.formula_sql import formula_to_sql

        where_clause, params = formula_to_sql(formula)
        loop = asyncio.new_event_loop()
        pool = self._get_pool()

        async def _do():
            async with pool.acquire() as conn:
                await conn.execute("SET LOCAL app.niche_id = $1", niche_id or "")
                sql = f"SELECT * FROM {table}"
                if where_clause:
                    sql += f" WHERE {where_clause}"
                sql += " ORDER BY created_at DESC"
                rows = await conn.fetch(sql, *params)
                return [self._row_to_record(dict(r)) for r in rows]

        return loop.run_until_complete(_do())

    def update(
        self, table: str, record_id: str, fields: Dict[str, Any],
    ) -> None:
        cols, extra = self._split_fields(table, fields)
        loop = asyncio.new_event_loop()
        pool = self._get_pool()

        async def _do():
            async with pool.acquire() as conn:
                await conn.execute("SET LOCAL app.niche_id = ''")
                sets = []
                values = []
                for i, (k, v) in enumerate(cols.items(), 1):
                    sets.append(f"{k} = ${i}")
                    values.append(v)
                if extra:
                    sets.append(f"extra = extra || ${len(values)+1}::jsonb")
                    values.append(json.dumps(extra))
                sets.append(f"updated_at = now()")
                values.append(record_id)
                sql = f"UPDATE {table} SET {', '.join(sets)} WHERE id = ${len(values)}"
                await conn.execute(sql, *values)

        loop.run_until_complete(_do())

    def delete(self, table: str, record_id: str) -> None:
        loop = asyncio.new_event_loop()
        pool = self._get_pool()

        async def _do():
            async with pool.acquire() as conn:
                await conn.execute("SET LOCAL app.niche_id = ''")
                await conn.execute(f"DELETE FROM {table} WHERE id = $1", record_id)

        loop.run_until_complete(_do())

    def batch_create(
        self, table: str, records: List[Dict[str, Any]],
    ) -> List[str]:
        return [self.create(table, r) for r in records]

    @staticmethod
    def _row_to_record(row: dict) -> dict:
        """Convert a PostgreSQL row to the {id, fields} format BacklogClient expects."""
        record_id = str(row.pop("id", ""))
        extra = row.pop("extra", None) or {}
        if isinstance(extra, str):
            extra = json.loads(extra)
        # Merge promoted columns + extra into fields
        fields = {k: v for k, v in row.items() if k not in ("created_at", "updated_at")}
        fields.update(extra)
        return {"id": record_id, "fields": fields}
```

- [ ] **Step 5: Run tests**

```bash
POSTGRES_PASSWORD=genlab_dev uv run --package genlab-core pytest genlab-core/tests/storage/ -v --tb=short
```

- [ ] **Step 6: Commit**

```bash
git add genlab-core/src/genlab_core/storage/postgres.py \
  genlab-core/src/genlab_core/storage/formula_sql.py \
  genlab-core/tests/storage/
git commit -m "feat(storage): implement PostgresBackend with RLS + formula_to_sql translator"
```

---

### Task 7: Switch Blueprints to PostgreSQL

- [ ] **Step 1: Update storage.yaml**

Change `blueprints: sharepoint` to `blueprints: postgres`.

- [ ] **Step 2: Run full test suite to verify no regressions**

```bash
POSTGRES_PASSWORD=genlab_dev uv run --package genlab-core pytest genlab-core/tests/ -x -q --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add genlab-core/config/storage.yaml
git commit -m "feat(storage): switch blueprints table to PostgreSQL backend

Phase 1 complete. Blueprints reads/writes now go to local PostgreSQL
with RLS niche isolation. All other tables remain on SharePoint."
```
