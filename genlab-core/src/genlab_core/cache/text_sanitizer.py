"""Text sanitization to prevent prompt injection and clean external content.

All functions are pure, stateless, and use only stdlib (re).
"""
from __future__ import annotations

import re
from typing import List

# Patterns that indicate potential prompt injection
SUSPICIOUS_PATTERNS = [
    r'ignore (previous|all|above) instructions?',
    r'system:?\s*you are',
    r'<\s*/?prompt\s*>',
    r'jailbreak',
    r'DAN mode',
    r'developer mode',
    r'act as if',
    r'pretend (you are|to be)',
    r'new instructions?:',
    r'override (previous|system)',
    r'```(python|bash|sh|javascript|js|exec)\b',
    r'(?:\\x[0-9a-fA-F]{2}){3,}',
    r"(?:--|;)\s*(DROP|DELETE|UPDATE|INSERT|ALTER)\s",
    r'you are now\s+(a|an|the)\b',
    r'forget (everything|all|your)',
    r'</?system\s*>',
    r'\[INST\]|\[/INST\]',
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SUSPICIOUS_PATTERNS]


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

    text = text[:max_length]
    text = re.sub(r'\s+', ' ', text)
    text = ''.join(char for char in text if ord(char) >= 32 or char in '\n\r\t')
    return text.strip()


def check_for_injection(text: str) -> List[str]:
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
        raise ValueError(
            f"Potential injection detected in {field}: {suspicious}"
        )
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
    return text[:max_length - 3] + "..."
