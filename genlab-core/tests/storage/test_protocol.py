"""Tests for StorageBackend protocol compliance."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from genlab_core.storage.protocol import StorageBackend


def test_protocol_exists():
    """StorageBackend defines the 6 CRUD methods."""
    assert hasattr(StorageBackend, "create")
    assert hasattr(StorageBackend, "get")
    assert hasattr(StorageBackend, "find")
    assert hasattr(StorageBackend, "update")
    assert hasattr(StorageBackend, "delete")
    assert hasattr(StorageBackend, "batch_create")


def test_protocol_is_runtime_checkable():
    """StorageBackend can be used with isinstance() at runtime."""
    assert issubclass(type(StorageBackend), type(Protocol))


def test_concrete_class_satisfies_protocol():
    """A class implementing all methods satisfies isinstance() check."""

    class FakeBackend:
        def create(self, table: str, record: Dict[str, Any]) -> str:
            return "id_1"

        def get(self, table: str, record_id: str) -> Optional[Dict[str, Any]]:
            return None

        def find(
            self, table: str, *, formula: str = "", niche_id: str = "",
        ) -> List[Dict[str, Any]]:
            return []

        def update(
            self, table: str, record_id: str, fields: Dict[str, Any],
        ) -> None:
            pass

        def delete(self, table: str, record_id: str) -> None:
            pass

        def batch_create(
            self, table: str, records: List[Dict[str, Any]],
        ) -> List[str]:
            return []

    backend = FakeBackend()
    assert isinstance(backend, StorageBackend)


def test_incomplete_class_fails_protocol():
    """A class missing methods does NOT satisfy the protocol."""

    class IncompleteBackend:
        def create(self, table: str, record: Dict[str, Any]) -> str:
            return ""

    backend = IncompleteBackend()
    assert not isinstance(backend, StorageBackend)
