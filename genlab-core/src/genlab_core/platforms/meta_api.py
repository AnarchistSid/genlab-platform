"""Single source of truth for the Meta Graph API version.

Closes audit R-44's "scattered v21.0 across ~20 sites" problem.
Before this module, the Graph API version was hardcoded as the literal
string ``v21.0`` in 8 different files (platform clients, metric
collectors, pollers, monitoring, scripts). Bumping required a grep +
replace sweep and easy-to-miss sites — a known maintenance trap that
also masked the looming ~June 2026 metric-retirement deadline.

Use these constants everywhere a Graph API URL is constructed:

    from genlab_core.platforms.meta_api import META_GRAPH_BASE_URL
    url = f"{META_GRAPH_BASE_URL}/{post_id}/insights"

Or for client constructors that accept ``api_version``:

    from genlab_core.platforms.meta_api import META_GRAPH_API_VERSION
    client = FacebookClient(api_version=META_GRAPH_API_VERSION)

Bumping the version (e.g. when v25.0 sunset deadlines arrive):
    1. Update ``META_GRAPH_API_VERSION`` below.
    2. Run the test suite — the tests pin the version via this module,
       so a mismatched literal in a new caller fails CI immediately.
    3. Re-test FB/IG publish + insights against the bumped version.

Audit ref: R-44.
"""

from __future__ import annotations

from typing import Final

# Meta Graph API version. Bumped from v21.0 to v22.0 — the audit
# recommended target (v25.0) post-dates this codebase's Meta SDK
# pin; v22.0 is the most recent version known compatible with our
# ``tweepy``-style EAA Page Token usage + the legacy insight metric
# names. Schedule a v23.0+ bump as part of the metric-retirement
# audit follow-up (R-44 part 2).
META_GRAPH_API_VERSION: Final[str] = "v22.0"

# Convenience: the full base URL, since most callers
# concatenate ``/{path}`` onto this exact prefix.
META_GRAPH_BASE_URL: Final[str] = f"https://graph.facebook.com/{META_GRAPH_API_VERSION}"
