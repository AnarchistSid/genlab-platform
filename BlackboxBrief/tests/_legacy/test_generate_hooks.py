"""Regression tests for formula-based hook generation quality."""

import re

from execution.generate_hooks import (
    extract_hook_elements,
    generate_hook_variants,
    validate_hook_grammar,
)


def test_extract_hook_elements_prefers_billion_number_over_multiplier():
    story = {
        "title": "OpenAI secures $50 billion after 15x growth in enterprise usage",
        "summary": "The round follows a 15x increase in paid deployments this quarter.",
    }

    elements = extract_hook_elements(story)

    assert "billion_number" in elements
    assert not elements["billion_number"].lower().endswith("x")
    assert "B" in elements["billion_number"].upper()


def test_validate_hook_grammar_rejects_multiplier_plus_billion_combo():
    hook = "💰 15x BILLION just announced for AI Safety"
    is_valid, issues = validate_hook_grammar(hook)

    assert not is_valid
    assert any("Invalid number-unit combination" in issue for issue in issues)


def test_generate_hook_variants_do_not_emit_x_billion_artifact():
    story = {
        "title": "OpenAI launches GPT-5 after 15x benchmark jump and $50 billion funding",
        "summary": "The company says the new model performs 15x faster and closed a $50 billion round for AI infrastructure.",
    }

    variants = generate_hook_variants(story, count=10)

    assert variants
    for variant in variants:
        hook = variant.get("hook", "")
        assert not re.search(r"\b\d+(?:\.\d+)?x\s+BILLION\b", hook, re.IGNORECASE)
        assert not re.search(r"\b0{2,}\d*x\b", hook, re.IGNORECASE)


def test_generate_hook_variants_skip_users_template_for_multiplier_numbers():
    story = {
        "title": "OpenAI launches GPT-5 with 1000x faster search",
        "summary": "The release claims a 1000x speedup on internal benchmarks.",
    }

    variants = generate_hook_variants(story, count=10)

    assert variants
    for variant in variants:
        hook = variant.get("hook", "").lower()
        assert not ("users in" in hook and "x" in hook)


def test_generate_hook_variants_skip_timeline_template_for_multiplier_numbers():
    story = {
        "title": "Startup reports 15x growth after new AI launch",
        "summary": "The company says adoption grew 15x quarter over quarter.",
    }

    variants = generate_hook_variants(story, count=10)

    assert variants
    for variant in variants:
        hook = variant.get("hook", "").lower()
        assert "in 15x:" not in hook
