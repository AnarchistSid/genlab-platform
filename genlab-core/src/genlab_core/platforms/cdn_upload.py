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

PROVIDER_TUNNEL / PROVIDER_LITTERBOX / PROVIDER_TMPFILES are the canonical
provider IDs used by ``CdnUploadResult.provider`` and the
``exclude_providers`` parameter on :func:`upload_to_cdn_full`. The latter
exists so the IG / Threads publish path can retry against a *different*
external CDN after Meta returns container error 2207077 (which means
Meta's fetcher could not download from the URL we handed it — almost
always a transient issue on the specific provider, not on the video).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time as _time
from dataclasses import dataclass
from pathlib import Path

import requests

# Canonical provider IDs (used in CdnUploadResult.provider and exclude_providers).
PROVIDER_TUNNEL = "tunnel"
PROVIDER_LITTERBOX = "litterbox"
PROVIDER_TMPFILES = "tmpfiles"


@dataclass(frozen=True)
class CdnUploadResult:
    """Structured result of a successful CDN upload.

    Attributes:
        url: The public HTTPS URL Meta / Threads / other consumers should fetch.
        provider: One of :data:`PROVIDER_TUNNEL`, :data:`PROVIDER_LITTERBOX`,
            :data:`PROVIDER_TMPFILES`. Callers retrying on container error
            2207077 use this to populate ``exclude_providers`` on the next
            :func:`upload_to_cdn_full` call.
        size_mb: Size of the uploaded file in megabytes (decimal MB, file
            size / (1024 * 1024)) — handy for log lines without re-statting.
    """

    url: str
    provider: str
    size_mb: float


logger = logging.getLogger(__name__)

_LITTERBOX_API = "https://litterbox.catbox.moe/resources/internals/api.php"
_TMPFILES_API = "https://tmpfiles.org/api/v1/upload"
_UPLOAD_TIMEOUT = 600


def _get_media_share_dir() -> Path:
    """Compute CDN share directory at runtime (not import time)."""
    root = os.environ.get("GENLAB_PROJECT_ROOT", "")
    if not root:
        root = str(Path(__file__).parent.parent.parent.parent.parent)  # genlab-core -> GenLab
    return Path(root) / ".media" / "cdn"


class _CircuitBreaker:
    """Simple circuit breaker for CDN providers."""

    def __init__(self, name: str, threshold: int = 3, reset_seconds: int = 3600):
        self.name = name
        self._failures = 0
        self._threshold = threshold
        self._reset_seconds = reset_seconds
        self._opened_at: float = 0

    def can_attempt(self) -> bool:
        if self._failures < self._threshold:
            return True
        if _time.time() - self._opened_at > self._reset_seconds:
            self._failures = 0
            return True
        return False

    def record_success(self):
        self._failures = 0

    def record_failure(self):
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = _time.time()
            logger.warning(
                "[CDN] Circuit breaker OPEN for %s after %d failures", self.name, self._failures
            )


