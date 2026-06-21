import sys

import pytest


@pytest.fixture(autouse=True)
def use_stub_broker(monkeypatch):
    """Force StubBroker for all engagement tests.

    monkeypatch reverts the env var after each test — no global pollution.
    """
    monkeypatch.setenv("DRAMATIQ_TEST", "1")
    for key in list(sys.modules.keys()):
        if "genlab_core.engagement.tasks" in key:
            del sys.modules[key]
    yield


@pytest.fixture(autouse=True)
def _clear_persona_engine_cache():
    """2026-06-21: ``comment_processor._get_persona_engine`` caches
    PersonaEngine per niche at module level (perf fix). Tests that
    ``@patch("...PersonaEngine")`` and expect the substitution to take
    effect must start with an empty cache — otherwise a stale entry
    from a prior test (or earlier import-time warmup) wins and the
    test's mock is never used.

    Lives in the engagement-tests conftest so it covers every test
    file in this directory — added after a Python 3.12-only failure
    on PR #414 where ``test_reply_dispatch_live.py`` saw a stale
    cached engine because the only file-local fixture (in
    ``test_comment_processor.py``) didn't apply here.
    """
    from genlab_core.engagement import comment_processor as cp

    cp._persona_engine_cache.clear()
    yield
    cp._persona_engine_cache.clear()
