"""Chat excitement scorer — measures Twitch chat hype during a clip.

Used by the SCORE_CLIPS pipeline stage as one of the 7 scoring dimensions
(chat_excitement, weight 0.13).

Computes a composite score (0.0-1.0) from 5 chat signals:
  1. MESSAGE_RATE  (weight 0.35) — msgs/sec vs. baseline
  2. EMOTE_DENSITY (weight 0.25) — tier-weighted emote matches per message
  3. CAPS_RATIO    (weight 0.15) — avg uppercase fraction per message
  4. KEYWORD_DENSITY (weight 0.15) — messages containing excitement keywords
  5. BREVITY       (weight 0.10) — short msgs indicate reactive, not conversational

All weights and thresholds come from config/scoring_weights.yaml
(``chat_excitement`` section).  Emote tiers and keywords from
config/chat_emotes.yaml.

Research basis: PogChampNet (Farza / Visor.gg) showed that chat activity
spikes strongly correlate with highlight moments in gaming streams.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal 1: MESSAGE_RATE
# ---------------------------------------------------------------------------


def signal_message_rate(
    num_messages: int,
    duration_sec: float,
    baseline: float,
) -> float:
    """Compute message-rate signal.

    Formula: min(actual_rate / (baseline * 5), 1.0)

    Args:
        num_messages: Total chat messages in the window.
        duration_sec: Duration of the chat window in seconds.
        baseline: Expected "normal" message rate (msgs/sec).

    Returns:
        Score in [0.0, 1.0].
    """
    if duration_sec <= 0 or num_messages <= 0:
        return 0.0
    actual_rate = num_messages / duration_sec
    return min(actual_rate / (baseline * 5), 1.0)


# ---------------------------------------------------------------------------
# Signal 2: EMOTE_DENSITY
# ---------------------------------------------------------------------------


def signal_emote_density(
    messages: list[str],
    emote_config: dict,
) -> float:
    """Compute emote-density signal.

    Scans each message for tier-1 and tier-2 emotes (as whole words).
    Weighted score = sum(tier_weight per match) / message_count, cap 1.0.

    Args:
        messages: List of raw message text strings.
        emote_config: Dict with ``emotes.tier_1`` / ``emotes.tier_2`` and their weights.

    Returns:
        Score in [0.0, 1.0].
    """
    if not messages:
        return 0.0

    emote_tiers = emote_config.get("emotes", {})
    total_weighted = 0.0

    for msg in messages:
        words = msg.split()
        for tier_key in ("tier_1", "tier_2"):
            tier = emote_tiers.get(tier_key, {})
            weight = tier.get("weight", 0.0)
            names = tier.get("names", [])
            for word in words:
                if word in names:
                    total_weighted += weight

    return min(total_weighted / len(messages), 1.0)


# ---------------------------------------------------------------------------
# Signal 3: CAPS_RATIO
# ---------------------------------------------------------------------------


def signal_caps_ratio(messages: list[str]) -> float:
    """Compute caps-ratio signal.

    Average fraction of uppercase alphabetic characters per message.
    Normalized so that >=0.7 -> 1.0.

    Returns:
        Score in [0.0, 1.0].
    """
    if not messages:
        return 0.0

    ratios: list[float] = []
    for msg in messages:
        alpha_chars = [c for c in msg if c.isalpha()]
        if not alpha_chars:
            continue
        upper_count = sum(1 for c in alpha_chars if c.isupper())
        ratios.append(upper_count / len(alpha_chars))

    if not ratios:
        return 0.0

    avg_ratio = sum(ratios) / len(ratios)
    # Normalize: 0.7 -> 1.0
    return min(avg_ratio / 0.7, 1.0)


# ---------------------------------------------------------------------------
# Signal 4: KEYWORD_DENSITY
# ---------------------------------------------------------------------------


def signal_keyword_density(
    messages: list[str],
    keywords: list[str],
) -> float:
    """Compute keyword-density signal.

    Fraction of messages containing at least one excitement keyword.
    Case-insensitive matching.  Supports multi-word keywords.

    Returns:
        Score in [0.0, 1.0].
    """
    if not messages:
        return 0.0

    lower_keywords = [kw.lower() for kw in keywords]
    matches = 0
    for msg in messages:
        msg_lower = msg.lower()
        if any(kw in msg_lower for kw in lower_keywords):
            matches += 1

    return min(matches / len(messages), 1.0)


# ---------------------------------------------------------------------------
# Signal 5: BREVITY
# ---------------------------------------------------------------------------


def signal_brevity(messages: list[str]) -> float:
    """Compute brevity signal.

    Formula: max(0, 1 - avg_words / 10).  Messages >10 words avg -> 0.0.
    Short, reactive messages (1-2 words) score highest.

    Returns:
        Score in [0.0, 1.0].
    """
    if not messages:
        return 0.0

    total_words = sum(len(msg.split()) for msg in messages)
    avg_words = total_words / len(messages)
    return max(0.0, 1.0 - avg_words / 10.0)


# ---------------------------------------------------------------------------
# Composite scorers
# ---------------------------------------------------------------------------


def _compute_signals(
    chat_data: dict,
    config: dict,
    emote_config: dict,
) -> dict[str, float] | None:
    """Compute all 5 signal scores from chat_data.

    Returns None if chat_data is invalid or below minimum thresholds.
    """
    if chat_data is None:
        return None

    if chat_data.get("fetch_status") != "ok":
        return None

    raw_messages = chat_data.get("messages", [])
    if not raw_messages:
        return None

    min_msgs = config.get("min_messages_for_score", 5)
    if len(raw_messages) < min_msgs:
        return None

    # Extract text strings
    texts = [m.get("text", "") if isinstance(m, dict) else str(m) for m in raw_messages]

    # Compute duration from clip window
    start = chat_data.get("clip_start_in_vod")
    end = chat_data.get("clip_end_in_vod")
    if start is not None and end is not None:
        duration = end - start
    else:
        duration = 30.0  # fallback

    baseline = config.get("baseline_message_rate", 5.0)
    keywords = emote_config.get("keywords", [])

    return {
        "message_rate": signal_message_rate(len(texts), duration, baseline),
        "emote_density": signal_emote_density(texts, emote_config),
        "caps_ratio": signal_caps_ratio(texts),
        "keyword_density": signal_keyword_density(texts, keywords),
        "brevity": signal_brevity(texts),
    }


def compute_excitement(
    chat_data: dict | None,
    config: dict,
    emote_config: dict,
) -> float:
    """Compute composite chat excitement score.

    Weighted sum of 5 signals, all individually bounded [0, 1],
    with signal weights from config.

    Args:
        chat_data: Chat data dict from ``fetch_clip_chat()``, or None.
        config: ``chat_excitement`` section from scoring_weights.yaml.
        emote_config: Parsed chat_emotes.yaml dict.

    Returns:
        Score in [0.0, 1.0].  Returns 0.0 when chat data is missing,
        fetch failed, or message count is below threshold.
    """
    signals = _compute_signals(chat_data, config, emote_config)
    if signals is None:
        return 0.0

    weights = config.get("signal_weights", {})
    composite = sum(signals[name] * weights.get(name, 0.0) for name in signals)
    return min(max(composite, 0.0), 1.0)


def compute_excitement_breakdown(
    chat_data: dict | None,
    config: dict,
    emote_config: dict,
) -> dict[str, float]:
    """Compute per-signal breakdown plus composite score.

    Same as ``compute_excitement`` but returns all intermediate values
    for debugging and analysis.

    Returns:
        Dict with keys: message_rate, emote_density, caps_ratio,
        keyword_density, brevity, composite.  All values in [0, 1].
    """
    signals = _compute_signals(chat_data, config, emote_config)
    if signals is None:
        return {
            "message_rate": 0.0,
            "emote_density": 0.0,
            "caps_ratio": 0.0,
            "keyword_density": 0.0,
            "brevity": 0.0,
            "composite": 0.0,
        }

    weights = config.get("signal_weights", {})
    composite = sum(signals[name] * weights.get(name, 0.0) for name in signals)
    composite = min(max(composite, 0.0), 1.0)

    return {**signals, "composite": composite}
