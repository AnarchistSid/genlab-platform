"""Connector-based source fetchers for creator ingestion."""

from .registry import fetch_via_connector

__all__ = ["fetch_via_connector"]
