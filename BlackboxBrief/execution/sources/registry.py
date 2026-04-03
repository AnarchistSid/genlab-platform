"""Registry and guardrails for connector-backed source fetchers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

from execution.sources.civitai_connector import fetch_source as fetch_civitai_source
from execution.sources.hf_spaces_connector import fetch_source as fetch_hf_spaces_source
from execution.sources.playwright_profile_connector import (
    fetch_source as fetch_playwright_profile_source,
)
from execution.sources.x_connector import fetch_source as fetch_x_source
from execution.sources.youtube_connector import fetch_source as fetch_youtube_source

ConnectorHandler = Callable[[Dict[str, Any]], Dict[str, Any]]

_CONNECTORS: Dict[str, ConnectorHandler] = {
    "youtube": fetch_youtube_source,
    "x": fetch_x_source,
    "twitter": fetch_x_source,
    "civitai": fetch_civitai_source,
    "hf_spaces": fetch_hf_spaces_source,
    "playwright_profile": fetch_playwright_profile_source,
}


def classify_fetch_status(error_text: str) -> str:
    """Normalize raw exception text to supported fetch statuses."""
    msg = (error_text or "").lower()
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return "rate_limited"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    return "failed"


def _empty_result(
    src: Dict[str, Any],
    connector: str,
    error: str,
    status: str = "failed",
) -> Dict[str, Any]:
    """Return a standardized failed fetch payload for a connector source."""
    return {
        "url": src.get("url", ""),
        "source_id": src.get("source_id", ""),
        "platform": src.get("platform", ""),
        "entry_count": 0,
        "entries": [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetch_method": f"connector:{connector}",
        "fetch_status": status,
        "error": error,
    }


def _normalize_result(
    src: Dict[str, Any],
    connector: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """Ensure connector results conform to the fetch contract."""
    normalized = dict(result or {})
    entries: List[Dict[str, Any]] = list(normalized.get("entries") or [])

    normalized.setdefault("url", src.get("url", ""))
    normalized.setdefault("source_id", src.get("source_id", ""))
    normalized.setdefault("platform", src.get("platform", ""))
    normalized.setdefault("entries", entries)
    normalized.setdefault("entry_count", len(entries))
    normalized.setdefault("fetched_at", datetime.now(timezone.utc).isoformat())
    normalized.setdefault("fetch_method", f"connector:{connector}")

    if normalized.get("error"):
        normalized.setdefault(
            "fetch_status",
            classify_fetch_status(str(normalized.get("error", ""))),
        )
    else:
        normalized.setdefault("fetch_status", "success")

    return normalized


def fetch_via_connector(src: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch to a connector handler and normalize output."""
    connector = (src.get("connector", "") or "").strip().lower()
    if not connector:
        return _empty_result(src, "unknown", "Missing connector name in source config")

    handler = _CONNECTORS.get(connector)
    if handler is None:
        return _empty_result(src, connector, f"Unknown connector: {connector}")

    try:
        result = handler(src)
    except Exception as exc:  # pragma: no cover - safety net
        status = classify_fetch_status(str(exc))
        return _empty_result(src, connector, str(exc), status=status)

    return _normalize_result(src, connector, result)
