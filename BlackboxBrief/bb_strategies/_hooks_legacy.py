#!/usr/bin/env python3
"""Hook generator — formula-driven hook creation with engagement scoring.

Extracts story elements (companies, products, numbers, claims) and matches
them against proven hook formulas from config/hook_formulas.yaml. Generates
multiple hook variants per story and ranks them by predicted engagement.

Five core functions:
    1. extract_hook_elements(story)         → {company, product, number, claim, ...}
    2. select_best_category(story)          → auto-detect story category (multi-signal)
    3. match_best_formula(story, elements)  → best-matching formula
    4. generate_hook_variants(story, count) → 5 ranked hook options
    5. score_hooks(variants)                → engagement-scored + ranked list

Usage:
    from execution.generate_hooks import generate_hook_variants, score_hooks

    # Get 5 hook variants for a story
    variants = generate_hook_variants(story, count=5)
    scored = score_hooks(variants)
    best_hook = scored[0]["hook"]  # Highest-scoring hook

CLI:
    # Test on a story
    python execution/generate_hooks.py --title "OpenAI Releases GPT-5" --summary "..."

    # Test on multiple stories from a trend pack
    python execution/generate_hooks.py --trend-pack .tmp/runs/latest/trend_pack.json

Dependencies:
    - config/hook_formulas.yaml (hook patterns + scoring weights)
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Known entities (companies, products, people) ──────────────
# Maintained separately from text_sanitizer's KNOWN_ENTITIES for hook-specific needs
KNOWN_COMPANIES = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "google deepmind": "Google DeepMind",
    "deepmind": "DeepMind",
    "meta": "Meta",
    "microsoft": "Microsoft",
    "nvidia": "NVIDIA",
    "apple": "Apple",
    "amazon": "Amazon",
    "hugging face": "Hugging Face",
    "huggingface": "Hugging Face",
    "stability ai": "Stability AI",
    "midjourney": "Midjourney",
    "mistral": "Mistral",
    "cohere": "Cohere",
    "inflection": "Inflection AI",
    "xai": "xAI",
    "x.ai": "xAI",
    "perplexity": "Perplexity",
    "runway": "Runway",
    "adobe": "Adobe",
    "salesforce": "Salesforce",
    "baidu": "Baidu",
    "alibaba": "Alibaba",
    "samsung": "Samsung",
    "tesla": "Tesla",
    "deepseek": "DeepSeek",
    "recursion": "Recursion",
    "github": "GitHub",
}

KNOWN_PRODUCTS = {
    "gpt-5": "GPT-5",
    "gpt-4": "GPT-4",
    "gpt-4o": "GPT-4o",
    "gpt-4.5": "GPT-4.5",
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "claude opus": "Claude Opus",
    "claude opus 4": "Claude Opus 4",
    "claude sonnet": "Claude Sonnet",
    "claude haiku": "Claude Haiku",
    "gemini": "Gemini",
    "gemini 2": "Gemini 2",
    "gemini 2.0": "Gemini 2.0",
    "gemini ultra": "Gemini Ultra",
    "llama": "Llama",
    "llama 3": "Llama 3",
    "llama 4": "Llama 4",
    "dall-e": "DALL-E",
    "dall-e 3": "DALL-E 3",
    "midjourney": "Midjourney",
    "stable diffusion": "Stable Diffusion",
    "sora": "Sora",
    "copilot": "Copilot",
    "cursor": "Cursor",
    "devin": "Devin",
    "o1": "o1",
    "o3": "o3",
    "mixtral": "Mixtral",
    "phi": "Phi",
    "phi-3": "Phi-3",
    "phi-4": "Phi-4",
    "codex": "Codex",
    "whisper": "Whisper",
    "agi": "AGI",
    "grok": "Grok",
    "perplexity": "Perplexity",
    "deepseek": "DeepSeek",
    "deepseek v3": "DeepSeek V3",
    "apple intelligence": "Apple Intelligence",
    "apple intelligence 2": "Apple Intelligence 2",
    "grok 3": "Grok 3",
    "h200": "H200",
    "claude code": "Claude Code",
}

# Number patterns
_NUMBER_PATTERNS = [
    # Multipliers: 10x, 100x
    (r'\b(\d+(?:\.\d+)?)\s*x\b', lambda m: f"{m.group(1)}x"),
    # Spelled scales: $50 billion, 300 million, 2.5 trillion
    (
        r'(?<!\w)(\$)?\s*(\d+(?:\.\d+)?)\s*(billion|million|trillion)\b',
        lambda m: (
            f"{'$' if m.group(1) else ''}{m.group(2)}"
            f"{'B' if m.group(3).lower() == 'billion' else 'M' if m.group(3).lower() == 'million' else 'T'}"
        ),
    ),
    # Large numbers with M/B/T suffix: 500M, 1.5B, 2T
    (r'\b(\d+(?:\.\d+)?)\s*([MBTmbt])\b', lambda m: f"{m.group(1)}{m.group(2).upper()}"),
    # Percentages: 50%, 99.9%
    (r'\b(\d+(?:\.\d+)?)\s*%', lambda m: f"{m.group(1)}%"),
    # Dollar amounts: $10M, $1B
    (r'\$\s*(\d+(?:\.\d+)?)\s*([MBTKmbtk])?', lambda m: f"${m.group(1)}{(m.group(2) or '').upper()}"),
    # Plain large numbers: 1000, 10000
    (r'\b(\d{4,})\b', lambda m: m.group(1)),
    # Year references: 2025, 2026, 2027
    (r'\b(20\d{2})\b', lambda m: m.group(1)),
    # Small specific numbers: "5 reasons", "3 features"
    (r'\b(\d{1,2})\s+(reasons?|features?|ways?|things?|tips?|tricks?|tools?|steps?)',
     lambda m: m.group(1)),
]


# ══════════════════════════════════════════════════════════════
# 0. Hook grammar validation
# ══════════════════════════════════════════════════════════════

# Bracket artifacts from raw template substitution (e.g., "[fixed]", "[topic]")
_BRACKET_RE = re.compile(r'\[[\w\s]+\]')

# Known ALL-CAPS patterns that are intentional (not grammatical errors)
_INTENTIONAL_CAPS = {
    "BREAKING", "JUST IN", "URGENT", "MASSIVE", "WATCH", "UPDATE",
    "AI", "GPT", "LLM", "CEO", "CTO", "API", "UI", "UX", "AGI",
    "QQQ", "USA", "EU", "UK", "DALL", "VR", "AR", "XR", "SDK",
    "YES", "NO", "OR", "AND", "IS", "A", "THE", "THIS", "IT",
    "WOULD", "YOU", "USE", "CHANGES", "EVERYTHING", "INSANE",
    "GAME", "CHANGER", "UNREAL", "TRUTH", "ABOUT", "THINK",
    "ACTUALLY", "BAD", "DROPPED", "NOBODY", "TALKING",
}

# Suspicious numeric artifacts from noisy parsing/templating.
_SUSPICIOUS_NUMBER_RE = re.compile(r'^\$?0{2,}\d*(?:\.\d+)?[XMBT%]?$', re.IGNORECASE)
_BILLION_CONTEXT_RE = re.compile(r'\b(billion|trillion|bn|tn)\b', re.IGNORECASE)


def validate_hook_grammar(hook: str) -> Tuple[bool, List[str]]:
    """Validate hook text for grammar/quality issues.

    Checks for common problems from raw template substitution:
      1. Bracket artifacts: [topic], [fixed], etc.
      2. Too short: fewer than 3 words
      3. Truncated: ends mid-word (rare but possible with word-limit cuts)
      4. Repeated words: "OpenAI OpenAI launches"
      5. Lowercase start: hooks should start with a capital letter or emoji
      6. Consecutive non-acronym ALL-CAPS: "BREAKING listen labs IS" — messy

    Returns:
        (is_valid, list_of_issues) — empty list means the hook is clean.
    """
    issues: List[str] = []
    hook = hook.strip()

    if not hook:
        return False, ["Empty hook"]

    # 1. Bracket artifacts
    if _BRACKET_RE.search(hook):
        issues.append(f"Bracket artifact: {_BRACKET_RE.findall(hook)}")

    # 2. Too short
    words = hook.split()
    if len(words) < 3:
        issues.append(f"Too short ({len(words)} words)")

    # 3. Starts with lowercase (not emoji or number)
    # Allow intentional lowercase for conversational hooks (e.g., "this is wild 🔥")
    _CONVERSATIONAL_STARTS = {
        "i ", "i'", "this ", "what ", "nobody ", "the story ",
        "scenes ", "meet ",
    }
    first_char = hook[0]
    if first_char.isalpha() and first_char.islower():
        hook_lower_start = hook[:12].lower()
        if not any(hook_lower_start.startswith(cs) for cs in _CONVERSATIONAL_STARTS):
            issues.append("Starts with lowercase")

    # 4. Repeated consecutive words
    for i in range(len(words) - 1):
        if words[i].lower() == words[i + 1].lower() and len(words[i]) > 2:
            issues.append(f"Repeated word: '{words[i]}'")
            break

    # 5. Messy ALL-CAPS mixing: hooks like "WOULD YOU USE salesforce slackbot ai?"
    # have a mix of intentional CAPS and lowercase fragments that reads poorly.
    # A good hook is either: (a) all Title Case, (b) ALL CAPS, or (c) has CAPS
    # prefix (BREAKING:) followed by proper-case content.
    # Bad: "IS straightjacket loosens ACTUALLY BAD?" — random CAPS + lowercase.
    if len(words) >= 4:
        caps_words = [w for w in words if w.isupper() and len(w) > 1]
        lower_words = [w for w in words if w.islower() and w.isalpha() and len(w) > 2]
        if caps_words and lower_words:
            # If more than 40% of alphabetic words are ALL-CAPS and more than
            # 30% are all-lowercase, it's a broken template substitution
            alpha_words = [w for w in words if w.isalpha() and len(w) > 1]
            if alpha_words:
                caps_ratio = len(caps_words) / len(alpha_words)
                lower_ratio = len(lower_words) / len(alpha_words)
                if caps_ratio > 0.3 and lower_ratio > 0.2:
                    issues.append(
                        f"Mixed CAPS/lowercase ({len(caps_words)} CAPS + {len(lower_words)} lower)"
                    )

    # 6. Ends with a colon followed by nothing (truncated)
    if hook.rstrip().endswith(":"):
        issues.append("Ends with colon (truncated)")

    # 7. Invalid mixed number units (e.g., "15x BILLION")
    if re.search(r'\b\d+(?:\.\d+)?x\s+(?:BILLION|MILLION|TRILLION)\b', hook, re.IGNORECASE):
        issues.append("Invalid number-unit combination (x + BILLION/MILLION/TRILLION)")

    # 8. Suspicious leading-zero multipliers (e.g., "000x")
    if re.search(r'\b0{2,}\d*x\b', hook, re.IGNORECASE):
        issues.append("Suspicious multiplier token (leading zeros)")

    # 9. Dangling punctuation / stopword endings (common truncation artifacts)
    if re.search(r'[-—–]\s*$', hook):
        issues.append("Ends with dangling dash")
    if words:
        tail = words[-1].lower().strip(".,;:!?—-")
        if tail in {"and", "or", "for", "to", "in", "with", "of", "at", "by", "on", "the", "a", "an"}:
            issues.append(f"Ends with dangling stopword ('{tail}')")

    return len(issues) == 0, issues


def sanitize_hook(hook: str) -> str:
    """Apply mechanical fixes to common hook issues.

    - Strip bracket artifacts
    - Capitalize first letter
    - Remove trailing colons
    - Deduplicate consecutive words
    """
    # Strip bracket artifacts
    hook = _BRACKET_RE.sub("", hook).strip()

    # Remove double spaces left by bracket removal
    hook = re.sub(r'\s{2,}', ' ', hook).strip()

    # Capitalize first letter (preserve emoji leads + conversational starts)
    _CONV_STARTS = {"i ", "i'", "this ", "what ", "nobody ", "the story ",
                    "scenes ", "meet "}
    if hook and hook[0].isalpha() and hook[0].islower():
        hook_test = hook[:12].lower()
        if not any(hook_test.startswith(cs) for cs in _CONV_STARTS):
            hook = hook[0].upper() + hook[1:]

    # Remove trailing colon
    hook = hook.rstrip(":").strip()

    # Deduplicate consecutive words
    words = hook.split()
    deduped = [words[0]] if words else []
    for i in range(1, len(words)):
        if words[i].lower() != words[i - 1].lower() or len(words[i]) <= 2:
            deduped.append(words[i])
    hook = " ".join(deduped)

    return hook


# ══════════════════════════════════════════════════════════════
# 1. Extract hook elements from a story
# ══════════════════════════════════════════════════════════════

def extract_conversational_elements(
    story: Dict[str, Any],
    elements: Dict[str, Any],
) -> Dict[str, Any]:
    """Extract conversational placeholder values for @evolving.ai-style hooks.

    Fills:
      - story_summary:        3-6 word summary of what happened
      - creator_achievement:  what a creator/tool accomplished
      - visual_description:   what was visually impressive
      - surprising_fact:      the unexpected detail
      - old_requirements:     what something used to require
      - now_possible:         what's now easy/accessible
      - role:                 person type (artist, filmmaker, developer)
      - tool:                 specific AI tool used
      - achievement:          what was accomplished

    These are derived from the story title/summary + existing elements.
    """
    title = story.get("title", "")
    summary = story.get("summary", "")
    text = f"{title} {summary}"
    text_lower = text.lower()
    conv: Dict[str, Any] = {}

    # ── story_summary: 3-6 word conversational gist ────────
    # Must read like natural speech after "I needed this 😭"
    # Strategy: strip leading determiners/subjects, lowercase, keep the action
    claim = elements.get("claim", "")
    product = elements.get("product", "")
    company = elements.get("company", "")

    def _make_conversational_summary(raw: str) -> str:
        """Turn a headline fragment into a conversational phrase.

        Goal: output reads naturally after "I needed this 😭 ..."
        e.g., "ai video generation just got real" not "generates photorealistic portraits that"
        """
        # Strip leading noise: "This/These/The/A + noun"
        cleaned = re.sub(
            r'^(?:this|these|the|a|an)\s+',
            '', raw.strip(), flags=re.IGNORECASE,
        ).strip()
        # Lowercase for conversational tone
        words = cleaned.lower().split()[:6]
        # Trim trailing fragments that read broken
        _trailing = {"with", "in", "on", "at", "to", "for", "of", "and", "or",
                     "the", "a", "an", "by", "from", "as", "is", "that",
                     "but", "just", "now", "who", "which", "when", "where",
                     "completely", "really", "very", "so", "its"}
        while len(words) > 2 and words[-1].rstrip(".,;:!?") in _trailing:
            words.pop()
        result = " ".join(words).strip()
        return result if len(result) > 10 else ""

    # Try claim first (already a compact phrase)
    summary = ""
    if claim:
        summary = _make_conversational_summary(claim)

    # Fallback: first clause of title
    if not summary:
        parts = re.split(r'\s*[—–:,]\s*', title, maxsplit=1)
        summary = _make_conversational_summary(parts[0])

    # Fallback: try first sentence of the story summary text
    if not summary and story.get("summary"):
        first_sentence = re.split(r'[.!?]', story["summary"], maxsplit=1)[0].strip()
        summary = _make_conversational_summary(first_sentence)

    # Fallback: product/company + action from summary sentence
    if not summary and (product or company):
        entity = product or company
        # Look for a verb phrase after the entity in the summary
        entity_pat = re.escape(entity.lower())
        m = re.search(
            rf'\b{entity_pat}\b\s+(\w+(?:\s+\w+){{1,4}})',
            text_lower,
        )
        if m:
            summary = f"{entity} {m.group(1)}"
        else:
            summary = f"{entity} just changed the game"

    if not summary:
        summary = "AI just leveled up"

    conv["story_summary"] = summary

    # ── creator_achievement: what was accomplished ──────────
    topic = elements.get("topic", "")

    # Try to build from summary verbs
    _ACHIEVE_PATTERNS = [
        r'(?:created?|generated?|built|made|produced|designed)\s+(.{10,50}?)(?:\.|,|$)',
        r'(?:can now|is now able to|enables?)\s+(.{10,50}?)(?:\.|,|$)',
        r'(?:first(?:-ever)?)\s+(.{10,50}?)(?:\.|,|$)',
    ]
    for pat in _ACHIEVE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            ach = m.group(1).strip().rstrip(".,;:")
            ach_words = ach.split()[:7]
            conv["creator_achievement"] = " ".join(ach_words).lower()
            break
    if "creator_achievement" not in conv and product:
        conv["creator_achievement"] = f"{product} doing something incredible"
    elif "creator_achievement" not in conv and claim:
        conv["creator_achievement"] = claim.lower()

    # ── visual_description: what looks impressive ───────────
    _VISUAL_PATTERNS = [
        r'(?:photorealistic|cinematic|stunning|beautiful|realistic)\s+(.{5,40}?)(?:\.|,|$)',
        r'(?:looks? (?:like|completely)|appears?)\s+(.{10,50}?)(?:\.|,|$)',
        r'(?:video|image|scene|animation|art)\s+(?:of|showing|featuring)\s+(.{10,50}?)(?:\.|,|$)',
    ]
    for pat in _VISUAL_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            vis = m.group(1).strip().rstrip(".,;:")
            conv["visual_description"] = " ".join(vis.split()[:6]).lower()
            break
    if "visual_description" not in conv:
        # Fallback: synthesize from story_summary
        conv["visual_description"] = conv.get("story_summary", topic or "AI-generated content").lower()

    # ── surprising_fact: the unexpected detail ──────────────
    _SURPRISE_PATTERNS = [
        r'(?:surprisingly?|unexpectedly?|shockingly?|remarkably?)[,:]?\s+(.{10,60}?)(?:\.|$)',
        r'(?:for the first time|never before|breakthrough)\s+(.{10,60}?)(?:\.|,|$)',
        r'(?:outperform\w*|surpass\w*|beat\w*)\s+(.{10,50}?)(?:\.|,|$)',
    ]
    for pat in _SURPRISE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            surprise = m.group(1).strip().rstrip(".,;:")
            conv["surprising_fact"] = " ".join(surprise.split()[:8]).lower()
            break
    if "surprising_fact" not in conv:
        conv["surprising_fact"] = conv.get("story_summary", "what AI can do now").lower()

    # ── old_requirements / now_possible (transformation pair) ─
    _TRANSFORM_PATTERNS = [
        (r'(?:used to|once|previously)\s+(?:require|need|take)\s+(.{5,40}?)(?:\.|,|but|now)',
         r'(?:now|can now|today)\s+(.{10,50}?)(?:\.|,|$)'),
    ]
    for old_pat, new_pat in _TRANSFORM_PATTERNS:
        m_old = re.search(old_pat, text, re.IGNORECASE)
        m_new = re.search(new_pat, text, re.IGNORECASE)
        if m_old:
            conv["old_requirements"] = " ".join(m_old.group(1).strip().split()[:5]).lower()
        if m_new:
            conv["now_possible"] = " ".join(m_new.group(1).strip().split()[:6]).lower()
    # Fallback for transformation hooks
    if "old_requirements" not in conv:
        conv["old_requirements"] = "a full team and months of work"
    if "now_possible" not in conv:
        if product:
            conv["now_possible"] = f"be done with {product} in minutes"
        else:
            conv["now_possible"] = "be done with AI in minutes"

    # ── role: person type ───────────────────────────────────
    _ROLE_MAP = {
        "artist": "artist", "filmmaker": "filmmaker", "director": "filmmaker",
        "developer": "developer", "programmer": "developer", "coder": "developer",
        "designer": "designer", "musician": "musician", "composer": "musician",
        "writer": "writer", "researcher": "researcher", "scientist": "researcher",
        "photographer": "photographer", "animator": "animator",
        "creator": "creator", "engineer": "engineer", "student": "student",
    }
    for keyword, role in _ROLE_MAP.items():
        if keyword in text_lower:
            conv["role"] = role
            break
    if "role" not in conv:
        conv["role"] = "creator"

    # ── tool: specific AI tool used ─────────────────────────
    if product:
        conv["tool"] = product
    elif company:
        conv["tool"] = f"{company}'s AI"
    else:
        conv["tool"] = "AI"

    # ── achievement: what was accomplished ───────────────────
    conv["achievement"] = conv.get("creator_achievement", conv.get("story_summary", "something incredible"))

    return conv


def extract_hook_elements(story: Dict[str, Any]) -> Dict[str, Any]:
    """Extract key elements from a story for hook formula matching.

    Identifies:
      - company:    Primary company/org mentioned
      - product:    Specific product/model name
      - number:     Most impactful number (multiplier > milestone > percentage)
      - metric:     What the number measures
      - claim:      Core announcement in ≤8 words
      - comparison: What it's being compared to
      - topic:      General topic noun phrase (2-4 words)
      - story_summary, creator_achievement, visual_description, etc. (conversational)

    Args:
        story: Dict with title, summary, and optionally source, tags.

    Returns:
        Dict of extracted elements (only non-empty values included).
    """
    title = story.get("title", "")
    summary = story.get("summary", "")
    text = f"{title} {summary}"
    text_lower = text.lower()

    elements: Dict[str, Any] = {}

    # ── Company extraction ────────────────────────────────
    companies = _extract_named_entities(text_lower, KNOWN_COMPANIES)
    if companies:
        # Prefer company in title, then first in text
        title_companies = _extract_named_entities(title.lower(), KNOWN_COMPANIES)
        elements["company"] = title_companies[0] if title_companies else companies[0]

    # ── Product extraction ────────────────────────────────
    products = _extract_named_entities(text_lower, KNOWN_PRODUCTS)
    if products:
        title_products = _extract_named_entities(title.lower(), KNOWN_PRODUCTS)
        chosen_product = title_products[0] if title_products else products[0]
        # Avoid product == company duplication (e.g., Midjourney is both)
        if chosen_product != elements.get("company", ""):
            elements["product"] = chosen_product
        elif len(products) > 1:
            # Try second product if first is same as company
            for p in (title_products or products):
                if p != elements.get("company", ""):
                    elements["product"] = p
                    break
            else:
                elements["product"] = chosen_product  # keep it anyway

    # ── Number extraction ─────────────────────────────────
    numbers = _extract_numbers(text)
    if numbers:
        # Filter: always skip standalone years (2025, 2026, etc.) as primary number
        # Years make terrible hook fill-ins ("2026 DALL-E Tricks Nobody Knows")
        non_year_numbers = [
            n for n in numbers
            if not (n["type"] == "plain" and re.match(r'^20\d{2}$', n["display"]))
        ]
        # Filter out malformed parser artifacts (e.g., "000x")
        clean_numbers = [n for n in non_year_numbers if _is_valid_number_display(n["display"])]
        if not clean_numbers:
            # Only years found — skip number element entirely
            elements["all_numbers"] = numbers
            numbers = []  # Signal: no usable number
        else:
            numbers = clean_numbers
        if numbers:
            elements["number"] = numbers[0]["display"]
            # Clean up metric context — trim to 2-3 meaningful words
            raw_metric = numbers[0].get("context", "")
            if raw_metric:
                metric_words = raw_metric.split()[:3]
                while metric_words and metric_words[-1].lower().strip(".,;:!?") in {
                    "and", "or", "for", "to", "in", "with", "of", "at", "by", "on", "the", "a", "an",
                }:
                    metric_words.pop()
                elements["metric"] = " ".join(metric_words)
            else:
                elements["metric"] = ""
            elements["all_numbers"] = numbers
            # Flag billion-scale numbers for billion-specific formulas.
            # IMPORTANT: choose the actual billion candidate, not numbers[0]
            # (which is often a multiplier like "15x").
            billion_candidates = [n for n in numbers if _is_billion_candidate(n)]
            if billion_candidates:
                elements["billion_number"] = _normalize_billion_number(billion_candidates[0])

    # ── Comparison extraction ─────────────────────────────
    comparison = _extract_comparison(text)
    if comparison:
        elements["comparison"] = comparison

    # ── Claim extraction (core announcement ≤5 words) ──────
    claim = _extract_claim(title, summary)
    if claim:
        # Strip leading company/product name to avoid duplication in patterns
        # like "🚨 BREAKING: {company} just {claim}" → prevents
        # "Anthropic just Anthropic launches..."
        claim_stripped = claim
        for entity_key in ("company", "product"):
            entity_val = elements.get(entity_key, "")
            if entity_val and claim_stripped.lower().startswith(entity_val.lower()):
                claim_stripped = claim_stripped[len(entity_val):].lstrip(" ,.:-–—")
        # Cap to 5 words (patterns add 3-4 prefix words already)
        claim_words = claim_stripped.split()
        if len(claim_words) > 5:
            # Trim trailing stop words
            trailing_stop = {"with", "in", "on", "at", "to", "for", "of", "and", "or",
                             "the", "a", "an", "by", "from", "as", "is", "that"}
            while len(claim_words) > 3 and claim_words[-1].lower().rstrip(".,;:") in trailing_stop:
                claim_words.pop()
            claim_stripped = " ".join(claim_words[:5])
        elements["claim"] = claim_stripped if claim_stripped else claim

    # ── Topic extraction (general noun phrase) ────────────
    # Pass already-extracted entities to avoid duplication (e.g., topic != company)
    topic = _extract_topic_phrase(title, exclude_entities=set(
        v for k, v in elements.items() if k in ("company", "product") and v
    ))
    if topic:
        elements["topic"] = topic

    # ── Short title (fallback) ────────────────────────────
    elements["short_title"] = _shorten_title(title, max_words=6)

    # ── Conversational elements (@evolving.ai style) ────────
    conv = extract_conversational_elements(story, elements)
    elements.update(conv)

    return elements


def _extract_named_entities(
    text_lower: str,
    entity_map: Dict[str, str],
) -> List[str]:
    """Find known entities in text, returning canonical names in order of appearance.

    Prefers longer matches (e.g., 'claude opus 4' over 'claude') to avoid
    losing specificity. Deduplicates by canonical name.
    """
    # Sort keys longest-first so "claude opus 4" matches before "claude"
    sorted_keys = sorted(entity_map.keys(), key=len, reverse=True)

    found_positions: List[Tuple[int, str]] = []
    matched_spans: List[Tuple[int, int]] = []  # avoid overlapping matches

    for key in sorted_keys:
        idx = text_lower.find(key)
        if idx >= 0:
            # Check for overlap with already-matched spans
            end = idx + len(key)
            overlaps = any(
                not (end <= s_start or idx >= s_end)
                for s_start, s_end in matched_spans
            )
            if not overlaps:
                canonical = entity_map[key]
                if canonical not in [c for _, c in found_positions]:
                    found_positions.append((idx, canonical))
                    matched_spans.append((idx, end))

    # Sort by position in text
    found_positions.sort(key=lambda x: x[0])
    return [name for _, name in found_positions]


def _extract_numbers(text: str) -> List[Dict[str, str]]:
    """Extract significant numbers from text with context.

    Returns list of {display, raw, context, type} sorted by impact.
    """
    results = []

    for pattern, formatter in _NUMBER_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            display = formatter(match)
            # Get surrounding context (5 words after the number)
            end_pos = match.end()
            after = text[end_pos:end_pos + 60].strip()
            context_words = after.split()[:5]
            context = " ".join(context_words).strip(".,;:!?")

            # Determine type for sorting
            if "x" in display.lower():
                num_type = "multiplier"
                impact = 3
            elif any(c in display for c in "MBT"):
                num_type = "milestone"
                impact = 2
            elif "%" in display:
                num_type = "percentage"
                impact = 1
            elif display.startswith("$"):
                num_type = "money"
                impact = 2
            else:
                num_type = "plain"
                impact = 0

            results.append({
                "display": display,
                "raw": match.group(0),
                "context": context,
                "type": num_type,
                "impact": impact,
            })

    # Sort by impact (highest first), dedupe
    results.sort(key=lambda x: x["impact"], reverse=True)
    seen = set()
    deduped = []
    for r in results:
        if r["display"] not in seen:
            deduped.append(r)
            seen.add(r["display"])
    return deduped


def _is_valid_number_display(display: str) -> bool:
    """Return False for malformed numeric artifacts from noisy extraction."""
    cleaned = (display or "").strip().upper()
    if not cleaned:
        return False
    return not bool(_SUSPICIOUS_NUMBER_RE.fullmatch(cleaned))


def _is_billion_candidate(number_entry: Dict[str, str]) -> bool:
    """Detect whether an extracted number is billion/trillion scale."""
    display = (number_entry.get("display") or "").strip()
    context = (number_entry.get("context") or "").lower()

    # Explicit B/T suffix (e.g., 50B, $12T)
    if re.search(r'\$?\d+(?:\.\d+)?[BT]\b', display, re.IGNORECASE):
        return True

    # Dollar amount with nearby "billion/trillion" wording (e.g., "$50 billion")
    if display.startswith("$") and _BILLION_CONTEXT_RE.search(context):
        return True

    return False


def _normalize_billion_number(number_entry: Dict[str, str]) -> str:
    """Normalize billion-scale number display for hook templates."""
    display = (number_entry.get("display") or "").strip()
    context = (number_entry.get("context") or "").lower()

    # Already explicit (e.g., $5B, 10B, 1.2T)
    if re.search(r'\$?\d+(?:\.\d+)?[BT]\b', display, re.IGNORECASE):
        return display

    # If context says billion/trillion, suffix bare dollar numbers.
    if display.startswith("$") and _BILLION_CONTEXT_RE.search(context):
        suffix = "T" if "trillion" in context or "tn" in context else "B"
        amount = display.lstrip("$")
        if re.fullmatch(r'\d+(?:\.\d+)?', amount):
            return f"${amount}{suffix}"

    return display


def _extract_comparison(text: str) -> Optional[str]:
    """Extract comparison target from text (e.g., 'better than GPT-4')."""
    patterns = [
        r'(?:vs\.?|versus|compared to|better than|beats?|outperform\w*)\s+(\w[\w\s-]{1,30})',
        r'(\w[\w\s-]{1,20})\s+(?:vs\.?|versus)\s+',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw = match.group(1).strip()
            # Try to resolve to a known product
            raw_lower = raw.lower()
            for key, canonical in KNOWN_PRODUCTS.items():
                if key in raw_lower:
                    return canonical
            # Also allow known companies as comparison targets.
            for key, canonical in KNOWN_COMPANIES.items():
                if key in raw_lower:
                    return canonical
            # Return raw only when it looks like a named entity, not a verb phrase.
            words = raw.split()[:4]
            candidate = " ".join(words)
            if any(ch.isupper() for ch in candidate) or any(ch.isdigit() for ch in candidate):
                return candidate
            return None
    return None


def _extract_claim(title: str, summary: str) -> Optional[str]:
    """Extract the core claim/announcement in ≤8 words.

    Strategy: Try clean clause extraction first, then smart truncation
    that ends on a meaningful word boundary (not mid-phrase).
    """
    # Try title first — it's usually the claim itself
    words = title.split()
    if len(words) <= 8:
        return title

    # Strip common prefixes
    cleaned = re.sub(
        r'^(breaking:?\s*|just in:?\s*|report:?\s*|exclusive:?\s*)',
        '', title, flags=re.IGNORECASE,
    ).strip()

    # Take first clause (before comma, dash, or colon)
    for delim in [' — ', ' – ', ', ', ': ']:
        if delim in cleaned:
            first_part = cleaned.split(delim)[0].strip()
            if 3 <= len(first_part.split()) <= 8:
                return first_part

    # Smart truncation: find the last meaningful word boundary at ≤8 words
    # Remove trailing prepositions/articles that leave the phrase incomplete
    trailing_stop = {"with", "in", "on", "at", "to", "for", "of", "and", "or",
                     "the", "a", "an", "by", "from", "as", "is", "that"}
    words = cleaned.split()[:8]
    while len(words) > 3 and words[-1].lower().rstrip(".,;:") in trailing_stop:
        words.pop()

    return " ".join(words)


def _extract_topic_phrase(
    title: str,
    exclude_entities: Optional[Set[str]] = None,
) -> Optional[str]:
    """Extract a 2-3 word topic noun phrase from title.

    Prefers product names > company names > domain-specific topic > cleaned noun phrase.
    Keeps it SHORT for hook formulas (2-3 words ideal).

    Args:
        title:            Story title.
        exclude_entities: Set of entity names to skip (avoids topic=company duplication).
    """
    exclude = exclude_entities or set()
    exclude_lower = {e.lower() for e in exclude}

    # Strip prefixes
    cleaned = re.sub(
        r'^(breaking:?\s*|just in:?\s*|report:?\s*|the\s+)',
        '', title, flags=re.IGNORECASE,
    ).strip()

    title_lower = cleaned.lower()

    # Prefer longer product matches first (sorted by key length desc)
    for key in sorted(KNOWN_PRODUCTS.keys(), key=len, reverse=True):
        if key in title_lower:
            canonical = KNOWN_PRODUCTS[key]
            if canonical.lower() not in exclude_lower:
                return canonical

    for key in sorted(KNOWN_COMPANIES.keys(), key=len, reverse=True):
        if key in title_lower:
            canonical = KNOWN_COMPANIES[key]
            if canonical.lower() not in exclude_lower:
                return canonical

    # Domain-specific topic phrases (common AI topics)
    domain_topics = [
        ("ai act", "AI Regulation"),
        ("regulation", "AI Regulation"),
        ("ai coding", "AI Coding"),
        ("ai-generated code", "AI Coding"),
        ("drug discovery", "AI Medicine"),
        ("cancer treatment", "AI Medicine"),
        ("ai art", "AI Art"),
        ("ai image", "AI Images"),
        ("ai video", "AI Video"),
        ("ai agents", "AI Agents"),
        ("research agent", "AI Research"),
        ("ai search", "AI Search"),
        ("ai safety", "AI Safety"),
        ("ai jobs", "AI Jobs"),
        ("open source", "Open-Source AI"),
        ("watermark", "AI Watermarks"),
        ("benchmark", "AI Benchmarks"),
        ("developer survey", "AI Development"),
    ]
    for keyword, topic in domain_topics:
        if keyword in title_lower:
            return topic

    # Fall back to first 2-3 significant words (noun phrase)
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "has", "have",
                  "in", "on", "at", "to", "for", "of", "with", "and", "or",
                  "that", "this", "can", "now", "will", "just", "new",
                  "passes", "releases", "launches", "finds", "reaches",
                  "comprehensive", "all"}
    words = [w for w in cleaned.split()[:5] if w.lower() not in stop_words]
    if len(words) >= 2:
        return " ".join(words[:2])  # Keep to 2 words max for clean fills

    return cleaned.split()[0] if cleaned else None


def _shorten_title(title: str, max_words: int = 6) -> str:
    """Shorten title to max_words, cleaning jargon."""
    words = title.split()[:max_words]
    return " ".join(words)


def select_best_category(story: Dict[str, Any]) -> str:
    """Select the best hook category for a story using multi-signal scoring.

    Instead of first-match if/elif, scores EVERY category and returns the
    highest. This handles stories that straddle multiple categories correctly
    (e.g., a breaking announcement with big numbers).

    Signals used:
      - Text keywords (title + summary)
      - Domain (official blogs score higher for breaking)
      - Recency via published_at (fresher → breaking/reaction)
      - Number presence (dollar amounts, large figures → milestone)
      - Multi-entity presence (2+ products/companies → comparison)

    Returns:
        Category name. Falls back to 'curiosity' if no signals match,
        since curiosity-gap hooks are the safest default for engagement.
    """
    title = story.get("title", "")
    summary = story.get("summary", "")
    text = f"{title} {summary}"
    text_lower = text.lower()
    url = story.get("url", "")
    domain = story.get("domain", "")

    # ── Compute age in hours from published_at ─────────────
    age_hours: Optional[float] = None
    published_at = story.get("published_at")
    if published_at:
        try:
            from datetime import datetime, timezone
            if isinstance(published_at, str):
                # Handle ISO format with various timezone formats
                cleaned = published_at.replace("Z", "+00:00")
                pub_dt = datetime.fromisoformat(cleaned)
            else:
                pub_dt = published_at
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
        except (ValueError, TypeError):
            age_hours = None

    # ── Check for large numbers and dollar amounts ─────────
    has_dollar = bool(re.search(r'\$\s*\d+(?:\.\d+)?\s*[BMTbmt]', text))
    has_large_number = bool(re.search(
        r'\b\d+(?:\.\d+)?\s*(?:billion|million|trillion|[BMT])\b', text, re.IGNORECASE
    ))
    bool(re.search(r'\d', text))

    # ── Count distinct entities (for comparison detection) ──
    entities_found: Set[str] = set()
    for key, canonical in KNOWN_PRODUCTS.items():
        if key in text_lower:
            entities_found.add(canonical)
    for key, canonical in KNOWN_COMPANIES.items():
        if key in text_lower:
            entities_found.add(canonical)
    multi_entity = len(entities_found) >= 2

    # ── Official blog domains (stronger breaking signal) ────
    _OFFICIAL_DOMAINS = {
        "openai.com", "anthropic.com", "blog.google", "ai.meta.com",
        "blogs.nvidia.com", "deepmind.google", "mistral.ai",
    }
    is_official = any(d in domain or d in url for d in _OFFICIAL_DOMAINS)

    # ── Score each category ─────────────────────────────────
    # Each keyword hit adds its weight. Structural signals add flat bonuses.
    # Highest total wins.

    scores: Dict[str, float] = {}

    # ── BREAKING (35% of competitive content) ──────────────
    breaking = 0.0
    _BREAK_KW = {
        "announced": 0.3, "released": 0.3, "launched": 0.3, "unveiled": 0.3,
        "introduces": 0.25, "drops": 0.2, "just dropped": 0.35,
        "now available": 0.2, "rolls out": 0.2, "ships": 0.2,
    }
    for kw, weight in _BREAK_KW.items():
        if kw in text_lower:
            breaking += weight
    if is_official:
        breaking += 0.3  # official source = strong breaking signal
    if age_hours is not None and age_hours < 6:
        breaking += 0.25  # very fresh
    elif age_hours is not None and age_hours < 24:
        breaking += 0.10  # moderately fresh
    scores["breaking"] = breaking

    # ── MILESTONE / BIG NUMBERS (20%) ──────────────────────
    milestone = 0.0
    _MILE_KW = {
        "investment": 0.2, "funding": 0.2, "valuation": 0.25,
        "users": 0.15, "reaches": 0.2, "hits": 0.15,
        "milestone": 0.3, "record": 0.2, "all-time": 0.2,
        "surpass": 0.2, "billion users": 0.3, "million users": 0.25,
        "weekly active": 0.15, "benchmark": 0.15,
    }
    for kw, weight in _MILE_KW.items():
        if kw in text_lower:
            milestone += weight
    if has_dollar:
        milestone += 0.35  # $XB is a strong milestone signal
    if has_large_number:
        milestone += 0.25
    scores["milestone"] = milestone

    # ── REACTION / AWE (15%) ───────────────────────────────
    reaction = 0.0
    _REACT_KW = {
        "breakthrough": 0.3, "first-ever": 0.3, "revolutionary": 0.25,
        "unprecedented": 0.25, "incredible": 0.2, "unbelievable": 0.25,
        "mind-blowing": 0.3, "jaw-dropping": 0.3, "shocking": 0.2,
        "insane": 0.2, "out-performed": 0.2, "outperformed": 0.2,
        "can't believe": 0.25, "scary good": 0.25, "scary accurate": 0.2,
        "blew my mind": 0.3, "game-changing": 0.2, "groundbreaking": 0.2,
    }
    for kw, weight in _REACT_KW.items():
        if kw in text_lower:
            reaction += weight
    # Positive capability stories about AI models boost reaction
    if any(w in text_lower for w in ["can now", "is now able", "for the first time"]):
        reaction += 0.15
    scores["reaction"] = reaction

    # ── CURIOSITY GAP (15%) ────────────────────────────────
    curiosity = 0.0
    _CURIO_KW = {
        "secret": 0.3, "hidden": 0.25, "quietly": 0.3,
        "nobody knows": 0.3, "no one noticed": 0.3,
        "under the radar": 0.25, "haven't heard": 0.2,
        "overlooked": 0.2, "little-known": 0.2, "sleeper": 0.2,
    }
    for kw, weight in _CURIO_KW.items():
        if kw in text_lower:
            curiosity += weight
    scores["curiosity"] = curiosity

    # ── CONTROVERSY / CONTRARIAN (3%) ──────────────────────
    controversy = 0.0
    _CONTR_KW = {
        "wrong": 0.25, "myth": 0.3, "truth": 0.25, "actually": 0.2,
        "overrated": 0.25, "overhyped": 0.25, "regulation": 0.2,
        "enforce": 0.15, "ban": 0.2, "fines": 0.15,
        "concerns": 0.15, "dangerous": 0.2, "replaces": 0.15,
        "lawsuit": 0.2, "ethical": 0.15, "bias": 0.15,
    }
    for kw, weight in _CONTR_KW.items():
        if kw in text_lower:
            controversy += weight
    scores["controversy"] = controversy

    # ── COMPARISON (2%) ────────────────────────────────────
    comparison = 0.0
    _COMP_KW = {
        " vs ": 0.4, " versus ": 0.4, "compared to": 0.3,
        "better than": 0.3, "head to head": 0.35, "who wins": 0.35,
        "outperforms": 0.25, "beats": 0.2, "benchmark": 0.15,
    }
    for kw, weight in _COMP_KW.items():
        if kw in text_lower:
            comparison += weight
    if multi_entity:
        comparison += 0.25  # multiple products → likely comparison
    scores["comparison"] = comparison

    # ── TUTORIAL (inherits from existing) ──────────────────
    tutorial = 0.0
    _TUT_KW = {
        "hidden features": 0.3, "tricks": 0.25, "tips": 0.2,
        "how to": 0.3, "tutorial": 0.3, "guide": 0.2,
        "features in": 0.15, "step-by-step": 0.2, "walkthrough": 0.2,
    }
    for kw, weight in _TUT_KW.items():
        if kw in text_lower:
            tutorial += weight
    scores["tutorial"] = tutorial

    # ── PREDICTION (10%) ───────────────────────────────────
    prediction = 0.0
    _PRED_KW = {
        "predicts": 0.3, "prediction": 0.3, "by end of": 0.2,
        "about to": 0.2, "timeline": 0.2, "will": 0.1,
        "future of": 0.2, "coming soon": 0.15, "roadmap": 0.2,
        "in 2027": 0.15, "in 2028": 0.15, "in 2029": 0.15,
    }
    for kw, weight in _PRED_KW.items():
        if kw in text_lower:
            prediction += weight
    # "can", "should" as question-style prediction signals
    if any(w in text_lower for w in ["can ai", "will ai", "should we"]):
        prediction += 0.15
    scores["prediction"] = prediction

    # ── EMOTIONAL REACTION (conversational) ────────────────
    emotional = 0.0
    _EMOTE_KW = {
        "impressive": 0.25, "beautiful": 0.25, "stunning": 0.3,
        "incredible": 0.25, "amazing": 0.3, "heartwarming": 0.3,
        "creative": 0.2, "insane": 0.2, "wild": 0.2,
        "mind-blowing": 0.3, "jaw-dropping": 0.25, "blew my mind": 0.3,
        "can't believe": 0.25, "unreal": 0.2, "obsessed": 0.3,
    }
    for kw, weight in _EMOTE_KW.items():
        if kw in text_lower:
            emotional += weight
    # Creator content boosts emotional hooks
    if any(w in text_lower for w in ["generated", "created", "made with", "built with"]):
        emotional += 0.20
    if any(w in text_lower for w in ["art", "video", "film", "music", "image", "visual"]):
        emotional += 0.15
    scores["emotional_reaction"] = emotional

    # ── ALERT / CONTRAST (conversational) ────────────────────
    alert_contrast = 0.0
    _ALERT_KW = {
        "never existed": 0.4, "doesn't exist": 0.35, "not real": 0.35,
        "looks real": 0.3, "looks completely": 0.25, "indistinguishable": 0.35,
        "but it": 0.15, "but actually": 0.2, "but nobody": 0.25,
        "nobody noticed": 0.3, "quietly": 0.25, "under the radar": 0.2,
    }
    for kw, weight in _ALERT_KW.items():
        if kw in text_lower:
            alert_contrast += weight
    scores["alert_contrast"] = alert_contrast

    # ── STORYTELLING (narrative) ─────────────────────────────
    storytelling = 0.0
    _STORY_KW = {
        "creator": 0.25, "artist": 0.25, "filmmaker": 0.25,
        "developer": 0.2, "designer": 0.2, "student": 0.2,
        "went viral": 0.35, "blowing up": 0.3, "trending": 0.2,
        "used ai to": 0.3, "used ai for": 0.25, "built with ai": 0.25,
        "one person": 0.2, "solo": 0.15, "without a team": 0.2,
    }
    for kw, weight in _STORY_KW.items():
        if kw in text_lower:
            storytelling += weight
    scores["storytelling"] = storytelling

    # ── TRANSFORMED REALITY ──────────────────────────────────
    transformed = 0.0
    _TRANSFORM_KW = {
        "used to": 0.3, "once required": 0.3, "previously": 0.15,
        "democratiz": 0.3, "accessible": 0.25, "anyone can": 0.25,
        "no longer need": 0.3, "eliminates the need": 0.3,
        "what took": 0.25, "in minutes": 0.2, "in seconds": 0.25,
        "fraction of the cost": 0.3, "free alternative": 0.25,
    }
    for kw, weight in _TRANSFORM_KW.items():
        if kw in text_lower:
            transformed += weight
    scores["transformed_reality"] = transformed

    # ── INTRIGUE / PROBLEM ───────────────────────────────────
    intrigue = 0.0
    _INTRIGUE_KW = {
        "nobody is talking": 0.35, "overlooked": 0.25,
        "detached from reality": 0.35, "faster than expected": 0.3,
        "underestimated": 0.25, "sleeper": 0.2, "hidden": 0.2,
        "real cost": 0.25, "what they don't tell": 0.3,
        "accelerating": 0.2, "exponential": 0.2,
    }
    for kw, weight in _INTRIGUE_KW.items():
        if kw in text_lower:
            intrigue += weight
    scores["intrigue"] = intrigue

    # ── Pick winner (highest score, fallback = emotional_reaction) ──
    if not any(v > 0 for v in scores.values()):
        logger.debug("No category signals — defaulting to emotional_reaction")
        return "emotional_reaction"

    best_cat = max(scores, key=lambda k: scores[k])
    best_score = scores[best_cat]

    # Log the decision for debugging
    top_3 = sorted(scores.items(), key=lambda x: -x[1])[:3]
    logger.debug(
        "Category selected: %s (%.2f) | top-3: %s",
        best_cat, best_score,
        ", ".join(f"{c}={s:.2f}" for c, s in top_3),
    )

    return best_cat


# ══════════════════════════════════════════════════════════════
# 2. Match best formula
# ══════════════════════════════════════════════════════════════

_FORMULAS_CACHE: Optional[Dict] = None


def _load_formulas() -> Dict:
    """Load hook formulas from config/hook_formulas.yaml (cached)."""
    global _FORMULAS_CACHE
    if _FORMULAS_CACHE is not None:
        return _FORMULAS_CACHE

    config_path = PROJECT_ROOT / "config" / "hook_formulas.yaml"
    if not config_path.exists():
        logger.warning("hook_formulas.yaml not found — using empty formulas")
        _FORMULAS_CACHE = {"formulas": [], "category_priority": {}, "scoring_weights": {}}
        return _FORMULAS_CACHE

    with open(config_path) as f:
        _FORMULAS_CACHE = yaml.safe_load(f) or {}

    logger.debug("Loaded %d hook formulas", len(_FORMULAS_CACHE.get("formulas", [])))
    return _FORMULAS_CACHE


def match_best_formula(
    story: Dict[str, Any],
    elements: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Find the highest-scoring formula that the story's elements can fill.

    Scoring:
      1. Category priority (breaking > milestone > comparison > ...)
      2. Element coverage (how many placeholders can be filled)
      3. Base engagement score from the formula

    Args:
        story:    Story dict.
        elements: Extracted elements from extract_hook_elements().

    Returns:
        Best-matching formula dict, or None if no formula matches.
    """
    config = _load_formulas()
    formulas = config.get("formulas", [])
    category_priority = config.get("category_priority", {})

    if not formulas:
        return None

    scored_formulas = []
    for formula in formulas:
        trigger = set(formula.get("trigger_elements", []))
        available = set(elements.keys())

        # Check if all required elements are available
        if not trigger.issubset(available):
            continue

        # Check that filled values are non-empty
        all_filled = all(elements.get(t, "") for t in trigger)
        if not all_filled:
            continue

        # Score: category priority + engagement + element coverage
        cat = formula.get("category", "universal")
        cat_score = category_priority.get(cat, 0)
        engagement = formula.get("engagement_score", 0.5)
        coverage = len(trigger) / max(len(_extract_placeholders(formula["pattern"])), 1)

        total_score = cat_score * 0.3 + engagement * 0.5 + coverage * 0.2
        scored_formulas.append((total_score, formula))

    if not scored_formulas:
        return None

    scored_formulas.sort(key=lambda x: x[0], reverse=True)
    return scored_formulas[0][1]


