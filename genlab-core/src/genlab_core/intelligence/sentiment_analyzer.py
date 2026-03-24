"""Lightweight sentiment analysis for engagement comments.

Uses keyword-based heuristic (no ML dependencies). Classifies comments
into: positive, negative, neutral, question, request.

For production: replace with a fine-tuned model or Anthropic API call.
"""
from __future__ import annotations

import re
from typing import Literal

SentimentLabel = Literal["positive", "negative", "neutral", "question", "request"]


# Weighted keyword lists
_POSITIVE_PATTERNS = [
    (r"\b(love|amazing|awesome|incredible|fire|goat|goated|sick|insane|beautiful|perfect|best)\b", 2),
    (r"\b(great|good|nice|cool|dope|lit|valid|W|huge|massive|clutch)\b", 1),
    (r"(🔥|❤️|😍|💪|👏|🙌|💯|⭐|🎉|😂|🤩)", 1),
    (r"\b(thank|thanks|appreciate|respect|legend)\b", 1),
]

_NEGATIVE_PATTERNS = [
    (r"\b(hate|trash|garbage|worst|terrible|awful|horrible|mid|L|ratio|cringe|boring|ass)\b", 2),
    (r"\b(bad|weak|dead|lame|fake|overrated|overhyped|disappointed|annoying)\b", 1),
    (r"(👎|😡|🤮|💀|🗑️)", 1),
    (r"\b(waste|scam|clickbait|misleading)\b", 2),
]

_QUESTION_PATTERNS = [
    (r"\?$", 2),
    (r"\b(what|when|where|how|why|who|which|can you|do you|is this|are you)\b", 1),
]

_REQUEST_PATTERNS = [
    (r"\b(please|make|do|cover|react to|review|try|show|post more|upload)\b", 1),
    (r"\b(part 2|more of this|next video|collab|series)\b", 2),
]


def analyze_sentiment(text: str) -> dict[str, any]:
    """Analyze comment sentiment.

    Returns:
        {
            "label": "positive" | "negative" | "neutral" | "question" | "request",
            "score": float (-1 to 1, negative to positive),
            "signals": {"positive": int, "negative": int, "question": int, "request": int}
        }
    """
    if not text or not text.strip():
        return {"label": "neutral", "score": 0.0, "signals": {}}

    lower = text.lower().strip()

    signals = {"positive": 0, "negative": 0, "question": 0, "request": 0}

    for pattern, weight in _POSITIVE_PATTERNS:
        signals["positive"] += len(re.findall(pattern, lower, re.IGNORECASE)) * weight

    for pattern, weight in _NEGATIVE_PATTERNS:
        signals["negative"] += len(re.findall(pattern, lower, re.IGNORECASE)) * weight

    for pattern, weight in _QUESTION_PATTERNS:
        signals["question"] += len(re.findall(pattern, lower, re.IGNORECASE)) * weight

    for pattern, weight in _REQUEST_PATTERNS:
        signals["request"] += len(re.findall(pattern, lower, re.IGNORECASE)) * weight

    # Determine label
    max_signal = max(signals, key=signals.get)
    max_value = signals[max_signal]

    if max_value == 0:
        label = "neutral"
    elif max_signal == "question" and signals["question"] >= 2:
        label = "question"
    elif max_signal == "request" and signals["request"] >= 2:
        label = "request"
    elif signals["positive"] > signals["negative"]:
        label = "positive"
    elif signals["negative"] > signals["positive"]:
        label = "negative"
    else:
        label = "neutral"

    # Score: -1 (very negative) to +1 (very positive)
    total = signals["positive"] + signals["negative"]
    if total > 0:
        score = round((signals["positive"] - signals["negative"]) / total, 2)
    else:
        score = 0.0

    return {"label": label, "score": score, "signals": signals}


def batch_analyze(comments: list[dict]) -> dict[str, int]:
    """Analyze a batch of comments and return sentiment distribution.

    Args:
        comments: List of dicts with 'text' key.

    Returns:
        {"positive": N, "negative": N, "neutral": N, "question": N, "request": N}
    """
    distribution = {"positive": 0, "negative": 0, "neutral": 0, "question": 0, "request": 0}
    for comment in comments:
        result = analyze_sentiment(comment.get("text", ""))
        distribution[result["label"]] += 1
    return distribution
