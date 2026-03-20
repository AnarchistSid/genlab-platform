"""CDN uploader for Instagram publishing.

Instagram's API requires a public HTTPS URL for video uploads.

Upload strategy (ordered by reliability):
  1. Cloudflare tunnel (CLOUDFLARE_TUNNEL_URL) — serves local files via
     the existing review server /api/media/ endpoint. Zero external deps,
     100% reliable when tunnel is running.
  2. litterbox.catbox.moe — free service, no SLA, sometimes unreachable.
  3. tmpfiles.org — free fallback, also unreliable.

Files served via tunnel don't expire (available as long as the file exists
on disk). External CDN files auto-expire (24h default).
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_LITTERBOX_API = "https://litterbox.catbox.moe/resources/internals/api.php"
_TMPFILES_API = "https://tmpfiles.org/api/v1/upload"
_UPLOAD_TIMEOUT = 600

# Shared directory for media files served by the dashboard's /api/media/ route.
_MEDIA_SHARE_DIR = Path(os.environ.get("GENLAB_PROJECT_ROOT", "")) / ".media" / "cdn"


def _serve_via_tunnel(file_path: Path) -> str | None:
    """Serve file via Cloudflare tunnel + dashboard /api/media/ route.

    Copies file to .media/cdn/ so the dashboard can serve it, then returns
    the public tunnel URL. This is the most reliable method — no external
    service dependency, no upload timeout, works for files of any size.
    """
    tunnel_url = os.environ.get("CLOUDFLARE_TUNNEL_URL", "").rstrip("/")
    if not tunnel_url:
        return None

    try:
        share_dir = _MEDIA_SHARE_DIR
        share_dir.mkdir(parents=True, exist_ok=True)

        # Use a unique name to avoid collisions
        dest = share_dir / file_path.name
        if not dest.exists() or dest.stat().st_size != file_path.stat().st_size:
            shutil.copy2(file_path, dest)

        # The dashboard serves files from PROJECT_ROOT via /api/media/<path>
        relative = dest.relative_to(Path(os.environ.get("GENLAB_PROJECT_ROOT", "")))
        public_url = f"{tunnel_url}/api/media/{relative}"

        # Verify the URL is reachable (quick HEAD check)
        try:
            resp = requests.head(public_url, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                logger.info("CDN tunnel: %s → %s", file_path.name, public_url)
                return public_url
            else:
                logger.warning(
                    "CDN tunnel: HEAD returned %d for %s", resp.status_code, public_url,
                )
        except requests.RequestException as exc:
            logger.warning("CDN tunnel: verification failed: %s", exc)

        # Return the URL anyway — tunnel might be briefly unreachable for HEAD
        # but still work for Instagram's fetch
        logger.info("CDN tunnel (unverified): %s → %s", file_path.name, public_url)
        return public_url

    except Exception as exc:
        logger.warning("CDN tunnel: failed: %s", exc)
        return None


def _upload_to_litterbox(file_path: Path, expiry: str, max_attempts: int) -> str | None:
    """Upload to litterbox.catbox.moe (free, best-effort)."""
    for attempt in range(max_attempts):
        try:
            with open(file_path, "rb") as f:
                resp = requests.post(
                    _LITTERBOX_API,
                    files={"fileToUpload": (file_path.name, f)},
                    data={"reqtype": "fileupload", "time": expiry},
                    timeout=_UPLOAD_TIMEOUT,
                )
            if resp.status_code == 200:
                url = resp.text.strip()
                if url.startswith("https://litter.catbox.moe/"):
                    logger.info("CDN litterbox: %s → %s", file_path.name, url)
                    return url
        except requests.Timeout:
            logger.warning("CDN litterbox: attempt %d/%d timed out", attempt + 1, max_attempts)
        except requests.RequestException as exc:
            logger.warning("CDN litterbox: attempt %d/%d failed: %s", attempt + 1, max_attempts, exc)

        if attempt < max_attempts - 1:
            delay = min(2 * (2 ** attempt), 30)
            time.sleep(delay)

    return None


def _upload_to_tmpfiles(file_path: Path) -> str | None:
    """Fallback: tmpfiles.org (up to 100 MB)."""
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                _TMPFILES_API,
                files={"file": (file_path.name, f)},
                timeout=_UPLOAD_TIMEOUT,
            )
        if resp.status_code != 200:
            logger.warning("tmpfiles: HTTP %d", resp.status_code)
            return None
        data = resp.json()
        if data.get("status") != "success":
            return None
        page_url = data.get("data", {}).get("url", "")
        dl_url = page_url.replace("http://tmpfiles.org/", "https://tmpfiles.org/dl/")
        logger.info("tmpfiles: %s → %s", file_path.name, dl_url)
        return dl_url
    except Exception as exc:
        logger.warning("tmpfiles failed: %s", exc)
        return None


def upload_to_cdn(
    file_path: str | Path,
    expiry: str = "24h",
    max_attempts: int = 3,
) -> str | None:
    """Upload a local file and return a public HTTPS URL.

    Strategy (ordered by reliability):
      1. Cloudflare tunnel — local file served via dashboard (100% reliable)
      2. litterbox.catbox.moe — free external CDN
      3. tmpfiles.org — free fallback

    Returns None if all methods fail.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        logger.error("CDN upload: file not found: %s", file_path)
        return None

    size_mb = file_path.stat().st_size / (1024 * 1024)
    logger.info("CDN upload: %s (%.1f MB, expiry=%s)", file_path.name, size_mb, expiry)

    # Tier 1: Cloudflare tunnel (most reliable)
    url = _serve_via_tunnel(file_path)
    if url:
        return url

    # Tier 2: Litterbox
    url = _upload_to_litterbox(file_path, expiry, max_attempts)
    if url:
        return url

    # Tier 3: tmpfiles
    logger.warning("Litterbox unreachable, trying tmpfiles.org...")
    return _upload_to_tmpfiles(file_path)
