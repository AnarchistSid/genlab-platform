"""Stable ID generation utilities.

All IDs are deterministic: same inputs always produce the same ID.
This ensures idempotent upserts and deduplication.
"""

import hashlib
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlparse

# Common tracking parameters to strip during URL normalization
TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'ref', 'source', 'fbclid', 'gclid', 'mc_cid', 'mc_eid'
}


def normalize_url(url: str) -> str:
    """Remove tracking params and normalize URL.

    Args:
        url: URL string to normalize. Must be non-empty and contain a scheme.

    Returns:
        Canonical URL with tracking params stripped, lowercased.

    Raises:
        ValueError: If url is empty or missing scheme/netloc.
    """
    if not url or not isinstance(url, str):
        raise ValueError(f"normalize_url requires a non-empty string, got: {url!r}")

    url = url.strip()
    parsed = urlparse(url)
    # Only lowercase scheme + netloc; preserve path/query/fragment case
    parsed = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
    )

    if not parsed.scheme or not parsed.netloc:
        raise ValueError(
            f"normalize_url requires a URL with scheme and host, got: {url!r}"
        )

    # Remove common tracking params
    if parsed.query:
        params = parse_qs(parsed.query)
        params = {k: v for k, v in params.items() if k not in TRACKING_PARAMS}
        query = urlencode(params, doseq=True)
    else:
        query = ''

    # Strip trailing slash for consistency (except root path)
    path = parsed.path.rstrip('/') or '/'

    # Rebuild URL
    canonical = f"{parsed.scheme}://{parsed.netloc}{path}"
    if query:
        canonical += f"?{query}"

    return canonical


def generate_story_id(url: str, published_at: str) -> str:
    """Generate stable story ID from URL + publish date.

    Returns: 64-char hex string (SHA-256)
    """
    canonical_url = normalize_url(url)

    # Normalize timestamp to date only (ignore time for stability)
    try:
        dt = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
        date_str = dt.date().isoformat()
    except (ValueError, TypeError):
        date_str = published_at[:10]  # Fallback to YYYY-MM-DD

    content = f"{canonical_url}|{date_str}"
    return hashlib.sha256(content.encode()).hexdigest()


def generate_candidate_id(story_id: str, template_id: str, angle: str) -> str:
    """Generate stable candidate ID.

    Returns: 64-char hex string (SHA-256)
    """
    content = f"{story_id}|{template_id}|{angle.lower().replace(' ', '_')}"
    return hashlib.sha256(content.encode()).hexdigest()


def generate_claim_id(text: str) -> str:
    """Generate stable claim ID.

    Returns: CLM_ + 8 uppercase hex chars
    """
    hash_full = hashlib.sha256(text.encode()).hexdigest()
    return f"CLM_{hash_full[:8].upper()}"


def generate_asset_id(story_id: str, source_url: str, asset_type: str) -> str:
    """Generate stable asset ID.

    Returns: AST_ + 16 uppercase hex chars
    """
    content = f"{story_id}|{source_url}|{asset_type}"
    hash_full = hashlib.sha256(content.encode()).hexdigest()
    return f"AST_{hash_full[:16].upper()}"


def generate_cluster_id(story_ids: list[str]) -> str:
    """Generate deterministic cluster ID from sorted member story_ids.

    Stable: same set of story_ids always produces the same cluster_id,
    regardless of input order.

    Returns: CLU_ + 16 uppercase hex chars
    """
    sorted_ids = sorted(story_ids)
    content = "|".join(sorted_ids)
    hash_full = hashlib.sha256(content.encode()).hexdigest()
    return f"CLU_{hash_full[:16].upper()}"


def generate_global_asset_id(source_url: str, asset_type: str) -> str:
    """Generate URL-global asset ID (not story-scoped).

    Two stories sharing the same image URL produce the same global_asset_id.
    Uses normalize_url() to strip tracking params before hashing.

    Returns: GAST_ + 16 uppercase hex chars
    """
    normalized = normalize_url(source_url)
    content = f"{normalized}|{asset_type}"
    hash_full = hashlib.sha256(content.encode()).hexdigest()
    return f"GAST_{hash_full[:16].upper()}"


def generate_ugc_asset_id(source_url: str, source_type: str) -> str:
    """Generate stable UGC asset ID.

    Returns: UGC_ + 16 uppercase hex chars
    """
    normalized = normalize_url(source_url)
    content = f"{normalized}|{source_type}"
    hash_full = hashlib.sha256(content.encode()).hexdigest()
    return f"UGC_{hash_full[:16].upper()}"


def generate_post_id(candidate_id: str, publish_date: str) -> str:
    """Generate stable post ID.

    Returns: 64-char hex string (SHA-256)
    """
    content = f"{candidate_id}|{publish_date}"
    return hashlib.sha256(content.encode()).hexdigest()
