"""Text cleaning for natural TTS output.

Strips hashtags, URLs, emoji, markdown formatting, and extra whitespace
so that TTS engines produce clean, natural-sounding speech.
"""
from __future__ import annotations

import re

# Compiled patterns for performance
_RE_HASHTAGS = re.compile(r"#\w+")
_RE_URLS = re.compile(r"https?://\S+")
_RE_EMOJI = re.compile(
    r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
    r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
    r"\U00002702-\U000027B0\U000024C2-\U0001F251]+"
)
_RE_MARKDOWN_BOLD = re.compile(r"\*+")
_RE_WHITESPACE = re.compile(r"\s+")
_RE_LEADING_PUNCT = re.compile(r"^[^\w]+")


def clean_text_for_tts(text: str) -> str:
    """Clean text for natural TTS output.

    Removes hashtags, URLs, emoji, markdown formatting, and normalizes
    whitespace. Idempotent — safe to call multiple times.
    """
    text = _RE_HASHTAGS.sub("", text)
    text = _RE_URLS.sub("", text)
    text = _RE_EMOJI.sub("", text)
    text = _RE_MARKDOWN_BOLD.sub("", text)
    text = _RE_WHITESPACE.sub(" ", text).strip()
    text = _RE_LEADING_PUNCT.sub("", text)
    return text
