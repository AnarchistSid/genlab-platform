"""Back-compat alias rules for the canonical per-platform metrics shape.

After refactor #1 collapsed the three FetchInsights paths into shared
canonical fetchers (:mod:`genlab_core.platforms.metrics`), the two
remaining call-sites — :mod:`genlab_core.pipeline.stages.fetch_insights`
and :mod:`genlab_core.scripts.run_fetch_insights` — still each wrapped
the canonical return with their own legacy-alias logic so downstream
SharePoint readers and analytics composite-score code expecting the
older key names kept working.

That alias-wrapping was duplicated 8 times (4 platforms × 2 call-sites)
with the same intent and subtly different bodies. This module is the
single source of truth: :func:`add_legacy_aliases` mutates the canonical
metrics dict in place with the per-platform legacy keys. Refactor-#10
wrap-up extracts it.

Alias table
-----------

Each entry is ``"new_key" -> "legacy_alias"``. Per-platform:

* Instagram: ``saves`` → ``saved`` (legacy SharePoint column name);
  ``watch_time_ms`` → ``watch_time_minutes`` (legacy minutes-as-float
  column).
* Twitter/X: ``shares`` → ``retweets``; ``comments`` → ``replies``.
* YouTube + Facebook: no aliases (canonical and legacy shapes already
  match for these platforms).

If a future canonical key is added that needs a back-compat alias on
a particular platform, add it here, not at each call-site.
"""

from __future__ import annotations

from typing import Any

# Per-platform alias rules. The value is a list of (canonical_key,
# legacy_alias_key, transform) tuples. ``transform`` is a callable
# applied to the canonical value before storing under the alias —
# ``None`` means store as-is.
_ALIAS_RULES: dict[str, list[tuple[str, str, Any]]] = {
    "instagram": [
        ("saves", "saved", None),
        ("watch_time_ms", "watch_time_minutes", lambda ms: round(ms / 60000, 1)),
    ],
    "twitter": [
        ("shares", "retweets", None),
        ("comments", "replies", None),
    ],
    "x": [  # canonical alias of twitter — same rules
        ("shares", "retweets", None),
        ("comments", "replies", None),
    ],
    "x_twitter": [  # registry-id form — same rules
        ("shares", "retweets", None),
        ("comments", "replies", None),
    ],
}


def add_legacy_aliases(metrics: dict[str, Any] | None, platform: str) -> dict[str, Any] | None:
    """Add per-platform legacy aliases onto ``metrics`` and return it.

    Returns ``None`` when input is ``None`` so the canonical "no data"
    pass-through stays clean (callers branch on ``None`` to mark SKIPPED
    rather than write zeros). Returns a copy when aliases are added so
    the caller's mutation safety isn't broken if the source dict was
    shared.

    Unknown platforms pass through unchanged — there's nothing to alias
    on platforms not in :data:`_ALIAS_RULES`.
    """
    if metrics is None:
        return None
    rules = _ALIAS_RULES.get(platform)
    if not rules:
        return dict(metrics)  # still hand back a copy so caller can mutate

    out: dict[str, Any] = dict(metrics)
    for canonical_key, legacy_key, transform in rules:
        if canonical_key not in out:
            continue
        value = out[canonical_key]
        out[legacy_key] = transform(value) if transform else value
    return out
