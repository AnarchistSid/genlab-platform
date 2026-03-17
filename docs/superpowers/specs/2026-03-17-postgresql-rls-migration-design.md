# PostgreSQL RLS Migration — Repository Pattern

**Goal**: Migrate from SharePoint to PostgreSQL with row-level security for true multi-tenancy, using a repository pattern that allows incremental table-by-table migration.

**Approach**: StorageBackend protocol with SharePointBackend (existing) and PostgresBackend (new). Config-driven per-table backend selection. Start with Blueprints table, migrate remaining 10 tables incrementally.

**Deployment**: Local PostgreSQL via Homebrew (Phase 1). Migrate to Supabase/managed when moving to Phase 2 SaaS.

---

## Architecture

```
Pipeline → BacklogClient (domain methods)
               ↓
         StorageBackend (protocol)
           ├── SharePointBackend (wraps GraphTableProxy — existing)
           └── PostgresBackend (asyncpg + RLS — new)
```

Config-driven per-table routing:
```yaml
# genlab-core/config/storage.yaml
default_backend: sharepoint
table_backends:
  blueprints: postgres      # Phase 1
  stories: sharepoint        # Phase 2
  assets: sharepoint
  templates: sharepoint
  sources: sharepoint
  publishing_analytics: sharepoint  # Phase 3
  analytics: sharepoint
  content_memory: sharepoint  # Phase 4
  bandit_arms: sharepoint
  pending_engagement: sharepoint  # Phase 5
  pending_feedback: sharepoint

postgres:
  host: localhost
  port: 5432
  database: genlab
  user: genlab
  password_env: POSTGRES_PASSWORD  # read from env var
  min_connections: 2
  max_connections: 10
```

## StorageBackend Protocol

```python
class StorageBackend(Protocol):
    def create(self, table: str, record: dict) -> str: ...
    def get(self, table: str, record_id: str) -> Optional[dict]: ...
    def find(self, table: str, *, formula: str = "", niche_id: str = "") -> list[dict]: ...
    def update(self, table: str, record_id: str, fields: dict) -> None: ...
    def delete(self, table: str, record_id: str) -> None: ...
    def batch_create(self, table: str, records: list[dict]) -> list[str]: ...
    def all(self, table: str, *, formula: str = "", niche_id: str = "") -> list[dict]: ...
```

The `formula` parameter accepts the existing OData-like filter syntax for backward compatibility. PostgresBackend translates these to SQL WHERE clauses. SharePointBackend passes them through to GraphTableProxy unchanged.

## BacklogClient Refactoring

BacklogClient's 30+ domain methods stay unchanged in signature. Internally, they switch from:

```python
# Before
self.blueprints.all(formula="...")

# After
self._backend("blueprints").all(formula="...")
```

Where `self._backend(table)` returns the configured StorageBackend for that table.

## PostgreSQL Schema

### Blueprints (Phase 1)

```sql
CREATE TABLE blueprints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    niche_id TEXT NOT NULL,
    candidate_id TEXT UNIQUE NOT NULL,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFTED',
    hook TEXT,
    hook_length INT GENERATED ALWAYS AS (length(hook)) STORED,
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

CREATE INDEX idx_blueprints_niche_status ON blueprints(niche_id, status);
CREATE INDEX idx_blueprints_candidate ON blueprints(candidate_id);
CREATE INDEX idx_blueprints_scheduled ON blueprints(scheduled_for) WHERE scheduled_for IS NOT NULL;
CREATE INDEX idx_blueprints_niche_date ON blueprints(niche_id, created_at DESC);

-- Row-Level Security
ALTER TABLE blueprints ENABLE ROW LEVEL SECURITY;

-- Niche isolation: queries only see rows matching app.niche_id
CREATE POLICY niche_isolation ON blueprints
    USING (niche_id = current_setting('app.niche_id', true)
           OR current_setting('app.niche_id', true) = ''
           OR current_setting('app.niche_id', true) IS NULL);

-- The OR clauses allow:
--   app.niche_id = 'gaming'  → see only gaming rows
--   app.niche_id = ''        → see all rows (admin/dashboard)
--   app.niche_id not set     → see all rows (backward compat)
```

### Stories (Phase 2)

