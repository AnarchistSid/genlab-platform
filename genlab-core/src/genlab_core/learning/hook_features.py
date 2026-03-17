"""Text feature extraction for hook quality prediction.

Pure Python/regex implementation — no spaCy, no heavy NLP dependencies.
Extracts lightweight text signals that correlate with hook engagement.
"""
from __future__ import annotations

import re
import unicodedata

# Regex for common superlative patterns
_SUPERLATIVE_RE = re.compile(
    r"\b(most|best|worst|biggest|smallest|fastest|slowest|largest|"
    r"highest|lowest|greatest|least|newest|oldest|latest|first|last|"
    r"top|ultimate|absolute)\b",
    re.IGNORECASE,
)

# Regex for emoji detection (covers most common emoji ranges)
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"  # enclosed characters
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero width joiner
    "\U00002B50"             # star
    "\U0000203C-\U00003299"  # other CJK/misc
    "]+",
)

# Regex for numbers (integers, decimals, percentages, dollar amounts)
_NUMBER_RE = re.compile(r"\b\d[\d,]*\.?\d*[%]?\b|\$[\d,]+\.?\d*")


def extract_text_features(text: str) -> dict[str, float]:
    """Extract NLP features from hook text using pure Python/regex.

    Returns an empty dict for empty/whitespace-only input.

    Features:
        word_count: Number of words.
        has_question: 1.0 if text ends with '?', else 0.0.
        has_number: 1.0 if text contains a number, else 0.0.
        emoji_count: Count of emoji characters.
        has_superlative: 1.0 if text contains a superlative word, else 0.0.
        starts_with_you: 1.0 if first word is 'you' (or 'your'), else 0.0.
        avg_word_length: Average character length of words.
        unique_word_ratio: Ratio of unique words to total words.
    """
    if not text or not text.strip():
        return {}

    text = text.strip()
    words = text.split()
    word_count = len(words)

    if word_count == 0:
        return {}

    # has_question: ends with ?
    has_question = 1.0 if text.rstrip().endswith("?") else 0.0

    # has_number
    has_number = 1.0 if _NUMBER_RE.search(text) else 0.0

    # emoji_count
    emoji_matches = _EMOJI_RE.findall(text)
    # Each match may contain multiple emoji; count individual codepoints
    emoji_count = 0.0
    for match in emoji_matches:
        for char in match:
            if unicodedata.category(char).startswith(("So", "Sk")):
                emoji_count += 1
            elif ord(char) > 0x1F000:
                emoji_count += 1

    # has_superlative
    has_superlative = 1.0 if _SUPERLATIVE_RE.search(text) else 0.0

    # starts_with_you
    first_word = words[0].lower().rstrip(".,!?:;")
    starts_with_you = 1.0 if first_word in ("you", "your", "you're") else 0.0

    # avg_word_length
    total_chars = sum(len(w) for w in words)
    avg_word_length = total_chars / word_count

    # unique_word_ratio
    lower_words = [w.lower() for w in words]
    unique_word_ratio = len(set(lower_words)) / word_count

    return {
        "word_count": float(word_count),
        "has_question": has_question,
        "has_number": has_number,
        "emoji_count": emoji_count,
        "has_superlative": has_superlative,
        "starts_with_you": starts_with_you,
        "avg_word_length": round(avg_word_length, 2),
        "unique_word_ratio": round(unique_word_ratio, 3),
    }


def build_feature_vector(
    hook_text: str,
    audio_path: str | None = None,
) -> dict[str, float]:
    """Combine features into a flat dict for the classifier.

    Audio features are skipped (librosa not installed, audio files not
    available for hooks). The audio_path parameter is accepted for
    forward compatibility but currently ignored.

    Args:
        hook_text: The hook text to extract features from.
        audio_path: Unused. Reserved for future audio feature extraction.

    Returns:
        Dict of feature_name -> float value.
    """
    return extract_text_features(hook_text)
