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

# ── Affiliate-network test tags ─────────────────────────────────────
# ``genlab_core.monetization.network_registry`` builds a singleton
# ``ADAPTERS`` dict at module import — each adapter captures its
# tag/publisher-id env var via ``field(default_factory=...)`` AT
# INSTANTIATION. So the env vars MUST be set before any test imports
# the module, not just before ``test_network_registry.py`` itself
# runs. The previous arrangement (env vars set at the top of
# ``test_network_registry.py``) raced with other test files that
# imported the registry first during collection (e.g.
# ``test_affiliate_matcher.py``, ``test_geo_resolver_*.py``), leaving
# the singletons with empty tags and breaking the assertions. Setting
# them here in conftest — which Python imports before any test file —
# closes the race deterministically.
os.environ.setdefault("AMAZON_US_AFFILIATE_TAG", "test-tag-20")
os.environ.setdefault("AMAZON_IN_AFFILIATE_TAG", "test-tag-21")
os.environ.setdefault("CUELINKS_PUBLISHER_ID", "000000")


# ── Detoxify/PyTorch segfault workaround (R-64) ──────────────────────
# PyTorch + Detoxify model loading in a threaded pytest context segfaults
# (libtorch_cpu -> libomp, layer_norm) on Python 3.13 AND 3.14 — and CI
# runs 3.13, so the guard MUST cover it. Force single-threaded OpenMP to
# avoid the crash, and skip the one test that loads the real model.
os.environ.setdefault("OMP_NUM_THREADS", "1")
_SKIP_DETOXIFY = sys.version_info >= (3, 13)


def pytest_collection_modifyitems(config, items):
    """Skip Detoxify-dependent tests on Python 3.13+ to avoid segfaults."""
    if not _SKIP_DETOXIFY:
        return
    skip_marker = pytest.mark.skip(reason="Detoxify segfaults on Python 3.13+ (PyTorch threading)")
    # Only skip the full integration test that loads the real Detoxify model.
    # test_toxicity_extended and test_engagement_engine mock the model.
    detoxify_modules = {
        "test_reply_dispatch_live",
    }
    for item in items:
        module_name = item.module.__name__.split(".")[-1] if item.module else ""
        if module_name in detoxify_modules:
            item.add_marker(skip_marker)