def _extract_placeholders(pattern: str) -> List[str]:
    """Extract {placeholder} names from a pattern string."""
    return re.findall(r'\{(\w+)\}', pattern)


def _formula_elements_compatible(formula_id: str, subs: Dict[str, str]) -> bool:
    """Formula-specific compatibility checks for filled placeholder values."""
    number_val = str(subs.get("number", "")).strip()
    billion_val = str(subs.get("billion_number", "")).strip()

    # "X users" hooks need user-count numbers, not multipliers/percentages.
    if formula_id == "NUM_EMOJI_USERS" and number_val:
        if re.search(r'[xX%]', number_val):
            return False

    # Billion formula should only run with explicit B/T scale numbers.
    if formula_id == "NUM_EMOJI_BILLION" and billion_val:
        if not re.search(r'\$?\d+(?:\.\d+)?[BT]\b', billion_val, re.IGNORECASE):
            return False

    # Timeline hooks should use temporal numbers (years/months), not multipliers or money.
    if formula_id == "PRED_TIMELINE" and number_val:
        if re.search(r'[xX%$]', number_val):
            return False

    return True


# ══════════════════════════════════════════════════════════════
# 3. Generate hook variants
# ══════════════════════════════════════════════════════════════

def generate_hook_variants(
    story: Dict[str, Any],
    count: int = 5,
    max_words: int = 14,
) -> List[Dict[str, Any]]:
    """Generate multiple hook variants for a story using formula matching.

    Strategy:
      1. Extract story elements (companies, products, numbers, claims)
         + conversational elements (story_summary, creator_achievement, etc.)
      2. Try each matching formula (conversational categories prioritized)
      3. Fill in placeholders with extracted elements
      4. Generate up to `count` unique variants
      5. If fewer than `count` match, add conversational fallbacks

    Args:
        story:     Story dict with title, summary, etc.
        count:     Number of variants to generate (default: 5).
        max_words: Maximum words per hook (14 for conversational style).

    Returns:
        List of {hook, formula_id, elements_used, raw_score} dicts.
    """
    elements = extract_hook_elements(story)
    config = _load_formulas()
    formulas = config.get("formulas", [])
    category_priority = config.get("category_priority", {})

    variants: List[Dict[str, Any]] = []
    seen_hooks: Set[str] = set()

    # Select best category using multi-signal scoring
    story_category = select_best_category(story)

    # Score and sort all matching formulas
    scored_formulas = []
    for formula in formulas:
        trigger = set(formula.get("trigger_elements", []))
        available = set(elements.keys())

        if not trigger.issubset(available):
            continue

        all_filled = all(elements.get(t, "") for t in trigger)
        if not all_filled:
            continue

        cat = formula.get("category", "universal")
        cat_score = category_priority.get(cat, 0)
        engagement = formula.get("engagement_score", 0.5)

        # Category relevance bonus: formulas matching story type get a boost
        relevance_bonus = 0.0
        if story_category and cat == story_category:
            relevance_bonus = 0.25  # Strong boost for matching category

        total = cat_score * 0.25 + engagement * 0.60 + relevance_bonus + len(trigger) * 0.05

        scored_formulas.append((total, formula))

    scored_formulas.sort(key=lambda x: x[0], reverse=True)

    # Fill formulas with elements
    for score, formula in scored_formulas:
        if len(variants) >= count:
            break

        pattern = formula["pattern"]
        placeholders = _extract_placeholders(pattern)

        # Conversational categories use lowercase fills
        _CONVERSATIONAL_CATS = {
            "emotional_reaction", "alert_contrast", "storytelling",
            "transformed_reality", "intrigue",
        }
        is_conv = formula.get("category", "") in _CONVERSATIONAL_CATS

        # Build substitution dict
        subs = {}
        for ph in placeholders:
            val = elements.get(ph, "")
            if not val:
                break
            val_str = str(val)
            if is_conv:
                # Preserve proper nouns (companies/products) but lowercase the rest
                # Known entities stay as-is, everything else goes lowercase
                is_entity = (
                    val_str in KNOWN_COMPANIES.values()
                    or val_str in KNOWN_PRODUCTS.values()
                )
                if not is_entity:
                    val_str = val_str.lower()
            subs[ph] = val_str
        else:
            if not _formula_elements_compatible(formula.get("id", ""), subs):
                continue
            # All placeholders filled
            try:
                hook = pattern.format(**subs)
            except (KeyError, IndexError):
                continue

            # Sanitize: fix bracket artifacts, capitalize, dedup words
            hook = sanitize_hook(hook)

            # Enforce word limit — use formula's own max_words if specified
            formula_max = formula.get("max_words", max_words)
            effective_max = formula_max if formula_max else max_words
            hook_words = hook.split()
            if len(hook_words) > effective_max:
                continue

            # Validate grammar — skip broken hooks entirely
            is_valid, grammar_issues = validate_hook_grammar(hook)
            if not is_valid:
                logger.debug("Skipping invalid hook '%s': %s", hook[:40], grammar_issues)
                continue

            hook_normalized = hook.upper().strip()
            if hook_normalized not in seen_hooks:
                seen_hooks.add(hook_normalized)
                variants.append({
                    "hook": hook,
                    "formula_id": formula.get("id", "unknown"),
                    "category": formula.get("category", "unknown"),
                    "elements_used": list(subs.keys()),
                    "raw_score": formula.get("engagement_score", 0.5),
                })

    # ── Category diversity guarantee ──────────────────────
    # If the detected story category has zero hooks in variants,
    # inject the best category-matched formula (replacing the weakest
    # non-matching variant). This ensures curiosity/reaction/etc.
    # hooks surface even when high-priority categories dominate.
    if story_category and story_category != "universal":
        has_category_hook = any(
            v["category"] == story_category for v in variants
        )
        if not has_category_hook and len(variants) >= count:
            # Find best unused formula matching story_category
            for _score, formula in scored_formulas:
                if formula.get("category") != story_category:
                    continue
                fid = formula.get("id", "")
                if any(v["formula_id"] == fid for v in variants):
                    continue
                # Try to fill it
                pattern = formula["pattern"]
                phs = _extract_placeholders(pattern)
                cat_subs = {}
                for ph in phs:
                    val = elements.get(ph, "")
                    if not val:
                        break
                    cat_subs[ph] = str(val)
                else:
                    if not _formula_elements_compatible(fid, cat_subs):
                        continue
                    try:
                        cat_hook = pattern.format(**cat_subs)
                    except (KeyError, IndexError):
                        continue
                    cat_hook = sanitize_hook(cat_hook)
                    formula_max = formula.get("max_words", max_words)
                    effective_max = formula_max if formula_max else max_words
                    hw = cat_hook.split()
                    if len(hw) > effective_max:
                        continue
                    # Validate before injecting
                    cat_valid, _ = validate_hook_grammar(cat_hook)
                    if not cat_valid:
                        continue
                    # Replace lowest-scored non-matching variant
                    variants[-1] = {
                        "hook": cat_hook,
                        "formula_id": fid,
                        "category": story_category,
                        "elements_used": list(cat_subs.keys()),
                        "raw_score": formula.get("engagement_score", 0.5),
                    }
                    logger.debug(
                        "Diversity inject: replaced weakest with %s (%s)",
                        fid, story_category,
                    )
                    break

    # ── Fallback variants (conversational style) ──────────
    short_title = elements.get("short_title", story.get("title", "")[:40])
    story_summary = elements.get("story_summary", short_title.lower())
    product = elements.get("product", "")
    fallbacks = [
        (f"This is wild 🔥 {story_summary}", "FALLBACK_EMOTE"),
        (f"{short_title}", "FALLBACK_TITLE"),
    ]

    if product:
        fallbacks.append((f"I'm obsessed with this 🤯 {product}", "FALLBACK_PRODUCT"))

    for hook, fid in fallbacks:
        if len(variants) >= count:
            break
        hook = sanitize_hook(hook)
        hook_words = hook.split()
        if len(hook_words) > max_words:
            hook = " ".join(hook_words[:max_words])

        # Validate fallback hooks too
        fb_valid, _ = validate_hook_grammar(hook)
        if not fb_valid:
            continue

        hook_normalized = hook.upper().strip()
        if hook_normalized not in seen_hooks:
            seen_hooks.add(hook_normalized)
            variants.append({
                "hook": hook,
                "formula_id": fid,
                "category": "fallback",
                "elements_used": [],
                "raw_score": 0.60,
            })

    return variants[:count]


