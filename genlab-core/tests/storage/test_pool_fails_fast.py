"""A missing DSN is a fatal misconfiguration, not something to wait on.

PostgresBackend used to synthesise `postgresql://genlab:@localhost:5432/genlab`
from its own defaults whenever no dsn was passed, so an UNCONFIGURED backend was
indistinguishable from one deliberately pointed at a local database.
`ConnectionPool(open=True)` then blocked trying to reach a Postgres that was not
there.

In production that turns a fatal misconfiguration into a hang — a pipeline with
no DATABASE_URL waiting on a connection that cannot exist. In the suite it cost
108+ timeouts and roughly 3.8 hours per side of the baseline comparison, which
is why the first plan was to mark a hundred-odd tests individually. Fixing the
cause removed the need for the markers entirely.
"""

from __future__ import annotations

import pytest
from genlab_core.exceptions import ConfigError
from genlab_core.storage.postgres import PostgresBackend


def test_no_dsn_anywhere_raises_immediately(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigError) as exc:
        PostgresBackend()
    # The message must say what to DO. "connection refused" sends the reader to
    # the database; this is not a database problem.
    assert "DATABASE_URL" in str(exc.value)


def test_it_does_not_block_while_refusing(monkeypatch):
    """The whole point: a second, not a five-minute timeout."""
    import time

    monkeypatch.delenv("DATABASE_URL", raising=False)
    t0 = time.time()
    with pytest.raises(ConfigError):
        PostgresBackend()
    assert time.time() - t0 < 1.0


def test_an_explicit_dsn_is_honoured_untouched(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert PostgresBackend(dsn="postgresql://a:b@h:5432/d")._dsn == "postgresql://a:b@h:5432/d"


def test_database_url_is_honoured(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@dbhost/genlab")
    assert PostgresBackend()._dsn == "postgresql://u:p@dbhost/genlab"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "db.internal"},
        {"user": "someone_else"},
        {"password": "hunter2"},
        {"database": "other"},
    ],
)
def test_explicit_connection_settings_still_work(monkeypatch, kwargs):
    """Deliberately pointing at localhost with real credentials is a
    CONFIGURATION, and must not be swept up by the refusal."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    be = PostgresBackend(**kwargs)
    assert be._dsn.startswith("postgresql://")


def test_a_bare_localhost_default_is_NOT_treated_as_configuration(monkeypatch):
    """The exact shape that hid the problem: every default left as-is looks like
    a valid local DSN and is not one."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigError):
        PostgresBackend(host="localhost", port=5432, database="genlab", user="genlab")
