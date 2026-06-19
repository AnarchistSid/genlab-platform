"""Regression test: GENLAB_SUPPRESS_DOTENV honored by settings.py.

The U-24 test-isolation bug (2026-06-18) was that ``settings.py``
unconditionally called ``load_dotenv()`` at module-import time. The
FIRST test that imported anything from ``genlab_core`` would re-populate
POSTGRES_PASSWORD (and other prod credentials) from .env into
os.environ — flipping storage-test ``skipif(not POSTGRES_PASSWORD)``
predicates True→False mid-suite. Tests that should SKIP would then
RUN against the operator's prod DB and fail mysteriously.

The fix: settings.py honors ``GENLAB_SUPPRESS_DOTENV=1`` and skips
the dotenv load entirely. ``tests/conftest.py`` sets this BEFORE
any genlab_core import.

These pins guard against re-introduction of the unconditional load.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def test_settings_module_does_not_repopulate_pg_password_with_sentinel(tmp_path, monkeypatch):
    """When GENLAB_SUPPRESS_DOTENV=1 is set, settings.py must NOT call
    load_dotenv, so it does not leak .env values into os.environ."""
    # Simulate a .env that would set POSTGRES_PASSWORD if loaded
    fake_env = tmp_path / ".env"
    fake_env.write_text("POSTGRES_PASSWORD=secret-from-env\n")
    monkeypatch.setenv("AGENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GENLAB_SUPPRESS_DOTENV", "1")
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

    # Reimport settings.py so its module-top load_dotenv runs (or doesn't)
    if "genlab_core.settings" in sys.modules:
        del sys.modules["genlab_core.settings"]
    importlib.import_module("genlab_core.settings")

    # The pop above + sentinel set means POSTGRES_PASSWORD should stay unset
    assert os.environ.get("POSTGRES_PASSWORD") is None, (
        "GENLAB_SUPPRESS_DOTENV=1 should prevent settings.py from re-loading "
        ".env. POSTGRES_PASSWORD leaked back in — the dotenv guard is broken."
    )


def test_settings_module_does_load_dotenv_without_sentinel(tmp_path, monkeypatch):
    """Without the sentinel, settings.py loads .env as normal — prod path."""
    fake_env = tmp_path / ".env"
    fake_env.write_text("GENLAB_DOTENV_PROBE=loaded-from-env\n")
    monkeypatch.setenv("AGENT_ROOT", str(tmp_path))
    monkeypatch.delenv("GENLAB_SUPPRESS_DOTENV", raising=False)
    monkeypatch.delenv("GENLAB_DOTENV_PROBE", raising=False)

    if "genlab_core.settings" in sys.modules:
        del sys.modules["genlab_core.settings"]
    importlib.import_module("genlab_core.settings")

    assert os.environ.get("GENLAB_DOTENV_PROBE") == "loaded-from-env", (
        "settings.py must still load .env in prod (no sentinel set)"
    )


def test_settings_load_respects_existing_env(tmp_path, monkeypatch):
    """The default ``override=False`` means a pre-existing env var wins
    over .env contents. This is the contract operators rely on for
    SSH+env-var overrides."""
    fake_env = tmp_path / ".env"
    fake_env.write_text("GENLAB_DOTENV_PROBE=value-from-env\n")
    monkeypatch.setenv("AGENT_ROOT", str(tmp_path))
    monkeypatch.delenv("GENLAB_SUPPRESS_DOTENV", raising=False)
    monkeypatch.setenv("GENLAB_DOTENV_PROBE", "value-from-shell")

    if "genlab_core.settings" in sys.modules:
        del sys.modules["genlab_core.settings"]
    importlib.import_module("genlab_core.settings")

    assert os.environ.get("GENLAB_DOTENV_PROBE") == "value-from-shell", (
        "shell env var should win over .env (override=False contract)"
    )


def test_conftest_sets_sentinel_before_any_genlab_import():
    """Pin the contract: tests/conftest.py must set GENLAB_SUPPRESS_DOTENV
    so the U-24 storage-test isolation bug doesn't reappear."""
    conftest_path = Path(__file__).parent / "conftest.py"
    content = conftest_path.read_text()
    assert 'GENLAB_SUPPRESS_DOTENV' in content, (
        "tests/conftest.py must set GENLAB_SUPPRESS_DOTENV before any "
        "genlab_core import — see U-24 investigation doc"
    )
    # Pin the assignment-vs-read direction: conftest must SET it, not READ
    sentinel_set = any(
        line.strip().startswith('os.environ["GENLAB_SUPPRESS_DOTENV"]')
        or line.strip().startswith("os.environ['GENLAB_SUPPRESS_DOTENV']")
        for line in content.splitlines()
    )
    assert sentinel_set, (
        "tests/conftest.py must SET (not just reference) GENLAB_SUPPRESS_DOTENV"
    )