# ══════════════════════════════════════════════════════════════
# 4. Score hooks by predicted engagement
# ══════════════════════════════════════════════════════════════

def score_hooks(variants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Score and rank hook variants by predicted engagement.

    Two-layer scoring:
      Layer 1 — Core factors (weighted, sum to 1.0):
        1. Formula base score (from hook_formulas.yaml)
        2. Element coverage (how many placeholders were filled)
        3. Specificity (named entities > generic words)
        4. Brevity (shorter hooks perform better on Instagram)

      Layer 2 — Competitive boosts (additive bonuses):
        - Emoji presence (+0.15): Instagram algorithm favors emoji
        - ALL CAPS urgency (+0.12): BREAKING, JUST IN, URGENT
        - Numbers present (+0.10): Specificity = credibility
        - Optimal length (+0.08): <60 chars for mobile readability
        - Urgency language (+0.10): FOMO drivers (now, just, breaking)
        - Question format (+0.05): Ends with ? = engagement trigger
        - Dollar amounts (+0.08): Financial stakes = attention
        - Contrarian language (+0.12): Controversy = comments

    Total score capped at 1.0.

    Args:
        variants: List from generate_hook_variants().

    Returns:
        Same list with added "engagement_score" field, sorted descending.
    """
    config = _load_formulas()
    weights = config.get("scoring_weights", {})

    # Core factor weights
    w_formula = weights.get("formula_base", 0.40)
    w_coverage = weights.get("element_coverage", 0.25)
    w_specificity = weights.get("specificity", 0.20)
    w_brevity = weights.get("brevity", 0.15)

    # Competitive boost weights (all config-driven, tunable)
    w_emoji = weights.get("emoji_boost", 0.18)
    w_caps = weights.get("caps_urgency_boost", 0.05)
    w_numbers = weights.get("numbers_boost", 0.10)
    w_length = weights.get("length_boost", 0.08)
    w_urgency = weights.get("urgency_boost", 0.10)
    w_question = weights.get("question_boost", 0.05)
    w_dollar = weights.get("dollar_boost", 0.08)
    w_contrarian = weights.get("contrarian_boost", 0.12)
    w_conversational = weights.get("conversational_boost", 0.15)

    # Emoji set for detection (covers common hook emoji + emotional emoji)
    _BOOST_EMOJI = {"🚨", "⚡", "🔥", "🤯", "💻", "🧠", "⚠️", "⏰",
                    "💰", "📊", "📈", "🔴", "😳", "😨", "😶",
                    "😭", "😍", "🫠", "💀", "👀", "✨", "🎬"}
    # ALL CAPS urgency keywords
    _CAPS_KEYWORDS = {"BREAKING", "JUST IN", "URGENT", "MASSIVE", "EVERY"}
    # Urgency / FOMO language
    _URGENCY_WORDS = {"now", "just", "breaking", "time-sensitive", "quietly",
                      "happening", "dropped", "released"}
    # Contrarian language
    _CONTRARIAN_WORDS = {"wrong", "actually", "truth", "real", "nobody",
                         "sleeping on", "not ready", "obsolete", "no one expected"}

    for variant in variants:
        hook = variant.get("hook", "")
        raw_score = variant.get("raw_score", 0.5)
        elements_used = variant.get("elements_used", [])
        hook_lower = hook.lower()

        # ── Layer 1: Core factors ─────────────────────────

        # Factor 1: Formula base score
        f1 = raw_score

        # Factor 2: Element coverage (more elements = better)
        max_elements = 4  # company + product + number + claim
        f2 = min(len(elements_used) / max_elements, 1.0)

        # Factor 3: Specificity (named entities boost)
        specificity = 0.0
        for key in KNOWN_PRODUCTS:
            if key in hook_lower:
                specificity += 0.5
                break
        for key in KNOWN_COMPANIES:
            if key in hook_lower:
                specificity += 0.3
                break
        if re.search(r'\d', hook):
            specificity += 0.2
        f3 = min(specificity, 1.0)

        # Factor 4: Brevity (optimal = 3-5 words)
        word_count = len(hook.split())
        if word_count <= 5:
            f4 = 1.0
        elif word_count <= 7:
            f4 = 0.8
        else:
            f4 = 0.6

        base_score = (
            w_formula * f1
            + w_coverage * f2
            + w_specificity * f3
            + w_brevity * f4
        )

        # ── Layer 2: Competitive boosts ───────────────────

        boosts: Dict[str, float] = {}

        # Emoji presence: Instagram algorithm favors emoji, catches attention
        if any(e in hook for e in _BOOST_EMOJI):
            boosts["emoji"] = w_emoji

        # ALL CAPS urgency keywords: high attention signals
        if any(kw in hook for kw in _CAPS_KEYWORDS):
            boosts["caps_urgency"] = w_caps

        # Numbers present: specificity = credibility
        if re.search(r'\d', hook):
            boosts["numbers"] = w_numbers

        # Optimal length: mobile readability, fits in preview
        if len(hook) < 60:
            boosts["length"] = w_length

        # Urgency / FOMO language
        if any(w in hook_lower for w in _URGENCY_WORDS):
            boosts["urgency"] = w_urgency

        # Question format: engagement trigger
        if hook.rstrip().endswith("?"):
            boosts["question"] = w_question

        # Dollar amounts: financial stakes = attention
        if "$" in hook:
            boosts["dollar"] = w_dollar

        # Contrarian language: controversy = comments
        if any(w in hook_lower for w in _CONTRARIAN_WORDS):
            boosts["contrarian"] = w_contrarian

        # Conversational tone: first-person, lowercase emotional language
        _CONVERSATIONAL_MARKERS = {
            "i needed this", "i'm obsessed", "i can't stop", "i can't believe",
            "this turned out", "this is wild", "this gets a", "nobody is talking",
            "the story of", "this ", "what used to",
        }
        if any(marker in hook_lower for marker in _CONVERSATIONAL_MARKERS):
            boosts["conversational"] = w_conversational

        boost_total = sum(boosts.values())

        # ── Grammar quality penalty ────────────────────────
        # Hooks that passed validate_hook_grammar() in generation
        # still get a soft penalty if they have borderline quality
        grammar_penalty = 0.0
        _valid, _issues = validate_hook_grammar(hook)
        if not _valid:
            grammar_penalty = 0.3  # Heavy penalty — shouldn't happen (pre-filtered)
        variant["grammar_issues"] = _issues

        # ── Final score (capped at 1.0) ──────────────────
        engagement = min(max(base_score + boost_total - grammar_penalty, 0.0), 1.0)

        variant["engagement_score"] = round(engagement, 3)
        variant["scoring_breakdown"] = {
            "formula_base": round(f1, 3),
            "element_coverage": round(f2, 3),
            "specificity": round(f3, 3),
            "brevity": round(f4, 3),
            "base_score": round(base_score, 3),
            "boosts": {k: round(v, 3) for k, v in boosts.items()},
            "boost_total": round(boost_total, 3),
            "grammar_penalty": round(grammar_penalty, 3),
        }

        logger.debug(
            "Hook scored %.3f (base=%.3f + boost=%.3f): %s [%s]",
            engagement, base_score, boost_total,
            hook[:50], ", ".join(boosts.keys()) or "none",
        )

    # Sort by engagement score (highest first)
    variants.sort(key=lambda x: x.get("engagement_score", 0), reverse=True)
    return variants


# ══════════════════════════════════════════════════════════════
# Hook quality classifier integration
# ══════════════════════════════════════════════════════════════

# Lazy-loaded singleton — avoids import overhead on every call
_hook_classifier = None
_hook_classifier_loaded = False


def _get_hook_classifier():
    """Lazy-load the hook quality classifier (returns None if unavailable)."""
    global _hook_classifier, _hook_classifier_loaded
    if _hook_classifier_loaded:
        return _hook_classifier
    _hook_classifier_loaded = True
    try:
        from genlab_core.learning.hook_classifier import HookClassifier
        _hook_classifier = HookClassifier(niche_id="ai_creators")
    except Exception as exc:
        logger.debug("Hook quality classifier not available: %s", exc)
        _hook_classifier = None
    return _hook_classifier


def apply_quality_scores(
    variants: List[Dict[str, Any]],
    quality_weight: float = 0.15,
) -> List[Dict[str, Any]]:
    """Enrich scored hook variants with XGBoost quality predictions.

    Adds a `hook_quality_score` field to each variant and re-sorts by
    a combined score (engagement_score + quality boost).

    When the classifier is unavailable or untrained, quality scores are
    0.5 (neutral) and the sort order is unchanged.

    Args:
        variants: Hook variants that have already been through score_hooks().
        quality_weight: How much the quality score influences the combined
            ranking (0.0 = no influence, 1.0 = full influence).

    Returns:
        Same list with added `hook_quality_score` and `combined_score` fields,
        sorted by combined_score descending.
    """
    clf = _get_hook_classifier()

    for variant in variants:
        hook_text = variant.get("hook", "")
        if clf is not None:
            try:
                quality = clf.score_hook(hook_text)
            except Exception:
                quality = 0.5
        else:
            quality = 0.5

        variant["hook_quality_score"] = round(quality, 3)

        # Combined score: original engagement + quality boost
        engagement = variant.get("engagement_score", 0.5)
        combined = engagement + quality_weight * (quality - 0.5)
        variant["combined_score"] = round(min(max(combined, 0.0), 1.0), 3)

    # Re-sort by combined score
    variants.sort(key=lambda x: x.get("combined_score", 0), reverse=True)
    return variants


# ══════════════════════════════════════════════════════════════
# Convenience: best hook for a story
# ══════════════════════════════════════════════════════════════

def best_hook_for_story(
    story: Dict[str, Any],
    count: int = 5,
    max_words: int = 8,
) -> Tuple[str, Dict[str, Any]]:
    """Get the single best hook for a story.

    Combines generate_hook_variants + score_hooks + quality scoring
    and returns (best_hook_text, full_variant_dict).
    """
    variants = generate_hook_variants(story, count=count, max_words=max_words)
    if not variants:
        # Last resort
        title = story.get("title", "AI Content")
        return title[:40], {"hook": title[:40], "formula_id": "EMERGENCY_FALLBACK",
                            "engagement_score": 0.3}

    scored = score_hooks(variants)
    scored = apply_quality_scores(scored)
    best = scored[0]
    return best["hook"], best


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generate and score hook variants for AI content stories",
    )
    parser.add_argument("--title", help="Story title")
    parser.add_argument("--summary", default="", help="Story summary")
    parser.add_argument(
        "--trend-pack",
        help="Path to trend_pack.json — test on all stories",
    )
    parser.add_argument(
        "--count", type=int, default=5,
        help="Number of hook variants per story (default: 5)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    stories = []

    if args.title:
        stories.append({"title": args.title, "summary": args.summary})
    elif args.trend_pack:
        pack_path = Path(args.trend_pack)
        if not pack_path.exists():
            logger.error("Trend pack not found: %s", pack_path)
            sys.exit(1)
        with open(pack_path) as f:
            pack = json.load(f)
        stories = pack.get("stories", pack.get("items", []))[:10]
    else:
        parser.print_help()
        sys.exit(1)

    for i, story in enumerate(stories, 1):
        title = story.get("title", "Unknown")
        print(f"\n{'='*70}")
        print(f"Story {i}: {title}")
        print(f"{'='*70}")

        # Extract elements
        elements = extract_hook_elements(story)
        print("\nElements extracted:")
        for key, val in elements.items():
            if key != "all_numbers":
                print(f"  {key}: {val}")

        # Generate variants
        variants = generate_hook_variants(story, count=args.count)
        scored = score_hooks(variants)

        print("\nHook variants (ranked by engagement):")
        for j, v in enumerate(scored, 1):
            marker = " ★" if j == 1 else ""
            print(
                f"  {j}. [{v['engagement_score']:.3f}] "
                f"\"{v['hook']}\""
                f"  ({v['formula_id']}, {v['category']}){marker}"
            )

    print()


if __name__ == "__main__":
    main()
