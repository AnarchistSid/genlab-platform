"""Pipeline stage: Urgency classifier + express lane routing.

Scans stories for urgency signals and classifies them:
  - CRITICAL: Major launch, company event (score ≥0.6)
  - HIGH:     Breaking news, viral moment (score ≥0.4)
  - MEDIUM:   Time-sensitive event (score ≥0.2)
  - LOW:      Standard story (default)

CRITICAL and HIGH stories are marked for express-lane processing
(auto-approve, skip review queue, priority rendering).

Signal patterns are niche-agnostic by default but can be extended
via niche_config.express_lane.extra_patterns.

Non-fatal: classification failures default stories to LOW urgency.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Urgency levels ────────────────────────────────────────────
URGENCY_CRITICAL = "CRITICAL"
URGENCY_HIGH = "HIGH"
URGENCY_MEDIUM = "MEDIUM"
URGENCY_LOW = "LOW"

EXPRESS_LEVELS = {URGENCY_CRITICAL, URGENCY_HIGH}

# ── Signal patterns (compiled once) ──────────────────────────

# Major launches (CRITICAL) — AI, gaming, entertainment
_MAJOR_LAUNCHES = re.compile(
    r"\b(gpt[- ]?[5-9]|claude[- ]?[4-9]|gemini[- ]?[2-9]|llama[- ]?[4-9]|"
    r"sora[- ]?2|dall[- ]?e[- ]?[4-9]|o[3-9]|grok[- ]?[3-9]|"
    # Sports majors
    r"super\s*bowl|world\s*cup|world\s*series|nba\s*finals|stanley\s*cup|"
    r"champions\s*league\s*final|"
    # Entertainment majors
    r"oscar|academy\s*award|emmy|grammy|golden\s*globe|"
    # Gaming majors
    r"e3|game\s*awards|gamescom|tga|playstation\s*showcase|"
    r"nintendo\s*direct|xbox\s*showcase)\b",
    re.IGNORECASE,
)

# Company events (CRITICAL)
_COMPANY_EVENTS = re.compile(
    r"\b(acquir\w+|merger|bought|billion|ipo|goes public|shut\s*down|bankrupt|"
    r"ceo\s+(resign|fired|step|leave|replace)|layoff|lay\s+off)\b",
    re.IGNORECASE,
)

# Breaking signals (HIGH)
_BREAKING = re.compile(
    r"\b(breaking|just\s+announced|just\s+released|just\s+launched|"
    r"just\s+dropped|embargo\s+lift|exclusive|first\s+look|"
    # Sports breaking
    r"traded|injured|suspended|fired|hired|signed|buzzer\s*beater|"
    r"walk[- ]?off|hat[- ]?trick|no[- ]?hitter|perfect\s*game|"
    # Entertainment breaking
    r"trailer\s+dropped|teaser\s+dropped|confirmed|cancelled|renewed)\b",
    re.IGNORECASE,
)

# Time-sensitive (MEDIUM)
_TIME_SENSITIVE = re.compile(
    r"\b(keynote|earnings|conference|summit|live\s+demo|today|"
    r"right\s+now|happening\s+now|"
    # Sports live
    r"halftime|overtime|game\s+\d|match\s+day|fight\s+night|"
    # Entertainment events
    r"premiere|opening\s+night|red\s+carpet|"
    # Anime seasonal
    r"season\s+premiere|episode\s+\d+|final\s+episode)\b",
    re.IGNORECASE,
)

# Major companies (booster)
_MAJOR_COMPANIES = re.compile(
    r"\b(openai|anthropic|google|deepmind|meta|microsoft|nvidia|apple|"
    r"amazon|tesla|xai|mistral|sony|nintendo|ea|ubisoft|riot|epic|valve|"
    r"warner|disney|marvel|dc|studio\s*ghibli|crunchyroll)\b",
    re.IGNORECASE,
)

# Large numbers (booster)
_LARGE_NUMBERS = re.compile(
    r"(?:\$\s*)?(\d+(?:\.\d+)?)\s*([BT])\b",
    re.IGNORECASE,
)


class ExpressLane:
    """Classify story urgency and route express candidates.

    Reads: context['stories']
    Writes: context['stories'][*]['urgency_classification'],
            context['run_stats']['express_lane']
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])
        if not stories:
            logger.info("[ExpressLane] No stories to classify")
            return context

        express_count = 0
        level_counts: dict[str, int] = {}

        for story in stories:
            try:
                classification = self._classify(story)
                story["urgency_classification"] = classification
                level = classification["urgency"]
                level_counts[level] = level_counts.get(level, 0) + 1
                if classification["express"]:
                    express_count += 1
            except Exception:
                logger.exception(
                    "[ExpressLane] Classification failed for %s",
                    story.get("story_id", story.get("title", "unknown")[:40]),
                )
                story["urgency_classification"] = {
                    "urgency": URGENCY_LOW,
                    "signals": [],
                    "score": 0.0,
                    "express": False,
                }
                level_counts[URGENCY_LOW] = level_counts.get(URGENCY_LOW, 0) + 1

        # Sort stories: express first, then by urgency score descending
        level_order = {URGENCY_CRITICAL: 0, URGENCY_HIGH: 1, URGENCY_MEDIUM: 2, URGENCY_LOW: 3}
        stories.sort(
            key=lambda s: (
                level_order.get(s.get("urgency_classification", {}).get("urgency", URGENCY_LOW), 9),
                -s.get("urgency_classification", {}).get("score", 0),
            )
        )

        logger.info(
            "[ExpressLane] %d stories classified: %s | %d express",
            len(stories),
            level_counts,
            express_count,
        )

        context.setdefault("run_stats", {})["express_lane"] = {
            "total": len(stories),
            "express_count": express_count,
            "level_counts": level_counts,
        }

        return context

    @staticmethod
    def _classify(story: dict[str, Any]) -> dict[str, Any]:
        """Classify a single story's urgency."""
        title = story.get("title", "")
        summary = story.get("summary", story.get("body", ""))
        text = f"{title} {summary}"

        signals: list[str] = []
        score = 0.0

        # CRITICAL signals
        if _MAJOR_LAUNCHES.search(text):
            signals.append("major_launch")
            score += 0.5

        if _COMPANY_EVENTS.search(text):
            signals.append("company_event")
            score += 0.4

        # HIGH signals
        if _BREAKING.search(text):
            signals.append("breaking_news")
            score += 0.3

        # MEDIUM signals
        if _TIME_SENSITIVE.search(text):
            signals.append("time_sensitive")
            score += 0.15

        # Boosters
        if _MAJOR_COMPANIES.search(text):
            signals.append("major_company")
            score += 0.1

        large_match = _LARGE_NUMBERS.search(text)
        if large_match:
            num = float(large_match.group(1))
            suffix = large_match.group(2).upper()
            if suffix == "B" and num >= 1:
                signals.append(f"large_number_{num}{suffix}")
                score += 0.2
            elif suffix == "T":
                signals.append(f"large_number_{num}{suffix}")
                score += 0.3

        # Multi-signal boost
        if len(signals) >= 3:
            score += 0.15

        # Determine level
        if score >= 0.6:
            urgency = URGENCY_CRITICAL
        elif score >= 0.4:
            urgency = URGENCY_HIGH
        elif score >= 0.2:
            urgency = URGENCY_MEDIUM
        else:
            urgency = URGENCY_LOW

        return {
            "urgency": urgency,
            "signals": signals,
            "score": round(score, 3),
            "express": urgency in EXPRESS_LEVELS,
        }
