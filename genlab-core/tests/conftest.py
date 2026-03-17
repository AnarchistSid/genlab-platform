"""Test configuration — ensure tests use SharePoint mocks, not live Postgres."""
import os

# Remove DATABASE_URL so BacklogClient falls back to SharePoint proxies.
# Tests mock the SharePoint Graph API — they don't need a real database.
os.environ.pop("DATABASE_URL", None)

# Also reset the factory config cache so it doesn't bleed between tests
try:
    from genlab_core.storage.factory import reset_backends
    reset_backends()
except ImportError:
    pass
