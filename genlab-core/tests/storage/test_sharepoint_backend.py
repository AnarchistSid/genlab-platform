"""Tests for SharePointBackend wrapping GraphTableProxy."""
from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.storage.protocol import StorageBackend
from genlab_core.storage.sharepoint import SharePointBackend


def test_implements_protocol():
    """SharePointBackend satisfies the StorageBackend protocol."""
    backend = SharePointBackend.__new__(SharePointBackend)
    # Give it the _proxies attribute so isinstance doesn't fail on structural check
    backend._proxies = {}
    assert isinstance(backend, StorageBackend)


def test_create_delegates_to_proxy():
    mock_proxy = MagicMock()
    mock_proxy.create.return_value = {"id": "rec_123", "fields": {"title": "test"}}
    backend = SharePointBackend({"blueprints": mock_proxy})
    result = backend.create("blueprints", {"title": "test"})
    assert result == "rec_123"
    mock_proxy.create.assert_called_once()


def test_get_delegates_to_proxy():
    mock_proxy = MagicMock()
    mock_proxy.get.return_value = {"id": "rec_123", "fields": {"title": "test"}}
    backend = SharePointBackend({"blueprints": mock_proxy})
    result = backend.get("blueprints", "rec_123")
    assert result == {"id": "rec_123", "fields": {"title": "test"}}
    mock_proxy.get.assert_called_once_with("rec_123")


def test_get_returns_none_on_error():
    mock_proxy = MagicMock()
    mock_proxy.get.side_effect = RuntimeError("not found")
    backend = SharePointBackend({"blueprints": mock_proxy})
    result = backend.get("blueprints", "nonexistent")
    assert result is None


def test_find_delegates_with_formula():
    mock_proxy = MagicMock()
    mock_proxy.all.return_value = [
        {"id": "rec_1", "fields": {"status": "DRAFTED"}},
    ]
    backend = SharePointBackend({"blueprints": mock_proxy})
    results = backend.find("blueprints", formula="{status}='DRAFTED'")
    assert len(results) == 1
    mock_proxy.all.assert_called_once()


def test_find_with_niche_id_appends_filter():
    mock_proxy = MagicMock()
    mock_proxy.all.return_value = []
    backend = SharePointBackend({"blueprints": mock_proxy})
    backend.find("blueprints", formula="{status}='DRAFTED'", niche_id="gaming")
    call_kwargs = mock_proxy.all.call_args
    # The formula should include niche_id filter
    formula_arg = call_kwargs.kwargs.get("formula") or call_kwargs[1].get("formula", "")
    assert "gaming" in formula_arg


def test_update_delegates_to_proxy():
    mock_proxy = MagicMock()
    backend = SharePointBackend({"blueprints": mock_proxy})
    backend.update("blueprints", "rec_123", {"status": "PUBLISHED"})
    mock_proxy.update.assert_called_once_with("rec_123", {"status": "PUBLISHED"})


def test_delete_delegates_to_proxy():
    mock_proxy = MagicMock()
    backend = SharePointBackend({"blueprints": mock_proxy})
    backend.delete("blueprints", "rec_123")
    mock_proxy.delete.assert_called_once_with("rec_123")


def test_batch_create_delegates():
    mock_proxy = MagicMock()
    mock_proxy.batch_create.return_value = [
        {"id": "rec_1", "fields": {}},
        {"id": "rec_2", "fields": {}},
    ]
    backend = SharePointBackend({"blueprints": mock_proxy})
    result = backend.batch_create("blueprints", [{"title": "a"}, {"title": "b"}])
    assert result == ["rec_1", "rec_2"]


def test_unknown_table_raises():
    backend = SharePointBackend({"blueprints": MagicMock()})
    try:
        backend.create("nonexistent_table", {"foo": "bar"})
        assert False, "Should have raised KeyError"
    except KeyError as e:
        assert "nonexistent_table" in str(e)
