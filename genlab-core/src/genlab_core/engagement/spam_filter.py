"""Fast regex-based spam filter for inbound comments.

No ML model needed — spam comments on social media follow
predictable patterns (URLs, promo language, repetition).
"""
from __future__ import annotations

import re

# Patterns that indicate spam. Compiled once at import time.
_SPAM_PATTERNS = [
    re.compile(r"https?://\S+", re.IGNORECASE),           # URLs
    re.compile(r"(check|visit|click)\s+(my|the|this)\s+(bio|link|profile)", re.IGNORECASE),
    re.compile(r"(free|earn|make)\s+\$?\d+", re.IGNORECASE),
    re.compile(r"(dm|message)\s+(me|us)\s+(for|to)", re.IGNORECASE),
    re.compile(r"(follow|sub)\s+(me|back|4follow)", re.IGNORECASE),
    re.compile(r"(.)\1{5,}"),                              # Repeated chars (aaaaaa)
    re.compile(r"(promo|discount|giveaway|winner)", re.IGNORECASE),
]


def is_spam(text: str) -> bool:
    """Return True if the comment matches common spam patterns."""
    if not text or len(text.strip()) < 2:
        return True  # Empty/trivial comments are not worth engaging with
    return any(p.search(text) for p in _SPAM_PATTERNS)
