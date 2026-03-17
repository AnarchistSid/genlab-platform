"""Tests for the StorageBackend protocol and implementations."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from genlab_core.storage.protocol import StorageBackend
from genlab_core.storage.sharepoint import SharePointBackend
from genlab_core.storage.postgres import PostgresBackend


class TestProtocol:
    def test_sharepoint_backend_is_storage_backend(self):
        proxies = {"Stories": MagicMock()}
        backend = SharePointBackend(proxies)
        assert isinstance(backend, StorageBackend)

    def test_postgres_backend_is_storage_backend(self):
        backend = PostgresBackend(dsn=None)
        assert isinstance(backend, StorageBackend)


class TestSharePointBackend:
    def test_find_delegates_to_proxy_all(self):
        mock_proxy = MagicMock()
        mock_proxy.all.return_value = [{"id": "1", "fields": {"title": "A"}}]
        backend = SharePointBackend({"Stories": mock_proxy})

        result = backend.find("Stories", formula="{status}='INTAKE'", max_records=10)

        mock_proxy.all.assert_called_once()
        kwargs = mock_proxy.all.call_args[1]
        assert "INTAKE" in kwargs["formula"]
        assert kwargs["max_records"] == 10
        assert len(result) == 1

    def test_find_with_niche_id_injects_filter(self):
        mock_proxy = MagicMock()
        mock_proxy.all.return_value = []
        backend = SharePointBackend({"Stories": mock_proxy})

        backend.find("Stories", formula="{status}='INTAKE'", niche_id="gaming")

        kwargs = mock_proxy.all.call_args[1]
        assert "gaming" in kwargs["formula"]
        assert "INTAKE" in kwargs["formula"]

    def test_find_with_niche_id_only(self):
        mock_proxy = MagicMock()
        mock_proxy.all.return_value = []
        backend = SharePointBackend({"Stories": mock_proxy})

        backend.find("Stories", niche_id="sports")

        kwargs = mock_proxy.all.call_args[1]
        assert "sports" in kwargs["formula"]

    def test_get_delegates_to_proxy_get(self):
        mock_proxy = MagicMock()
        expected = {"id": "42", "fields": {"title": "B"}}
        mock_proxy.get.return_value = expected
        backend = SharePointBackend({"Stories": mock_proxy})

        result = backend.get("Stories", "42")

        mock_proxy.get.assert_called_once_with("42")
        assert result == expected

    def test_create_delegates_to_proxy_create(self):
        mock_proxy = MagicMock()
        mock_proxy.create.return_value = {"id": "new-1", "fields": {}}
        backend = SharePointBackend({"Stories": mock_proxy})

        result = backend.create("Stories", {"title": "Test"}, typecast=True)

        mock_proxy.create.assert_called_once_with({"title": "Test"}, typecast=True)
        assert result["id"] == "new-1"

    def test_update_delegates_to_proxy_update(self):
        mock_proxy = MagicMock()
        mock_proxy.update.return_value = {"id": "42", "fields": {"status": "DONE"}}
        backend = SharePointBackend({"Blueprints": mock_proxy})

        result = backend.update("Blueprints", "42", {"status": "DONE"}, typecast=True)

        mock_proxy.update.assert_called_once_with("42", {"status": "DONE"}, typecast=True)
        assert result["fields"]["status"] == "DONE"

    def test_delete_delegates_to_proxy_delete(self):
        mock_proxy = MagicMock()
        backend = SharePointBackend({"Stories": mock_proxy})

        backend.delete("Stories", "42")

        mock_proxy.delete.assert_called_once_with("42")

    def test_batch_create_delegates(self):
        mock_proxy = MagicMock()
        mock_proxy.batch_create.return_value = [{"id": "1"}, {"id": "2"}]
        backend = SharePointBackend({"Stories": mock_proxy})

        result = backend.batch_create("Stories", [{"title": "A"}, {"title": "B"}])

        mock_proxy.batch_create.assert_called_once()
        assert len(result) == 2

    def test_batch_update_delegates(self):
        mock_proxy = MagicMock()
        mock_proxy.batch_update.return_value = [{"id": "1"}]
        backend = SharePointBackend({"Blueprints": mock_proxy})

        records = [{"id": "1", "fields": {"status": "DONE"}}]
        backend.batch_update("Blueprints", records)

        mock_proxy.batch_update.assert_called_once_with(records)

    def test_upload_attachment_delegates(self):
        mock_proxy = MagicMock()
        mock_proxy.upload_attachment.return_value = {"filename": "a.png"}
        backend = SharePointBackend({"Blueprints": mock_proxy})

        result = backend.upload_attachment("Blueprints", "42", "cover", "/tmp/a.png")

        mock_proxy.upload_attachment.assert_called_once_with("42", "cover", "/tmp/a.png")
        assert result["filename"] == "a.png"

    def test_raises_for_unknown_table(self):
        backend = SharePointBackend({"Stories": MagicMock()})
        with pytest.raises(ValueError, match="no proxy configured"):
            backend.find("NonExistent")

    def test_find_no_formula_no_niche(self):
        mock_proxy = MagicMock()
        mock_proxy.all.return_value = []
        backend = SharePointBackend({"Stories": mock_proxy})

        backend.find("Stories")

        kwargs = mock_proxy.all.call_args[1]
        assert kwargs["formula"] is None


class TestPostgresBackend:
    def test_find_raises_not_implemented(self):
        backend = PostgresBackend()
        with pytest.raises(NotImplementedError, match="find"):
            backend.find("Stories")

    def test_get_raises_not_implemented(self):
        backend = PostgresBackend()
        with pytest.raises(NotImplementedError, match="get"):
            backend.get("Stories", "42")

    def test_create_raises_not_implemented(self):
        backend = PostgresBackend()
        with pytest.raises(NotImplementedError, match="create"):
            backend.create("Stories", {})

    def test_update_raises_not_implemented(self):
        backend = PostgresBackend()
        with pytest.raises(NotImplementedError, match="update"):
            backend.update("Stories", "42", {})

    def test_delete_raises_not_implemented(self):
        backend = PostgresBackend()
        with pytest.raises(NotImplementedError, match="delete"):
            backend.delete("Stories", "42")

    def test_batch_create_raises_not_implemented(self):
        backend = PostgresBackend()
        with pytest.raises(NotImplementedError, match="batch_create"):
            backend.batch_create("Stories", [])

    def test_batch_update_raises_not_implemented(self):
        backend = PostgresBackend()
        with pytest.raises(NotImplementedError, match="batch_update"):
            backend.batch_update("Stories", [])

    def test_upload_attachment_raises_not_implemented(self):
        backend = PostgresBackend()
        with pytest.raises(NotImplementedError, match="upload_attachment"):
            backend.upload_attachment("Stories", "42", "field", "/tmp/a.png")
