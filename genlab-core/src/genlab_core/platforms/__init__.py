"""Unified platform client package.

Usage:
    from genlab_core.platforms import get_client, list_platforms
    client = get_client("instagram")
    result = client.publish(payload)
"""
from genlab_core.platforms.registry import get_client, list_platforms

__all__ = ["get_client", "list_platforms"]
