"""Pin: Constitutional AI principles in hook generator system prompt.

The 2026-06-22 'make-the-agent-smarter' audit identified Constitutional
AI (Bai et al. 2023) as the highest-leverage zero-cost lever: explicit
principles at the top of the system prompt reduce violations vs.
principles scattered through constraints.

This PR consolidates 5 explicit principles (GROUNDED, SPECIFIC,
ON-NICHE, ANSWERABLE, DEFENSIBLE) into a single CONSTITUTION block
the LLM self-verifies against during generation.

Cost: ~5% prompt-token overhead, no new LLM calls. The block lives
INSIDE the hook-generation call (vs. Lever K hook critic which is a
SEPARATE post-generation LLM judge — they compound, not duplicate).

These pins guard:
- All 5 principles literally appear in the system prompt
- The principles use the precise wording downstream code relies on
- The principles are placed BEFORE the existing rule constraints
  (Constitutional AI research: principle-first beats principle-last)
"""

from __future__ import annotations

from pathlib import Path

_HOOK_GENERATOR_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "genlab_core"
    / "writing"
    / "llm_hook_generator.py"
)


class TestConstitutionPresent:
    """Source-level pins: the 5 principles are literally present in
    the hook generator. Source pin (vs runtime pin) is appropriate
    because the system prompt is a static string — runtime tests
    would just re-read the file content."""

    def test_constitution_header_present(self):
        """Pin: the CONSTITUTION header appears (anchor for the block)."""
        content = _HOOK_GENERATOR_SRC.read_text()
        assert "CONSTITUTION" in content
        # Header phrase used to introduce the principles
        assert "self-verify your hook against these 5 principles" in content

    def test_principle_1_grounded(self):
        """Pin: GROUNDED principle (no invented proper nouns).

        This is the most-violated principle historically (the 2026-06-20
        SpliceReel Pixar/Toy-Story-4 hallucination). The keyword
        'GROUNDED' must appear, and the explanation must mention
        'invented names'."""
        content = _HOOK_GENERATOR_SRC.read_text()
        assert "1. GROUNDED:" in content
        assert "invented names" in content

    def test_principle_2_specific(self):
        """Pin: SPECIFIC principle (concrete details required).

        Counters the generic-template failure mode the audit found
        80-90% saturated in 4 of 5 niches."""
        content = _HOOK_GENERATOR_SRC.read_text()
        assert "2. SPECIFIC:" in content

    def test_principle_3_on_niche(self):
        """Pin: ON-NICHE principle (niche-appropriate vocabulary).

        References the per-niche audience string from style config."""
        content = _HOOK_GENERATOR_SRC.read_text()
        assert "3. ON-NICHE:" in content
        # The audience-substitution placeholder must be wired
        assert "{style['audience']}" in content

    def test_principle_4_answerable(self):
        """Pin: ANSWERABLE principle (questions must have answers).

        Counters the rhetorical-bait failure mode."""
        content = _HOOK_GENERATOR_SRC.read_text()
        assert "4. ANSWERABLE (if question):" in content

    def test_principle_5_defensible(self):
        """Pin: DEFENSIBLE principle (claims must be supportable).

        Counters unsupported superlatives ('best ever', 'first time')."""
        content = _HOOK_GENERATOR_SRC.read_text()
        assert "5. DEFENSIBLE (if claim):" in content


class TestConstitutionPlacement:
    """Pin: principles appear BEFORE existing constraints. Constitutional
    AI research (Bai et al. 2023) shows principle-first prompts reduce
    violations better than principle-last."""

    def test_constitution_before_length_rule(self):
        """Pin: CONSTITUTION block placed before the 20-60 chars rule.

        Order matters — LLM models attend more to early prompt content.
        The existing 'A hook is the text overlay...' line should come
        AFTER the principles."""
        content = _HOOK_GENERATOR_SRC.read_text()
        constitution_idx = content.find("CONSTITUTION")
        hook_intro_idx = content.find("A hook is the text overlay")
        assert constitution_idx > 0, "CONSTITUTION block must be present"
        assert hook_intro_idx > 0, "Hook intro line must be present"
        assert constitution_idx < hook_intro_idx, (
            f"CONSTITUTION (at {constitution_idx}) must come BEFORE the "
            f"length-rule intro (at {hook_intro_idx}). Constitutional AI "
            "research: principle-first beats principle-last."
        )


class TestConstitutionAdditive:
    """Pin: the existing anti-fabrication + banned-phrases rules
    STAY in the system prompt. CONSTITUTION is additive, not a
    replacement — the granular rules provide concrete examples
    while the principles provide the abstract contract."""

    def test_anti_fabrication_rules_still_present(self):
        """Pin: ANTI-FABRICATION block (2026-06-20 Pixar/TS4 fix) is
        still there. Don't regress that hardening when adding the
        Constitution above it."""
        content = _HOOK_GENERATOR_SRC.read_text()
        assert "STRICT ANTI-FABRICATION RULES" in content
        # The specific Pixar hallucination case the rules were added for
        assert "Pixar" in content  # context comment reference

    def test_banned_phrases_still_present(self):
        """Pin: BANNED phrases block still injects the dynamic list."""
        content = _HOOK_GENERATOR_SRC.read_text()
        assert "BANNED phrases" in content
        assert "{banned_text}" in content
