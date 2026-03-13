import os
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
