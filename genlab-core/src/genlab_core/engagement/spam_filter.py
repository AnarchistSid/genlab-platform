"""Fast regex-based spam filter for inbound comments.

No ML model needed — spam comments on social media follow
predictable patterns (URLs, promo language, repetition).
"""

from __future__ import annotations

import re

# Domains that are legitimate in viewer comments (not spam)
_SAFE_URL_DOMAINS = re.compile(
    r"https?://(?:www\.)?"
    r"(?:youtube\.com|youtu\.be|instagram\.com|twitter\.com|x\.com|"
    r"twitch\.tv|tiktok\.com|facebook\.com|reddit\.com|"
    r"threads\.net|imdb\.com|myanimelist\.net|anilist\.co|"
    r"store\.steampowered\.com|epicgames\.com|"
    r"crunchyroll\.com|funimation\.com|netflix\.com|"
    r"espn\.com|nba\.com|nfl\.com)",
    re.IGNORECASE,
)

# Patterns that indicate spam. Compiled once at import time.
_SPAM_PATTERNS = [
    re.compile(r"(check|visit|click)\s+(my|the|this)\s+(bio|link|profile)", re.IGNORECASE),
    re.compile(r"(free|earn|make)\s+\$?\d+", re.IGNORECASE),
    re.compile(r"(dm|message)\s+(me|us)\s+(for|to)", re.IGNORECASE),
    re.compile(r"(follow|sub)\s+(me|back|4follow)", re.IGNORECASE),
    re.compile(r"(.)\1{5,}"),  # Repeated chars (aaaaaa)
    re.compile(r"(promo|discount|giveaway|winner)", re.IGNORECASE),
]

# URL pattern — only flags URLs that are NOT from safe domains
_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


def is_spam(text: str) -> bool:
    """Return True if the comment matches common spam patterns."""
    if not text or len(text.strip()) < 2:
        return True  # Empty/trivial comments are not worth engaging with

    # Check URL pattern separately — allow safe domains
    url_match = _URL_PATTERN.search(text)
    if url_match and not _SAFE_URL_DOMAINS.search(text):
        return True  # URL present but not from a safe domain

    return any(p.search(text) for p in _SPAM_PATTERNS)
