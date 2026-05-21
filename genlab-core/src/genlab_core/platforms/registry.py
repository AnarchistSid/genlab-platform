"""Lazy platform client registry.

Platform modules are imported on first use, not at package load time.
This avoids pulling in tweepy, google-api-python-client, etc. when
only one platform is needed.
"""

from __future__ import annotations

import importlib

# Maps platform_id -> "module.path:ClassName"
_REGISTRY: dict[str, str] = {
    "instagram": "genlab_core.platforms.instagram:InstagramClient",
    "youtube": "genlab_core.platforms.youtube:YouTubeClient",
    "x_twitter": "genlab_core.platforms.x_twitter:XTwitterClient",
    "facebook": "genlab_core.platforms.facebook:FacebookClient",
    "threads": "genlab_core.platforms.threads:ThreadsClient",
    "tiktok": "genlab_core.platforms.tiktok:TikTokClient",
}

# Cache instantiated classes (not instances) to avoid repeated imports
_CLASS_CACHE: dict[str, type] = {}


def get_client(platform_id: str, **kwargs):
    """Lazy-load a platform client module and return an instance.

    Args:
        platform_id: One of the registered platform IDs.
        **kwargs: Passed to the client constructor (overrides env-var defaults).

    Returns:
        An instance implementing at minimum the Publisher protocol.

    Raises:
        ValueError: If platform_id is not registered.
        ImportError: If the platform module cannot be imported.
    """
    if platform_id not in _REGISTRY:
        raise ValueError(f"Unknown platform: {platform_id}")

    if platform_id not in _CLASS_CACHE:
        entry = _REGISTRY[platform_id]
        module_path, class_name = entry.rsplit(":", 1)
        module = importlib.import_module(module_path)
        _CLASS_CACHE[platform_id] = getattr(module, class_name)

    return _CLASS_CACHE[platform_id](**kwargs)


def list_platforms() -> list[str]:
    """Return all registered platform IDs."""
    return list(_REGISTRY.keys())
