"""Pin: ``_get_persona_engine`` caches PersonaEngine per niche_id.

Perf fix (2026-06-21): the engagement worker rebuilt a fresh
``PersonaEngine`` (and re-loaded the persona YAML) on every reply.
At Dramatiq actor scale (multiple replies/min per platform) this was
wasted disk I/O + YAML parsing on the hot path. The fix mirrors the
``_toxicity_gate`` singleton pattern already in this module.

These pins lock in:

  1. N calls to ``_get_persona_engine("gaming")`` return the SAME
     PersonaEngine instance (object identity, not just equality).
  2. ``_load_persona`` is called EXACTLY ONCE for the first request
     per niche, never again for that niche on the same worker.
  3. Different niches get distinct cached engines (no cross-niche
     persona contamination — gaming persona must never be served for
     a sports reply).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from genlab_core.engagement.persona_schema import (
    NichePersona,
    ReplyConstraints,
    VoiceConfig,
)


def _make_persona(niche_id: str, name: str) -> NichePersona:
    """Build a minimal valid persona — niche_id + name distinguish the
    two fixtures used in the cross-niche-isolation pin."""
    return NichePersona(
        name=name,
        niche_id=niche_id,
        voice=VoiceConfig(
            formality=0.5,
            enthusiasm=0.5,
            emoji_density="moderate",
            vocabulary="balanced",
        ),
        style_examples=["nice", "cool"],
        topics_to_avoid=["politics"],
        reply_constraints=ReplyConstraints(max_length_chars=150),
    )


@pytest.fixture(autouse=True)
def _clear_persona_cache():
    """Each test starts with an empty cache; otherwise cache leakage
    from a prior test would make later tests trivially pass."""
    from genlab_core.engagement import comment_processor as cp

    cp._persona_engine_cache.clear()
    yield
    cp._persona_engine_cache.clear()


class TestPersonaEngineCachedPerNiche:
    def test_repeated_calls_return_same_instance(self):
        """Object identity is the strongest cache pin — proves we're
        returning the cached value, not just an equal one."""
        from genlab_core.engagement import comment_processor as cp

        with patch.object(cp, "_load_persona", return_value=_make_persona("gaming", "CR")):
            engine1 = cp._get_persona_engine("gaming")
            engine2 = cp._get_persona_engine("gaming")
            engine3 = cp._get_persona_engine("gaming")

        assert engine1 is engine2 is engine3, (
            "Cache miss: _get_persona_engine returned different instances for the same niche_id."
        )

    def test_load_persona_called_once_per_niche(self):
        """The expensive disk-IO + YAML parse fires once on first
        access, then never again for the same niche."""
        from genlab_core.engagement import comment_processor as cp

        with patch.object(
            cp,
            "_load_persona",
            return_value=_make_persona("gaming", "CR"),
        ) as spy_load:
            for _ in range(25):
                cp._get_persona_engine("gaming")

            # The pin: the heavy load happens exactly ONCE despite 25
            # logical reply requests. Before the fix this was 25.
            assert spy_load.call_count == 1, (
                f"Expected _load_persona called 1× for 25 gaming requests, "
                f"got {spy_load.call_count}. Cache regression?"
            )

    def test_different_niches_get_distinct_engines(self):
        """No cross-niche contamination — gaming and sports must each
        get THEIR persona, not whoever happened to be cached first."""
        from genlab_core.engagement import comment_processor as cp

        def _fake_load(niche_id):
            return _make_persona(niche_id, name=f"Channel-{niche_id}")

        with patch.object(cp, "_load_persona", side_effect=_fake_load) as spy_load:
            gaming_engine = cp._get_persona_engine("gaming")
            sports_engine = cp._get_persona_engine("sports")
            # Re-request both — should hit cache.
            gaming_again = cp._get_persona_engine("gaming")
            sports_again = cp._get_persona_engine("sports")

        # Object identity per niche, distinct between niches.
        assert gaming_engine is gaming_again
        assert sports_engine is sports_again
        assert gaming_engine is not sports_engine, (
            "Cross-niche contamination: gaming and sports returned the "
            "same engine instance — cache key not respecting niche_id."
        )
        # Each persona's identity preserved.
        assert gaming_engine._persona.name == "Channel-gaming"
        assert sports_engine._persona.name == "Channel-sports"
        # Load fires once per niche (2 calls total, not 4).
        assert spy_load.call_count == 2, (
            f"Expected 2 loads (one per niche), got {spy_load.call_count}."
        )
