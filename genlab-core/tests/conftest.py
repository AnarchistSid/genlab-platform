"""Test configuration — ensure tests use SharePoint mocks, not live Postgres.

settings.py calls load_dotenv() at import time, which loads .env values into
os.environ BEFORE conftest runs. We must pop the Postgres vars here AND
individual test fixtures should use patch.dict("os.environ", ...) for full
isolation against re-import or re-loading of .env.
"""
import os

# Remove Postgres env vars so BacklogClient falls back to SharePoint proxies.
# Tests mock the SharePoint Graph API — they don't need a real database.
os.environ.pop("DATABASE_URL", None)
os.environ.pop("GENLAB_USE_POSTGRES", None)

# Also prevent load_dotenv from re-populating these if settings.py
# is re-loaded during test discovery.
os.environ["GENLAB_USE_POSTGRES"] = ""
