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
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f300-\U0001f5ff"  # symbols & pictographs
    "\U0001f680-\U0001f6ff"  # transport & map
    "\U0001f1e0-\U0001f1ff"  # flags
    "\U00002702-\U000027b0"  # dingbats
    "\U000024c2-\U0001f251"  # enclosed characters
    "\U0001f900-\U0001f9ff"  # supplemental symbols
    "\U0001fa00-\U0001fa6f"  # chess symbols
    "\U0001fa70-\U0001faff"  # symbols extended-A
    "\U00002600-\U000026ff"  # misc symbols
    "\U0000fe00-\U0000fe0f"  # variation selectors
    "\U0000200d"  # zero width joiner
    "\U00002b50"  # star
    "\U0000203c-\U00003299"  # other CJK/misc
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
    *,
    include_embeddings: bool = False,
) -> dict[str, float]:
    """Combine features into a flat dict for the classifier.

    Audio features are skipped (librosa not installed, audio files not
    available for hooks). The audio_path parameter is accepted for
    forward compatibility but currently ignored.

    Args:
        hook_text: The hook text to extract features from.
        audio_path: Unused. Reserved for future audio feature extraction.
        include_embeddings: If True, appends semantic embedding features
            from ``hook_embeddings.embed_hook`` to the 8 hand-engineered
            text features. Keyed as ``emb_0``..``emb_<dim-1>``. The
            embedding dim depends on the active tier (256 for TF-IDF /
            zero fallback, 384 for sentence-transformers). Default
            ``False`` preserves backwards compatibility with existing
            XGBoost model files trained on the 8-feature vector — those
            models would fail to load against a wider vector.

            W3.3 Layer 2 — see ``docs/W3-3-transformer-hook-classifier-roadmap.md``.
            Callers that opt in should embed the tier name in their
            serialised model metadata so a load-time check catches drift.

    Returns:
        Dict of feature_name -> float value.
    """
    features = extract_text_features(hook_text)

    if include_embeddings:
        # Lazy-import so opting out keeps the module dependency-free
        # (sentence-transformers and sklearn don't load on the happy path).
        from genlab_core.learning.hook_embeddings import embed_hook

        embedding = embed_hook(hook_text)
        for i, value in enumerate(embedding):
            features[f"emb_{i}"] = float(value)

    return features
