"""Test configuration — ensure tests use SharePoint mocks, not live Postgres.

settings.py calls load_dotenv() at import time, which loads .env values into
os.environ BEFORE conftest runs. We must pop the Postgres vars here AND
individual test fixtures should use patch.dict("os.environ", ...) for full
isolation against re-import or re-loading of .env.
"""
import os
import sys

import pytest

# Remove Postgres env vars so BacklogClient falls back to SharePoint proxies.
# Tests mock the SharePoint Graph API — they don't need a real database.
os.environ.pop("DATABASE_URL", None)
os.environ.pop("GENLAB_USE_POSTGRES", None)

# Also prevent load_dotenv from re-populating these if settings.py
# is re-loaded during test discovery.
os.environ["GENLAB_USE_POSTGRES"] = ""


# ── Python 3.14 Detoxify/PyTorch segfault workaround ─────────────────
# PyTorch + Detoxify model loading in threaded pytest context causes
# segfaults on Python 3.14. Skip tests that load the model directly.
_SKIP_DETOXIFY = sys.version_info >= (3, 14)

def pytest_collection_modifyitems(config, items):
    """Skip Detoxify-dependent tests on Python 3.14+ to avoid segfaults."""
    if not _SKIP_DETOXIFY:
        return
    skip_marker = pytest.mark.skip(reason="Detoxify segfaults on Python 3.14+ (PyTorch threading)")
    detoxify_modules = {
        "test_engagement_engine",
        "test_toxicity_extended",
        "test_reply_dispatch_live",
    }
    for item in items:
        module_name = item.module.__name__.split(".")[-1] if item.module else ""
        if module_name in detoxify_modules:
            item.add_marker(skip_marker)
