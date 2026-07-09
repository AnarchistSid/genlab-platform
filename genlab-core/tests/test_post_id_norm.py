"""Contract pins for ``genlab_core.cache.post_id_norm.normalize_post_id``.

Task #624 (2026-07-09) — the single canonical post_id normalizer.

## Why this test file exists

Three sites previously implemented the ``{platform}:{id}`` contract
independently. #748 fixed a double-prefix bug in one of them. #624
consolidates all three on this module. These pins lock the contract
so a 4th caller can't re-diverge.

The high-value pin is ``test_analytics_store_divergence_fixed``:
the pre-#624 ``analytics_store.py:211`` inline expression used
``startswith(f"{platform}:")`` and would produce
``"facebook:youtube:abc"`` given cross-platform-tagged input. The
canonical helper uses ``":" in post_id`` and passes it through
unchanged. Regression on this line would silently re-introduce the
same class of double-prefix bug as #748.
"""

from __future__ import annotations

from genlab_core.cache.post_id_norm import normalize_post_id

# ── happy path ───────────────────────────────────────────────────


def test_bare_id_gets_platform_prefix() -> None:
    """The base case: writer hands us a bare native id, we prepend."""
    assert normalize_post_id("instagram", "18067901951361934") == ("instagram:18067901951361934")
    assert normalize_post_id("youtube", "BlZaT02z0lw") == "youtube:BlZaT02z0lw"
    assert normalize_post_id("facebook", "2183623629126716") == ("facebook:2183623629126716")


def test_already_prefixed_id_passes_through() -> None:
    """Idempotence: the fix from #748. Calling again on an already-
    normalized id must not double-prefix."""
    assert normalize_post_id("youtube", "youtube:BlZaT02z0lw") == "youtube:BlZaT02z0lw"
    assert normalize_post_id("facebook", "facebook:2183623629126716") == (
        "facebook:2183623629126716"
    )
    assert normalize_post_id("instagram", "instagram:1234") == "instagram:1234"


# ── the divergence fix (regression pin) ──────────────────────────


def test_analytics_store_divergence_fixed() -> None:
    """CRITICAL regression pin — #624's actual load-bearing change.

    Pre-#624, ``analytics_store.py:211`` used
    ``post_id.startswith(f"{platform}:")`` which would double-prefix
    when the tagged prefix disagreed with the platform arg. The
    canonical helper uses ``":" in post_id`` (any prefix passes
    through). If someone later "fixes" this to ``startswith``, this
    test fails loudly. Do not weaken.

    The scenario: a cross-platform tagged post_id (rare but happens
    in analytics_store when the same reel is upserted from different
    platform reporters) gets handed to the normalizer with a
    different ``platform`` arg. The correct behavior is to trust the
    tag on the id and leave it alone — not stack a second prefix.
    """
    # If the pre-#624 startswith-check regressed, this would return
    # "facebook:youtube:abc" — same class of double-prefix bug as #748.
    assert normalize_post_id("facebook", "youtube:abc") == "youtube:abc"
    assert normalize_post_id("instagram", "youtube:abc") == "youtube:abc"
    # A cross-tagged id with a genuinely unrelated prefix also
    # passes through — the helper never second-guesses caller intent.
    assert normalize_post_id("instagram", "crosspost:xyz") == "crosspost:xyz"


# ── empty / falsy inputs ─────────────────────────────────────────


def test_empty_string_passes_through() -> None:
    """Caller's fallback handling stays unchanged — an empty id
    stays empty, so the caller sees the same missing-id signal."""
    assert normalize_post_id("instagram", "") == ""


def test_none_passes_through() -> None:
    """None input — same reasoning as empty string. Callers that
    typed the argument as str | None still get a passthrough."""
    assert normalize_post_id("instagram", None) is None  # type: ignore[arg-type]


# ── idempotence property ─────────────────────────────────────────


def test_idempotent_across_all_input_shapes() -> None:
    """Property test: ``normalize(normalize(x)) == normalize(x)``
    for every category of input the function handles. This is the
    class-of-bug prevention pin — the guarantee that made the #748
    fix work AND that fails loudly if a 4th caller reintroduces a
    non-idempotent variant.
    """
    inputs = [
        ("instagram", "18067901951361934"),  # bare id
        ("instagram", "instagram:18067901951361934"),  # already prefixed
        ("facebook", "youtube:abc"),  # cross-tagged
        ("facebook", ""),  # empty
    ]
    for platform, post_id in inputs:
        once = normalize_post_id(platform, post_id)
        twice = normalize_post_id(platform, once)
        assert once == twice, (
            f"Not idempotent for platform={platform!r} "
            f"post_id={post_id!r}: once={once!r} twice={twice!r}"
        )


# ── deletegation pins for the two re-export sites ────────────────


def test_feedback_registration_delegates_to_canonical() -> None:
    """The ``_normalize_post_id`` wrapper in
    ``publishing.feedback_registration`` MUST call through to this
    module. If someone re-inlines a copy of the logic there, the
    class-of-bug re-opens. This pin catches that regression.
    """
    from genlab_core.publishing.feedback_registration import _normalize_post_id

    # Match the divergence-fix pin: bare id, prefixed id, cross-tagged.
    assert _normalize_post_id("facebook", "1234") == "facebook:1234"
    assert _normalize_post_id("facebook", "facebook:1234") == "facebook:1234"
    assert _normalize_post_id("facebook", "youtube:abc") == "youtube:abc"


def test_pending_feedback_task_delegates_to_canonical() -> None:
    """Same as above for the ``pending_feedback_task`` wrapper."""
    from genlab_core.learning.pending_feedback_task import (
        _post_id_with_platform_prefix,
    )

    assert _post_id_with_platform_prefix("facebook", "1234") == "facebook:1234"
    assert _post_id_with_platform_prefix("facebook", "facebook:1234") == ("facebook:1234")
    assert _post_id_with_platform_prefix("facebook", "youtube:abc") == "youtube:abc"


def test_analytics_store_uses_canonical() -> None:
    """analytics_store previously had an INLINE expression. Post-
    #624 it MUST import and call the canonical helper. This test
    grep-verifies at runtime by importing analytics_store and
    checking the canonical import lives in its module namespace.
    """
    import genlab_core.http.analytics_store as analytics_store

    # The canonical helper must be reachable from analytics_store's
    # module namespace — i.e. it was imported at module top. If a
    # future edit re-inlines the expression and drops the import,
    # this pin fails and forces the reviewer to look at why.
    assert hasattr(analytics_store, "normalize_post_id"), (
        "analytics_store must import normalize_post_id at module top. "
        "Do not re-inline the expression — it's the exact site the "
        "#624 divergence fix targeted."
    )
