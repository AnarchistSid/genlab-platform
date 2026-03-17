"""Lightweight CDN upload helper for Threads video publishing.

Threads requires a public HTTPS URL for video content (unlike TikTok which
accepts local file uploads). This helper symlinks the rendered video into a
local serve directory and returns the public URL via the Cloudflare tunnel.

The Cloudflare tunnel (CLOUDFLARE_TUNNEL_URL) is already running for the
review dashboard and proxies requests to the local server.

Usage:
    from genlab_core.publishing.cdn_upload import LocalCDNUpload
    uploader = LocalCDNUpload()
    url = uploader.upload("/tmp/rendered/video.mp4")
    # ... publish to Threads ...
    uploader.cleanup("/tmp/rendered/video.mp4")
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from genlab_core.settings import settings

logger = logging.getLogger(__name__)

# Default serve directory — resolved per-agent via AGENT_ROOT at init time


class LocalCDNUpload:
    """Resolve a local video file to a public URL via Cloudflare tunnel."""

    def __init__(
        self,
        tunnel_url: str | None = None,
        serve_dir: Path | None = None,
    ):
        self._tunnel_url = (
            tunnel_url
            or getattr(settings, "cloudflare_tunnel_url", None)
            or ""
        )
        self._serve_dir = serve_dir or (settings.get_project_root() / ".tmp" / "cdn")

        if not self._tunnel_url:
            raise ValueError(
                "CLOUDFLARE_TUNNEL_URL not set. "
                "Threads requires a public video URL via Cloudflare tunnel."
            )

    def upload(self, local_path: str) -> str:
        """Copy video to the CDN serve directory and return its public URL.

        Uses a UUID prefix to avoid filename collisions.
        """
        src = Path(local_path)
        if not src.exists():
            raise FileNotFoundError(f"Video file not found: {local_path}")

        self._serve_dir.mkdir(parents=True, exist_ok=True)

        # UUID prefix prevents collisions from same-named files
        cdn_filename = f"{uuid.uuid4().hex[:8]}_{src.name}"
        dest = self._serve_dir / cdn_filename

        # Copy rather than symlink — more reliable across filesystems
        shutil.copy2(str(src), str(dest))

        public_url = f"{self._tunnel_url.rstrip('/')}/cdn/{cdn_filename}"
        logger.info("[CDN] Uploaded %s → %s", src.name, public_url)
        return public_url

    def cleanup(self, cdn_url_or_path: str) -> None:
        """Remove a specific CDN copy after the platform confirms the post.

        Accepts either the public URL returned by upload() or the CDN filename.
        Only deletes the exact file — not all files matching the source name.
        """
        # Extract filename from URL or path
        cdn_filename = cdn_url_or_path.rsplit("/", 1)[-1]
        target = self._serve_dir / cdn_filename
        if target.exists():
            target.unlink()
            logger.debug("[CDN] Cleaned up %s", cdn_filename)