_litterbox_cb = _CircuitBreaker("litterbox")
_tmpfiles_cb = _CircuitBreaker("tmpfiles")


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
        share_dir = _get_media_share_dir()
        share_dir.mkdir(parents=True, exist_ok=True)

        # Use hash prefix to avoid collisions between niches
        import hashlib

        file_hash = hashlib.sha256(str(file_path).encode()).hexdigest()[:8]
        dest = share_dir / f"{file_hash}_{file_path.name}"
        if not dest.exists() or dest.stat().st_size != file_path.stat().st_size:
            shutil.copy2(file_path, dest)

        # The dashboard serves files from PROJECT_ROOT via /api/media/<path>
        project_root = os.environ.get("GENLAB_PROJECT_ROOT", "")
        if not project_root:
            logger.warning("CDN tunnel: GENLAB_PROJECT_ROOT not set — cannot compute relative path")
            return None
        relative = dest.relative_to(Path(project_root))
        public_url = f"{tunnel_url}/api/media/{relative}"

        # Verify the URL is reachable (quick HEAD check)
        try:
            resp = requests.head(public_url, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                logger.info("CDN tunnel: %s → %s", file_path.name, public_url)
                return public_url
            else:
                logger.warning(
                    "CDN tunnel: HEAD returned %d for %s",
                    resp.status_code,
                    public_url,
                )
        except requests.RequestException as exc:
            logger.warning("CDN tunnel: verification failed: %s", exc)

        # Tunnel verification failed — fall through to external CDN so
        # Instagram/Threads can actually fetch the video.  A 530 here means
        # Meta's servers will also get a 530 → error 2207077.
        logger.warning(
            "CDN tunnel (unverified): %s — falling through to external CDN", file_path.name
        )
        return None

    except Exception as exc:
        logger.warning("CDN tunnel: failed: %s", exc)
        return None


def _upload_to_litterbox(file_path: Path, expiry: str, max_attempts: int) -> str | None:
    """Upload to litterbox.catbox.moe (free, best-effort)."""
    if not _litterbox_cb.can_attempt():
        # 2026-07-21: elevated INFO → WARNING. When breaker is open, this
        # is a real degradation signal (only 1 remaining CDN provider
        # left in the cascade for IG/Threads). Publisher error messages
        # reference "exhausted providers" — operator needs to see WHY
        # a provider was skipped, not just that it was.
        logger.warning(
            "[CDN] Circuit breaker OPEN for litterbox — skipping. Cascade "
            "falls to tmpfiles only. Breaker retries hourly."
        )
        return None

    file_size = file_path.stat().st_size
    if file_size > 200 * 1024 * 1024:  # 200MB litterbox limit
        logger.info(
            "[CDN] File too large for litterbox (%d MB), skipping", file_size // (1024 * 1024)
        )
        return None

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
                    _litterbox_cb.record_success()
                    return url
        except requests.Timeout:
            logger.warning("CDN litterbox: attempt %d/%d timed out", attempt + 1, max_attempts)
            _litterbox_cb.record_failure()
        except requests.RequestException as exc:
            logger.warning(
                "CDN litterbox: attempt %d/%d failed: %s", attempt + 1, max_attempts, exc
            )
            _litterbox_cb.record_failure()

        if attempt < max_attempts - 1:
            delay = min(2 * (2**attempt), 30)
            _time.sleep(delay)

    return None


_TMPFILES_DL_URL_RE = re.compile(
    r'href="(https://tmpfiles\.org/dl/\d+\.[0-9a-f]+/[^"]+)"'
)


def _resolve_tmpfiles_direct_url(page_url: str) -> str | None:
    """Resolve the direct video URL from tmpfiles.org's wrapper page.

    tmpfiles.org changed their URL scheme in 2026-07: the old
    ``/dl/{token}/{filename}`` direct URL now 302s back to the HTML
    wrapper. The real signed direct URL lives inside the wrapper HTML
    as ``href="https://tmpfiles.org/dl/{TS}.{HASH}/{token}/{filename}"``.

    Args:
        page_url: The wrapper URL returned by tmpfiles' upload API
            (either http:// or https://). Fetched and parsed here.

    Returns:
        The signed direct URL string on success, ``None`` if the wrapper
        doesn't contain a recognisable ``/dl/`` href (site changed
        format again, upload expired, or network hiccup).
    """
    if not page_url:
        return None
    # Normalise to https and drop any accidental /dl/ prefix left over
    # from an earlier version of this function or a caller who already
    # tried the naive replace.
    https_page = page_url.replace("http://tmpfiles.org/", "https://tmpfiles.org/").replace(
        "https://tmpfiles.org/dl/", "https://tmpfiles.org/"
    )
    try:
        resp = requests.get(https_page, timeout=_UPLOAD_TIMEOUT)
    except Exception as exc:
        logger.warning("[CDN] tmpfiles wrapper fetch failed: %s", exc)
        return None
    if resp.status_code != 200:
        logger.warning("[CDN] tmpfiles wrapper returned HTTP %d", resp.status_code)
        return None
    match = _TMPFILES_DL_URL_RE.search(resp.text)
    if not match:
        logger.warning(
            "[CDN] tmpfiles wrapper HTML missing /dl/{ts}.{hash}/... href — "
            "the site may have changed format again"
        )
        return None
    return match.group(1)


def _upload_to_tmpfiles(file_path: Path) -> str | None:
    """Fallback: tmpfiles.org (up to 100 MB)."""
    if not _tmpfiles_cb.can_attempt():
        # 2026-07-21: elevated INFO → WARNING (see litterbox site above).
        logger.warning(
            "[CDN] Circuit breaker OPEN for tmpfiles — skipping. If "
            "litterbox is also open the cascade is empty. Breaker "
            "retries hourly."
        )
        return None

    file_size = file_path.stat().st_size
    if file_size > 100 * 1024 * 1024:  # 100MB tmpfiles limit
        logger.info(
            "[CDN] File too large for tmpfiles (%d MB), skipping", file_size // (1024 * 1024)
        )
        return None

    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                _TMPFILES_API,
                files={"file": (file_path.name, f)},
                timeout=_UPLOAD_TIMEOUT,
            )
        if resp.status_code != 200:
            logger.warning("tmpfiles: HTTP %d", resp.status_code)
            _tmpfiles_cb.record_failure()
            return None
        data = resp.json()
        if data.get("status") != "success":
            _tmpfiles_cb.record_failure()
            return None
        page_url = data.get("data", {}).get("url", "")
        # 2026-07-19: tmpfiles.org changed URL scheme. The old
        # ``/dl/{token}/{filename}`` pattern now 302s back to the
        # HTML wrapper page. The real direct-video URL requires a
        # timestamp-hash segment: ``/dl/{TS}.{HASH}/{token}/{filename}``
        # that's only available by scraping the wrapper HTML.
        #
        # HTML pattern: ``href="https://tmpfiles.org/dl/{TS}.{HASH}/...``
        # Verified 2026-07-19 with real Meta preflight failures across
        # all 3 IG-attempted channels (ai_creators, gaming, sports).
        # Prior naive replace yielded /dl/{token}/{file} → 302 → wrapper
        # → HEAD sees text/html → Meta preflight rejects.
        dl_url = _resolve_tmpfiles_direct_url(page_url)
        if dl_url is None:
            logger.warning(
                "tmpfiles: could not extract direct /dl/ URL from wrapper "
                "at %s — Meta preflight would reject as text/html",
                page_url,
            )
            _tmpfiles_cb.record_failure()
            return None
        logger.info("tmpfiles: %s → %s", file_path.name, dl_url)
        _tmpfiles_cb.record_success()
        return dl_url
    except Exception as exc:
        logger.warning("tmpfiles failed: %s", exc)
        _tmpfiles_cb.record_failure()
        return None


