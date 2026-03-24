"""CDN upload adapter — wraps platforms.cdn_upload for class-based API.

Provides LocalCDNUpload class for backward compat with code that uses
the upload()/cleanup() pattern. Delegates to platforms.cdn_upload.upload_to_cdn().
"""
from __future__ import annotations

import logging
from pathlib import Path

from genlab_core.platforms.cdn_upload import upload_to_cdn

logger = logging.getLogger(__name__)


class LocalCDNUpload:
    """Class-based CDN upload adapter."""

    def __init__(self, tunnel_url: str | None = None, serve_dir: Path | None = None):
        self._tunnel_url = tunnel_url
        self._serve_dir = serve_dir

    def upload(self, local_path: str) -> str:
        """Upload via the canonical upload_to_cdn function."""
        url = upload_to_cdn(local_path)
        if not url:
            raise RuntimeError(f"CDN upload failed for {local_path}")
        return url

    def cleanup(self, cdn_url_or_path: str) -> None:
        """Remove a CDN copy (best-effort)."""
        logger.debug("[CDN] cleanup requested for %s", cdn_url_or_path)
