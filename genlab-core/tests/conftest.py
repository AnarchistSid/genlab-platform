"""Test configuration — ensure tests use SharePoint mocks, not live Postgres.

settings.py calls load_dotenv() at import time, which loads .env values into
os.environ. Before the GENLAB_SUPPRESS_DOTENV guard (2026-06-19) this ran
BEFORE conftest could pop the Postgres vars, so the FIRST test that
imported anything from ``genlab_core`` re-populated POSTGRES_PASSWORD
from .env and silently flipped storage-test skipif predicates True→False
mid-suite — causing tests to fail against the operator's prod DB whenever
test ordering changed (e.g. starlette 1.x upgrade). See
``docs/U-24-starlette-1x-investigation.md`` for the full bug.

Two layers of defense:
  1. ``GENLAB_SUPPRESS_DOTENV=1`` opt-out in settings.py — set here at
     the top of conftest BEFORE any genlab_core import.
  2. Autouse fixture (below) that pops POSTGRES_PASSWORD after every
     test — belt-and-suspenders for paths that bypass settings.py and
     set the env var directly via load_dotenv elsewhere or unaudited
     monkeypatch fixtures.
"""

import os
import sys

import pytest

# Suppress load_dotenv() in settings.py BEFORE any genlab_core import.
os.environ["GENLAB_SUPPRESS_DOTENV"] = "1"

# Remove Postgres env vars so BacklogClient falls back to SharePoint proxies.
# Tests mock the SharePoint Graph API — they don't need a real database.
os.environ.pop("DATABASE_URL", None)
os.environ.pop("GENLAB_USE_POSTGRES", None)
os.environ.pop("POSTGRES_PASSWORD", None)

# Also prevent load_dotenv from re-populating these if settings.py
# is re-loaded during test discovery.
os.environ["GENLAB_USE_POSTGRES"] = ""

# 2026-07-21: disable transformation-stage output validation in tests.
# Tests simulate transformation success with 2KB fake-MP4 fixture
# files that ffprobe correctly rejects — validation would flip real
# stage successes to skipped and break pre-validation test assertions.
# Prod default is ON (validation catches real degraded intermediate
# files that would otherwise cause motion_compositor -22 EINVAL).
os.environ["GENLAB_TRANSFORM_STAGE_VALIDATION"] = "0"

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


@pytest.fixture(autouse=True)
def _prevent_postgres_password_leak():
    """U-24 belt-and-suspenders: wipe POSTGRES_PASSWORD around every test.

    The dotenv sentinel above stops settings.py from re-loading .env, but
    some test paths still indirectly trigger load_dotenv in other modules
    (e.g. ``scripts/collect_audience_metrics.py`` line 58) OR set
    POSTGRES_PASSWORD via unaudited monkeypatch fixtures whose cleanup
    fires late. Even pytest collection-time imports can re-populate it
    via deep import chains.

    Wipe BEFORE setup (skipif predicates evaluate against a clean env)
    AND AFTER teardown (next test sees clean env). The pop-before pop
    pair makes the skipif evaluation deterministic regardless of test
    ordering or collection-time side effects.

    Tests that genuinely need POSTGRES_PASSWORD (the integration tests
    in tests/storage/) should set it via ``monkeypatch.setenv`` in their
    own fixtures — monkeypatch's setup runs AFTER this autouse fixture's
    setup phase, so the value is present during test execution and
    cleaned up before this fixture's teardown.
    """
    os.environ.pop("POSTGRES_PASSWORD", None)
    yield
    os.environ.pop("POSTGRES_PASSWORD", None)