```sql
CREATE TABLE stories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    niche_id TEXT NOT NULL,
    story_id TEXT UNIQUE NOT NULL,
    title TEXT,
    url TEXT,
    source_name TEXT,
    source_type TEXT,
    status TEXT DEFAULT 'INTAKE',
    published_at TIMESTAMPTZ,
    score FLOAT DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT now(),
    extra JSONB DEFAULT '{}'
);

ALTER TABLE stories ENABLE ROW LEVEL SECURITY;
CREATE POLICY niche_isolation ON stories
    USING (niche_id = current_setting('app.niche_id', true)
           OR current_setting('app.niche_id', true) IN ('', NULL));
```

### Remaining tables follow same pattern — promote heavily-queried columns, catch-all `extra JSONB`.

## RLS Integration

```python
class PostgresBackend:
    async def _with_niche(self, niche_id: str, query_fn):
        async with self._pool.acquire() as conn:
            await conn.execute("SET LOCAL app.niche_id = $1", niche_id or "")
            return await query_fn(conn)
```

`SET LOCAL` scopes the setting to the current transaction — no cross-request leaks.

## Formula Translation

Existing code uses OData-like formulas:
```
{status}='PUBLISHED'
AND({niche_id}='gaming', {status}='DRAFTED')
```

PostgresBackend translates these to SQL:
```sql
WHERE status = 'PUBLISHED'
WHERE niche_id = 'gaming' AND status = 'DRAFTED'
```

This is done by extending the existing `formula_to_odata()` function in graph_proxy.py with a `formula_to_sql()` counterpart.

## Data Migration Script

For each table cutover:
1. Create PostgreSQL table via Alembic migration
2. Run `migrate_table.py --table blueprints` to copy all SharePoint records
3. Switch `storage.yaml` to `blueprints: postgres`
4. Run pipeline, verify reads/writes go to PostgreSQL
5. Keep SharePoint as read-only backup for 7 days
6. Remove SharePoint backend for that table

## Migration Phases

| Phase | Tables | Effort | Switch criteria |
|---|---|---|---|
| 0 | Setup: PostgreSQL, asyncpg, Alembic, StorageBackend protocol | 1 day | Tests pass |
| 1 | Blueprints | 1 day | Pipeline creates + reads blueprints from PG |
| 2 | Stories, Assets | 1 day | Pipeline fetches + scores from PG |
| 3 | Publishing_Analytics, Analytics | 1 day | FetchInsights reads from PG |
| 4 | Content_Memory, BanditArms | 1 day | Dedup + learning from PG |
| 5 | PendingEngagement, PendingFeedback | 1 day | Engagement from PG |
| 6 | Templates, Sources | 0.5 day | Config reads from PG |
| 7 | Remove SharePoint dependency | 0.5 day | Final cutover |

## Files

| File | Change |
|---|---|
| `genlab-core/src/genlab_core/storage/__init__.py` | NEW — package |
| `genlab-core/src/genlab_core/storage/protocol.py` | NEW — StorageBackend protocol |
| `genlab-core/src/genlab_core/storage/postgres.py` | NEW — PostgresBackend |
| `genlab-core/src/genlab_core/storage/sharepoint.py` | NEW — wraps BacklogClient |
| `genlab-core/src/genlab_core/storage/factory.py` | NEW — backend factory from config |
| `genlab-core/src/genlab_core/storage/formula_sql.py` | NEW — formula → SQL translator |
| `genlab-core/src/genlab_core/storage/migrate_table.py` | NEW — SP → PG data copier |
| `genlab-core/src/genlab_core/http/backlog_client.py` | MODIFY — delegate to StorageBackend |
| `genlab-core/config/storage.yaml` | NEW — per-table backend config |
| `genlab-core/migrations/env.py` | NEW — Alembic environment |
| `genlab-core/migrations/versions/001_blueprints.py` | NEW — Blueprints table |
| `genlab-core/tests/storage/test_protocol.py` | NEW |
| `genlab-core/tests/storage/test_postgres.py` | NEW |
| `genlab-core/tests/storage/test_formula_sql.py` | NEW |
| `genlab-core/tests/storage/test_factory.py` | NEW |

## Dependencies

- `asyncpg>=0.29` — async PostgreSQL driver
- `alembic>=1.13` — database migrations
- PostgreSQL 16 via `brew install postgresql@16`

## Risks

- Formula translation may miss edge cases — mitigate with comprehensive test suite comparing SharePoint vs PostgreSQL results
- `extra JSONB` column may become a dumping ground — mitigate by promoting columns to proper SQL as usage patterns emerge
- RLS `SET LOCAL` requires transactions — all PostgresBackend operations must run in explicit transactions
