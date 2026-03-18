"""Temp CDN uploader for Instagram publishing.

Instagram's API requires a public HTTPS URL for video uploads.
This module uploads local files to litterbox.catbox.moe (primary)
or tmpfiles.org (fallback) and returns the public URL.

Files auto-expire (24h default).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_LITTERBOX_API = "https://litterbox.catbox.moe/resources/internals/api.php"
_TMPFILES_API = "https://tmpfiles.org/api/v1/upload"
_UPLOAD_TIMEOUT = 600


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
    """Upload a local file to a temp CDN and return a public HTTPS URL.

    Tries litterbox.catbox.moe first, falls back to tmpfiles.org.
    Returns None if both fail.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        logger.error("CDN upload: file not found: %s", file_path)
        return None

    size_mb = file_path.stat().st_size / (1024 * 1024)
    logger.info("CDN upload: %s (%.1f MB, expiry=%s)", file_path.name, size_mb, expiry)

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
                    logger.info("CDN upload OK: %s → %s", file_path.name, url)
                    return url
        except requests.Timeout:
            logger.warning("CDN upload: attempt %d/%d timed out", attempt + 1, max_attempts)
        except requests.RequestException as exc:
            logger.warning("CDN upload: attempt %d/%d failed: %s", attempt + 1, max_attempts, exc)

        if attempt < max_attempts - 1:
            delay = min(2 * (2 ** attempt), 30)
            time.sleep(delay)

    logger.warning("Litterbox unreachable, trying tmpfiles.org...")
    return _upload_to_tmpfiles(file_path)
