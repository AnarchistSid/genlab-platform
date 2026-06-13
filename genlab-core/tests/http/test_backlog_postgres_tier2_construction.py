"""Regression pin: Tier 2 stores must be constructed on the Postgres path.

Closes the gap that caused the 22-day "0 published / $2 burned" outage of
2026-05-21 through 2026-06-12.

Before fix #2 of the autonomy roadmap: BacklogClient.__init__ early-returned
on the Postgres-primary path (``GENLAB_USE_POSTGRES=true`` + ``DATABASE_URL``)
before constructing ``self._stories`` / ``self._blueprints`` / etc.
push_to_backlog's delegators (e.g. ``client.find_story_by_story_id``) all
called ``self._stories.find_story_by_story_id`` which raised AttributeError;
the call site's try/except swallowed the error and the pipeline silently
created 0 blueprints despite stories + render + QC all succeeding.

After fix: ``_construct_tier2_stores`` is invoked on both init paths.

This test pins the architectural invariant — IF this test passes, no
delegator can regress to silent-AttributeError behavior on the Postgres path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def postgres_client():
    """Build a BacklogClient on the Postgres init path with a mocked backend."""
    fake_pg = MagicMock(name="PostgresBackend")
    fake_proxy_cls = MagicMock(name="PostgresTableProxy")
    fake_proxy_cls.side_effect = lambda *a, **k: MagicMock(
        name=f"proxy({a[1] if len(a) > 1 else '?'})"
    )

    with (
        patch.dict(
            "os.environ",
            {
                "GENLAB_USE_POSTGRES": "true",
                "DATABASE_URL": "postgresql://u:p@h/db",
            },
            clear=False,
        ),
        patch(
            "genlab_core.storage.postgres.PostgresBackend",
            return_value=fake_pg,
        ),
        patch(
            "genlab_core.storage.postgres.PostgresTableProxy",
            fake_proxy_cls,
        ),
    ):
        from genlab_core.http.backlog_client import BacklogClient

        client = BacklogClient()
        yield client


class TestPostgresPathHasTier2Stores:
    """Every Tier 2 store the delegators reference must exist."""

    def test_stores_attribute_present(self, postgres_client):
        # The delegators at lines 679, 688, 699, 703 of backlog_client.py
        # call self._stories.*. AttributeError here = the outage.
        assert hasattr(postgres_client, "_stories")
        assert postgres_client._stories is not None

    def test_blueprints_store_present(self, postgres_client):
        # Delegators at lines 714, 725, 737, 749, 760 use self._blueprints.*
        assert hasattr(postgres_client, "_blueprints")
        assert postgres_client._blueprints is not None

    def test_analytics_store_present(self, postgres_client):
        assert hasattr(postgres_client, "_analytics")
        assert postgres_client._analytics is not None

    def test_engagement_store_present(self, postgres_client):
        assert hasattr(postgres_client, "_engagement")
        assert postgres_client._engagement is not None

    def test_ab_tests_store_present(self, postgres_client):
        assert hasattr(postgres_client, "_ab_tests")
        assert postgres_client._ab_tests is not None

    def test_sources_store_present(self, postgres_client):
        assert hasattr(postgres_client, "_sources")
        assert postgres_client._sources is not None

    def test_templates_store_present(self, postgres_client):
        assert hasattr(postgres_client, "_templates")
        assert postgres_client._templates is not None

    def test_assets_store_present(self, postgres_client):
        assert hasattr(postgres_client, "_assets")
        assert postgres_client._assets is not None


class TestDelegatorsCallable:
    """The host-class delegators must not raise AttributeError.

    Each test calls a delegator and confirms it gets past the
    `self._stories.*` / `self._blueprints.*` chain. We don't assert
    on the return value — that's a Postgres integration concern.
    A successful call (even one that raises a non-AttributeError) means
    the delegator dispatch is intact.
    """

    def test_find_story_by_story_id_dispatches(self, postgres_client):
        # Patch the actual store method to a sentinel so we know the
        # delegator reached it instead of crashing on missing attr.
        postgres_client._stories.find_story_by_story_id = MagicMock(return_value=None)
        result = postgres_client.find_story_by_story_id("some_id")
        assert result is None
        postgres_client._stories.find_story_by_story_id.assert_called_once()

    def test_create_story_dispatches(self, postgres_client):
        postgres_client._stories.create_story = MagicMock(return_value="story-uuid")
        result = postgres_client.create_story({"story_id": "x", "title": "t", "url": "u"})
        assert result == "story-uuid"
        postgres_client._stories.create_story.assert_called_once()

    def test_batch_create_stories_dispatches(self, postgres_client):
        postgres_client._stories.batch_create_stories = MagicMock(return_value=[])
        result = postgres_client.batch_create_stories([])
        assert result == []
        postgres_client._stories.batch_create_stories.assert_called_once()

    def test_create_blueprint_dispatches(self, postgres_client):
        postgres_client._blueprints.create_blueprint = MagicMock(return_value="bp-1")
        result = postgres_client.create_blueprint({"title": "t", "niche_id": "ai_creators"})
        assert result == "bp-1"
        postgres_client._blueprints.create_blueprint.assert_called_once()
