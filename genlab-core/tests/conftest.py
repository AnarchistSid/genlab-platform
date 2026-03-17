"""Test configuration — ensure tests use SharePoint mocks, not live Postgres."""
import os

# Remove Postgres env vars so BacklogClient falls back to SharePoint proxies.
# Tests mock the SharePoint Graph API — they don't need a real database.
os.environ.pop("DATABASE_URL", None)
os.environ.pop("GENLAB_USE_POSTGRES", None)
