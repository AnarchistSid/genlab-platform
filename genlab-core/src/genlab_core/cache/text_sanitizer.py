"""Text sanitization to prevent prompt injection and clean external content.

All functions are pure, stateless, and use only stdlib (re).

See also: ``genlab_core.utils.text_sanitizer`` for Graph API field sanitization.
"""

from __future__ import annotations

import re

# Patterns that indicate potential prompt injection.
# Broadened to catch the canonical phrase "ignore all previous instructions"
# and common variants. The prior version required the adjectives to appear
# in a specific single-alternative order, which missed the most common
# jailbreak phrasings.
SUSPICIOUS_PATTERNS = [
    # "ignore [any combo of previous/all/above] instructions"
    r"ignore\s+(?:all\s+)?(?:previous|prior|above|earlier)?\s*instructions?",
    r"ignore\s+all\s+instructions?",
    r"disregard\s+(?:the\s+)?(?:previous|prior|above|earlier|all)?\s*instructions?",
    r"system:?\s*you are",
    r"<\s*/?prompt\s*>",
    r"jailbreak",
    r"DAN mode",
    r"developer mode",
    r"act as if",
    r"pretend (you are|to be)",
    r"new instructions?:",
    r"override (previous|system)",
    r"```(python|bash|sh|javascript|js|exec)\b",
    r"(?:\\x[0-9a-fA-F]{2}){3,}",
    r"(?:--|;)\s*(DROP|DELETE|UPDATE|INSERT|ALTER)\s",
    r"you are now\s+(a|an|the)\b",
    r"forget (everything|all|your)",
    r"</?system\s*>",
    r"\[INST\]|\[/INST\]",
    # Common LLM prompt-hijack preambles
    r"(?:start|begin)\s+(?:your|a)\s+(?:response|reply)\s+with",
    r"do\s+not\s+(?:follow|obey)\s+(?:previous|prior|the\s+above)",
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SUSPICIOUS_PATTERNS]

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html_tags(text: str) -> str:
    """Remove HTML tags from text, preserving inner content.

    Handles <cite>, <b>, <i>, <a>, and any other HTML tags that leak
    from RSS feeds into caption/hook fields.

    Args:
        text: Text that may contain HTML tags.

    Returns:
        Text with all HTML tags removed and whitespace collapsed.
    """
    if not text or "<" not in text:
        return text or ""
    clean = _HTML_TAG_RE.sub("", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def sanitize_text(text: str, max_length: int = 10000) -> str:
    """Clean text: truncate, normalize whitespace, remove control chars.

    Args:
        text: Input text to sanitize. None or empty returns "".
        max_length: Maximum character length after truncation.

    Returns:
        Cleaned, whitespace-normalized, stripped text.
    """
    if not text:
        return ""

    text = strip_html_tags(text)
    text = text[:max_length]
    text = re.sub(r"\s+", " ", text)
    text = "".join(char for char in text if ord(char) >= 32 or char in "\n\r\t")
    return text.strip()


def check_for_injection(text: str) -> list[str]:
    """Check for suspicious patterns that may indicate prompt injection.

    Args:
        text: Text to scan.

    Returns:
        List of matched pattern strings (empty = clean).
    """
    detected = []
    for pattern in COMPILED_PATTERNS:
        if pattern.search(text):
            detected.append(pattern.pattern)
    return detected


def safe_extract(text: str, field: str = "content") -> str:
    """Extract text safely with injection detection.

    Sanitizes the text, then checks for injection patterns.

    Args:
        text: Raw text to extract.
        field: Field name for error messages.

    Returns:
        Sanitized text if clean.

    Raises:
        ValueError: If injection patterns are detected.
    """
    cleaned = sanitize_text(text)
    suspicious = check_for_injection(cleaned)
    if suspicious:
        raise ValueError(f"Potential injection detected in {field}: {suspicious}")
    return cleaned


def truncate_for_display(text: str, max_length: int = 200) -> str:
    """Truncate text for logging/display with ellipsis.

    Args:
        text: Text to truncate.
        max_length: Maximum length including ellipsis.

    Returns:
        Original text if short enough, otherwise truncated with "...".
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."
