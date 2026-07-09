"""Canonical `post_id` normalizer.

Task #624 (2026-07-09) — single source of truth for the
``{platform}:{native_id}`` composite-id contract that joins
``pending_feedback.post_id`` ↔ ``publishing_analytics.post_id`` at
metric-collection time.

## Why one function

Three sites in genlab-core previously implemented this contract
independently:

* ``publishing.feedback_registration._normalize_post_id`` — check
  ``":" in post_id``, pass through if present.
* ``learning.pending_feedback_task._post_id_with_platform_prefix``
  — check ``":" in post_id``, pass through if present.
* ``http.analytics_store`` inline expression — check
  ``post_id.startswith(f"{platform}:")``, pass through **only if
  the exact platform prefix matches**.

The third diverged: given cross-platform-tagged input
``"youtube:abc"`` with ``platform="facebook"``, sites 1 and 2 leave
it alone but site 3 produces ``"facebook:youtube:abc"`` — the same
class of double-prefix bug that #748 fixed in ``pending_feedback``.

This module unifies all three on the ``":" in post_id`` semantics
so the divergence can't reappear when a 4th caller is added.

## The contract

* ``normalize_post_id(platform, post_id)`` is **idempotent**:
  ``normalize_post_id(p, normalize_post_id(p, x)) == normalize_post_id(p, x)``
  for all ``p, x``.
* Empty / falsy ``post_id`` passes through unchanged (caller
  handles the missing-id path however it wants).
* Any ``":"``-shaped ``post_id`` passes through unchanged — a
  caller who tagged the id with a different platform prefix did
  so intentionally (rare cross-platform tag case), and this
  helper trusts them.
* Bare ``post_id`` gets ``"{platform}:"`` prepended.

## Related memory

See ``[[class-of-bug-alerts-must-reflect-current-state-not-historical-signal]]``
for the write-side idempotence discipline this helper embodies.
"""

from __future__ import annotations


def normalize_post_id(platform: str, post_id: str) -> str:
    """Return ``post_id`` in canonical ``{platform}:{native_id}`` form.

    Idempotent: safe to call repeatedly; safe to compose with itself.

    See module docstring for the full contract + divergence-fix
    rationale.
    """
    if not post_id:
        return post_id
    if ":" in post_id:
        return post_id
    return f"{platform}:{post_id}"