def upload_to_cdn_full(
    file_path: str | Path,
    expiry: str = "24h",
    max_attempts: int = 3,
    *,
    require_external: bool = False,
    exclude_providers: frozenset[str] = frozenset(),
) -> CdnUploadResult | None:
    """Upload a local file and return a :class:`CdnUploadResult` describing
    where it landed.

    Same tier order as :func:`upload_to_cdn` (tunnel → litterbox → tmpfiles),
    but skips any provider whose ID is in ``exclude_providers``. The caller
    uses this when Meta returns container error 2207077 on the previous URL
    — re-uploading to the *same* provider would yield another URL Meta still
    can't fetch, so the outer publisher loop excludes the failed provider
    and tries the next tier.

    Args:
        file_path: Local path to upload.
        expiry: Litterbox-only — how long the file should be hosted
            (``"1h"`` / ``"12h"`` / ``"24h"`` / ``"72h"``).
        max_attempts: Litterbox-only retry budget per attempt.
        require_external: If True, skip :data:`PROVIDER_TUNNEL` because Meta
            can't fetch through Cloudflare bot protection.
        exclude_providers: Frozenset of provider IDs to skip even when they
            would otherwise be tried. Used by the IG / Threads retry loop
            after a 2207077 to force a different provider on the next round.

    Returns:
        :class:`CdnUploadResult` on success, ``None`` if every non-excluded
        provider returned no URL.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        logger.error("CDN upload: file not found: %s", file_path)
        return None

    size_mb = file_path.stat().st_size / (1024 * 1024)
    logger.info(
        "CDN upload: %s (%.1f MB, expiry=%s, external=%s, exclude=%s)",
        file_path.name,
        size_mb,
        expiry,
        require_external,
        sorted(exclude_providers) if exclude_providers else "[]",
    )

    # Tier 1: Cloudflare tunnel (most reliable for direct access)
    if not require_external and PROVIDER_TUNNEL not in exclude_providers:
        url = _serve_via_tunnel(file_path)
        if url:
            return CdnUploadResult(url=url, provider=PROVIDER_TUNNEL, size_mb=size_mb)

    # Tier 2: Litterbox (externally accessible)
    if PROVIDER_LITTERBOX not in exclude_providers:
        url = _upload_to_litterbox(file_path, expiry, max_attempts)
        if url:
            return CdnUploadResult(url=url, provider=PROVIDER_LITTERBOX, size_mb=size_mb)
        logger.warning("Litterbox unreachable, trying tmpfiles.org...")

    # Tier 3: tmpfiles (externally accessible)
    if PROVIDER_TMPFILES not in exclude_providers:
        url = _upload_to_tmpfiles(file_path)
        if url:
            return CdnUploadResult(url=url, provider=PROVIDER_TMPFILES, size_mb=size_mb)

    return None


def upload_to_cdn(
    file_path: str | Path,
    expiry: str = "24h",
    max_attempts: int = 3,
    *,
    require_external: bool = False,
) -> str | None:
    """Backward-compatible wrapper around :func:`upload_to_cdn_full`.

    Returns just the URL string for callers that don't need provider /
    size metadata. New callers should prefer :func:`upload_to_cdn_full`
    so they can pass ``exclude_providers`` on retry.
    """
    result = upload_to_cdn_full(
        file_path,
        expiry=expiry,
        max_attempts=max_attempts,
        require_external=require_external,
    )
    return result.url if result is not None else None
