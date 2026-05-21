#!/usr/bin/env python3
"""Three-pass deduplication, clustering, and scoring of parsed items.

Pass 1: Exact URL match deduplication.
Pass 2: Title similarity via Jaccard coefficient on character 3-grams.
Pass 3: TF-IDF topic clustering — groups related stories about the same event.

After deduplication + clustering, items are scored using config/scoring_weights.yaml
(authority, recency, novelty) and output as ranked_stories.json.
"""

import argparse
import json
import logging
import math
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import yaml
from genlab_core.cache.stable_ids import generate_cluster_id, normalize_url

# Optional: scikit-learn for TF-IDF clustering (graceful fallback)
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity as sklearn_cosine

    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEIGHTS_PATH = PROJECT_ROOT / "config" / "scoring_weights.yaml"
TOPIC_WEIGHTS_PATH = PROJECT_ROOT / "config" / "topic_weights.yaml"


def load_weights(path: Path = WEIGHTS_PATH) -> Dict[str, Any]:
    """Load scoring weights configuration."""
    if not path.exists():
        logger.warning("Scoring weights not found at %s; using defaults", path)
        return {
            "weights": {
                "virality_fit": 0.35,
                "recency": 0.25,
                "novelty": 0.20,
                "authority": 0.20,
            },
            "authority_map": {"default": 0.5},
            "recency_decay_hours": 24,
            "novelty_threshold": 0.85,
        }
    with open(path) as f:
        return yaml.safe_load(f)


def load_topic_weights(path: Path = TOPIC_WEIGHTS_PATH) -> Dict[str, Any]:
    """Load topic weights configuration for audience-interest scoring."""
    if not path.exists():
        logger.warning("Topic weights not found at %s; topic scoring disabled", path)
        return {}
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Jaccard 3-gram similarity
# ---------------------------------------------------------------------------


def char_ngrams(text: str, n: int = 3) -> Set[str]:
    """Generate character n-grams from text."""
    text = text.lower().strip()
    if len(text) < n:
        return {text}
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def jaccard_similarity(a: str, b: str, n: int = 3) -> float:
    """Compute Jaccard similarity between two strings using char n-grams."""
    set_a = char_ngrams(a, n)
    set_b = char_ngrams(b, n)
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# Three-pass deduplication
# ---------------------------------------------------------------------------


def dedup_pass_url(items: List[Dict]) -> Tuple[List[Dict], int]:
    """Pass 1: Remove exact URL duplicates, keeping highest priority."""
    seen: Dict[str, Dict] = {}
    for item in items:
        url = item.get("url", "")
        if not url:
            continue
        canonical = normalize_url(url)
        if canonical in seen:
            existing = seen[canonical]
            if item.get("source_priority", 0) > existing.get("source_priority", 0):
                seen[canonical] = item
        else:
            seen[canonical] = item
    deduped = list(seen.values())
    removed = len(items) - len(deduped)
    logger.info("Pass 1 (URL): %d -> %d items (%d duplicates removed)", len(items), len(deduped), removed)
    return deduped, removed


def dedup_pass_title(items: List[Dict], threshold: float = 0.85) -> Tuple[List[Dict], int]:
    """Pass 2: Remove near-duplicate titles using Jaccard 3-gram similarity."""
    keep = []
    removed = 0
    for item in items:
        title = item.get("title", "")
        is_dup = False
        for kept in keep:
            sim = jaccard_similarity(title, kept.get("title", ""))
            if sim >= threshold:
                is_dup = True
                # Keep the one with higher priority
                if item.get("source_priority", 0) > kept.get("source_priority", 0):
                    keep.remove(kept)
                    keep.append(item)
                break
        if not is_dup:
            keep.append(item)
        else:
            removed += 1
    logger.info("Pass 2 (title): %d -> %d items (%d near-duplicates removed)", len(items), len(keep), removed)
    return keep, removed


# ---------------------------------------------------------------------------
# Pass 3: TF-IDF Topic Clustering
# ---------------------------------------------------------------------------


def _build_clusters_union_find(sim_matrix, threshold: float, n: int) -> List[List[int]]:
    """Single-linkage clustering via union-find on a similarity matrix.

    Two items join the same cluster if their cosine similarity >= threshold.
    Returns list of clusters, each a list of item indices.
    """
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i][j] >= threshold:
                union(i, j)

    clusters_map = defaultdict(list)
    for i in range(n):
        clusters_map[find(i)].append(i)

    return list(clusters_map.values())


def cluster_stories(items: List[Dict], config: Dict[str, Any]) -> Tuple[List[Dict], Dict[str, Any]]:
    """Pass 3: Cluster topically related stories using TF-IDF cosine similarity.

    Uses single-linkage clustering: two stories join the same cluster if their
    title+summary TF-IDF cosine similarity >= threshold.

    Annotates each item with:
      - cluster_id: deterministic hash of sorted member story_ids
      - cluster_rank: 1 = highest-scoring in cluster, 2 = second, etc.
      - is_cluster_primary: True if cluster_rank == 1
      - cluster_size: number of stories in this cluster
      - cluster_member_urls: URLs of all stories in the cluster

    Returns: (annotated items, clustering_stats dict)
    """
    clustering_cfg = config.get("clustering", {})
    enabled = clustering_cfg.get("enabled", True)
    threshold = clustering_cfg.get("similarity_threshold", 0.35)

    # Fallback if sklearn not available or clustering disabled
    if not enabled or not HAS_SKLEARN or len(items) < 2:
        reason = "disabled" if not enabled else ("sklearn not installed" if not HAS_SKLEARN else "too few items")
        logger.info("Pass 3 (clustering): skipped — %s", reason)
        # Still annotate each item as a singleton cluster
        for item in items:
            sid = item.get("story_id", "")
            item["cluster_id"] = generate_cluster_id([sid])
            item["cluster_rank"] = 1
            item["is_cluster_primary"] = True
            item["cluster_size"] = 1
            item["cluster_member_urls"] = [item.get("url", "")]
        stats = {"total_clusters": len(items), "avg_size": 1.0, "singletons": len(items), "stories_clustered": 0}
        return items, stats

    # Build text corpus: title + summary for each item
    ngram_range = tuple(clustering_cfg.get("tfidf_ngram_range", [1, 2]))
    max_features = clustering_cfg.get("tfidf_max_features", 5000)

    corpus = []
    for item in items:
        text = (item.get("title", "") + " " + item.get("summary", "")).strip()
        corpus.append(text if text else "empty")

    # TF-IDF vectorization
    vectorizer = TfidfVectorizer(
        ngram_range=ngram_range,
        max_features=max_features,
        stop_words="english",
    )
    tfidf_matrix = vectorizer.fit_transform(corpus)

    # Pairwise cosine similarity
    sim_matrix = sklearn_cosine(tfidf_matrix).tolist()

    # Single-linkage clustering
    clusters = _build_clusters_union_find(sim_matrix, threshold, len(items))

    # Annotate items with cluster metadata
    singletons = 0
    stories_clustered = 0
    for cluster_indices in clusters:
        # Get story_ids for this cluster
        cluster_story_ids = [items[i].get("story_id", "") for i in cluster_indices]
        cluster_id = generate_cluster_id(cluster_story_ids)
        cluster_urls = [items[i].get("url", "") for i in cluster_indices]

        # Sort cluster members by composite_score (descending) to assign ranks
        scored = [(i, items[i].get("composite_score", 0)) for i in cluster_indices]
        scored.sort(key=lambda x: x[1], reverse=True)

        is_multi = len(cluster_indices) > 1
        if is_multi:
            stories_clustered += len(cluster_indices)
        else:
            singletons += 1

        for rank, (idx, _score) in enumerate(scored, 1):
            items[idx]["cluster_id"] = cluster_id
            items[idx]["cluster_rank"] = rank
            items[idx]["is_cluster_primary"] = rank == 1
            items[idx]["cluster_size"] = len(cluster_indices)
            items[idx]["cluster_member_urls"] = cluster_urls

    total_clusters = len(clusters)
    avg_size = len(items) / total_clusters if total_clusters > 0 else 0

    logger.info(
        "Pass 3 (clustering): %d items -> %d clusters (avg %.1f items/cluster, %d singletons, %d stories in multi-clusters)",
        len(items),
        total_clusters,
        avg_size,
        singletons,
        stories_clustered,
    )

    # Log non-singleton clusters for visibility
    for cluster_indices in clusters:
        if len(cluster_indices) > 1:
            titles = [items[i].get("title", "?")[:60] for i in cluster_indices]
            logger.info("  Cluster (%d stories): %s", len(cluster_indices), " | ".join(titles))

    stats = {
        "total_clusters": total_clusters,
        "avg_size": round(avg_size, 2),
        "singletons": singletons,
        "stories_clustered": stories_clustered,
    }
    return items, stats


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_authority(item: Dict, authority_map: Dict[str, float]) -> float:
    """Score based on source priority (primary) or platform domain (fallback).

    Uses the source_priority field set by fetch_ai_creators.py from sources.yaml
    when available. Falls back to domain-based lookup in authority_map for
    items without source_priority (e.g., manually injected items or test data).
    """
    source_priority = item.get("source_priority")
    if source_priority is not None:
        return float(source_priority)

    # Fallback: domain-based lookup for items without source_priority
    domain = item.get("domain", "")
    if domain in authority_map:
        return authority_map[domain]
    # Try stripping subdomains
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        parent = ".".join(parts[i:])
        if parent in authority_map:
            return authority_map[parent]
    return authority_map.get("default", 0.5)


def score_recency(item: Dict, decay_hours: float = 24) -> float:
    """Score based on how recently the item was published (exponential decay)."""
    published = item.get("published_at")
    if not published:
        return 0.3  # Unknown date gets a low default

    try:
        pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_hours = max(0, (now - pub_dt).total_seconds() / 3600)
        return math.exp(-0.693 * age_hours / decay_hours)  # Half-life decay
    except (ValueError, TypeError):
        return 0.3


def score_novelty(item: Dict, all_items: List[Dict], threshold: float = 0.85) -> float:
    """Score novelty: inverse of max similarity to other items."""
    title = item.get("title", "")
    max_sim = 0.0
    for other in all_items:
        if other.get("story_id") == item.get("story_id"):
            continue
        sim = jaccard_similarity(title, other.get("title", ""))
        max_sim = max(max_sim, sim)
    # Novelty = 1 - max_similarity (capped at threshold)
    return max(0.0, 1.0 - max_sim)


# ---------------------------------------------------------------------------
# Virality Fit Scoring — pattern-matches titles/summaries against hook formulas
# ---------------------------------------------------------------------------

# Regex patterns for each hook formula (compiled once, reused per-item)
_HOOK_PATTERNS: Dict[str, re.Pattern] = {
    "dollar_amount": re.compile(
        r"\$\d[\d,]*\.?\d*\s*[bmkt](?:illion|rillion)?|\$\d[\d,]*\.?\d*",
        re.IGNORECASE,
    ),
    "pop_culture_crossover": re.compile(
        r"(?:game of thrones|naruto|harry potter|marvel|disney|star wars|"
        r"lord of the rings|pokemon|minecraft|studio ghibli|avengers|"
        r"breaking bad|the office|batman|spider.?man|taylor swift|"
        r"beyonce|drake|kanye|eminem|kardashian|stranger things|"
        r"squid game|one piece|demon slayer|jujutsu kaisen|"
        r"gta|fortnite|zelda|mario|sonic|pixar|dreamworks|"
        r"homer simpson|simpsons|south park|family guy|rick and morty|"
        r"super bowl|oscar|grammy|emmy|golden globe|"
        r"night of the living dead|hollywood|bollywood|"
        r"warner bros|netflix|hbo|spotify|youtube|tiktok|"
        r"kendall jenner|kim kardashian|beyonc[eé]|rihanna|"
        r"john wick|james bond|indiana jones|jurassic|terminator|"
        r"barbie|oppenheimer|deadpool|joker)",
        re.IGNORECASE,
    ),
    "human_interest": re.compile(
        r"\b\d{1,3}[\s-]?year[\s-]?old\b|"
        r"\b(?:grandma|grandmother|grandpa|grandfather|teen|student|teacher|farmer|"
        r"doctor|nurse|veteran|blind|deaf|disabled|autistic|homeless|refugee|"
        r"worker|employee|realtor|lawyer|artist|musician|author|writer)\b.*\b(?:ai|gpt|robot|chatbot)\b|"
        r"\b(?:ai|gpt|robot|chatbot)\b.*\b(?:grandma|grandmother|grandpa|grandfather|teen|student|teacher|farmer|"
        r"doctor|nurse|veteran|blind|deaf|disabled|autistic|homeless|refugee|"
        r"worker|employee|realtor|lawyer|artist|musician|author|writer)\b|"
        r"\b(?:after (?:you(?:'re)? dead|death|you die)|run your (?:social|life|account)|"
        r"people are (?:bringing|using|losing|quitting|afraid)|"
        r"telling workers|replaced by|losing (?:jobs?|work)|unemployment)\b",
        re.IGNORECASE,
    ),
    "ai_creator_showcase": re.compile(
        r"(?:someone|guy|woman|man|kid|user|artist|creator|developer|designer)\s+"
        r"(?:just\s+)?(?:made|created|built|generated|designed|trained|used)\b|"
        r"\b(?:ai|gpt|dall.?e|midjourney|sora|stable.?diffusion|flux|kling|veo)\s+"
        r"(?:generated|created|made|built|designed)\b|"
        r"\blook what\b|"
        r"\bwait (?:until|till) you see\b",
        re.IGNORECASE,
    ),
    "just_happened_urgency": re.compile(
        r"\b(?:just|breaking|now|today|hours? ago|minutes? ago|"
        r"this (?:morning|afternoon|evening)|happening now|"
        r"breaking news|just announced|just released|just dropped|"
        r"just launched|moments? ago)\b",
        re.IGNORECASE,
    ),
    "provocative_claim": re.compile(
        r"\b(?:should be worried|is (?:dead|dying|over|doomed|obsolete|finished)|"
        r"will (?:replace|destroy|kill|end|eliminate|disrupt)|"
        r"(?:replace|destroy|kill|end|eliminat|disrupt|poison|ruin|wreck)(?:ing|ed|s)?\b|"
        r"warns?\b|disaster|catastroph|crisis|collaps|"
        r"fear (?:grows?|that|of)|panic|alarm|backlash|outrage|scandal|"
        r"after (?:you'?re|they'?re|we'?re) dead|after (?:death|you die)|"
        r"no longer need|don'?t need|stop (?:using|doing|saying)|"
        r"is a lie|was wrong|nobody (?:is|was) ready|"
        r"you'?re not ready|we'?re? (?:not ready|cooked|screwed)|"
        r"what they don'?t tell you|"
        r"the (?:truth|real reason|dark side|hidden|secret)|"
        r"insane|terrifying|shocking|unbelievable|mind.?blowing|"
        r"game.?changer|changes everything|end of|"
        r"worst|orwellian|dystopi|creepy|scary|eerie|haunting|"
        r"quit\s+\w+\s+over|fleeing|exodus|revolt|"
        r"replaced by (?:ai|robot|machine|bot))\b",
        re.IGNORECASE,
    ),
    "geopolitical_angle": re.compile(
        r"\b(?:china|russia|eu|europe|india|japan|korea|us|america|uk|"
        r"congress|senate|white house|pentagon|military|government|"
        r"regulation|ban(?:s|ned)?|sanctions?|trade war|"
        r"arms? race|cold war|espionage|surveillance|"
        r"national security|classified|export controls?)\b"
        r".*\b(?:ai|artificial intelligence|gpt|llm|deepfake)\b|"
        r"\b(?:ai|artificial intelligence|gpt|llm|deepfake)\b"
        r".*\b(?:china|russia|eu|europe|india|japan|korea|us|america|uk|"
        r"congress|senate|white house|pentagon|military|government|"
        r"regulation|ban(?:s|ned)?|sanctions?|trade war)\b",
        re.IGNORECASE,
    ),
    "listicle_utility": re.compile(
        r"\b\d+\s+(?:best|top|free|new|essential|must.?have|powerful|"
        r"amazing|incredible|useful|hidden|secret|game.?changing)\s+"
        r"(?:ai|gpt|prompt|tool|app|website|feature|trick|hack|tip|way|thing|resource|extension)s?\b|"
        r"\b\d+\s+(?:prompt|tool|app|website|feature|trick|hack|tip|way|thing|resource|extension)s?\s+"
        r"(?:that|to|for|you)\b",
        re.IGNORECASE,
    ),
    "stop_doing_x": re.compile(
        r"\bstop\s+(?:saying|using|doing|asking|typing|writing|sending)\b|"
        r"\bnever\s+(?:say|use|do|ask|type|write|send)\s+(?:this|that|these)\b|"
        r"\byou'?re\s+using\s+(?:it|chatgpt|ai|gpt)\s+wrong\b",
        re.IGNORECASE,
    ),
    "before_after": re.compile(
        r"\bbefore\s+(?:and\s+)?after\b|"
        r"\b(?:vs\.?|versus|compared to|transformation|upgraded|evolved|"
        r"went from|turned (?:into|from))\b",
        re.IGNORECASE,
    ),
}

# Content type bonus patterns
_CONTENT_TYPE_PATTERNS: Dict[str, re.Pattern] = {
    "has_video_demo": re.compile(
        r"\b(?:demo|demonstration|showcase|watch|video|clip|footage|"
        r"robot(?:ic)?s?\b.*(?:walk|run|jump|dance|move|talk|serve|cook|fold)|"
        r"real.?time|live demo|in action)\b",
        re.IGNORECASE,
    ),
    "has_visual_output": re.compile(
        r"\b(?:generated|created|made|produced)\s+(?:a\s+)?(?:image|video|art|"
        r"music|song|film|movie|animation|render|photo|portrait|painting|"
        r"trailer|poster)\b|"
        r"\b(?:ai[\s-]?(?:generated|art|image|video|music|animation))\b|"
        r"\b(?:midjourney|dall.?e|stable.?diffusion|sora|kling|runway|flux|"
        r"veo|wan\s?\d|hailuo|pika)\b",
        re.IGNORECASE,
    ),
    "has_recognizable_face": re.compile(
        r"\b(?:elon\s?musk|sam\s?altman|mark\s?zuckerberg|jeff\s?bezos|"
        r"jensen\s?huang|sundar\s?pichai|satya\s?nadella|tim\s?cook|"
        r"dario\s?amodei|demis\s?hassabis|bill\s?gates|"
        r"trump|biden|obama|pope|musk|zuck|bezos|altman)\b",
        re.IGNORECASE,
    ),
}


def score_virality_fit(item: Dict, config: Dict[str, Any]) -> float:
    """Score how well a story matches viral Instagram hook formulas.

    Examines title + summary against regex patterns for each hook formula
    defined in config/scoring_weights.yaml. Multiple matches stack,
    capped at max_score (1.0).

    Returns a float between 0.0 and 1.0.
    """
    vf_config = config.get("virality_fit", {})
    hook_weights = vf_config.get("hook_formulas", {})
    bonus_weights = vf_config.get("content_type_bonus", {})
    max_score = vf_config.get("max_score", 1.0)

    # Combine title + summary for pattern matching
    title = item.get("title", "")
    summary = item.get("summary", "")
    text = f"{title} {summary}".strip()

    if not text:
        return 0.0

    total = 0.0
    matched_hooks = []

    # Check each hook formula
    for hook_name, weight in hook_weights.items():
        pattern = _HOOK_PATTERNS.get(hook_name)
        if pattern and pattern.search(text):
            total += weight
            matched_hooks.append(hook_name)

    # Check content type bonuses
    for bonus_name, weight in bonus_weights.items():
        pattern = _CONTENT_TYPE_PATTERNS.get(bonus_name)
        if pattern and pattern.search(text):
            total += weight
            matched_hooks.append(f"+{bonus_name}")

    capped = min(total, max_score)

    if matched_hooks:
        logger.debug("Virality %.2f for '%s': %s", capped, title[:60], ", ".join(matched_hooks))

    return capped


# ---------------------------------------------------------------------------
# Topic Weight Scoring — audience interest multiplier from topic_weights.yaml
# ---------------------------------------------------------------------------

# Keyword-to-topic mapping for detecting which topic a story belongs to.
# Each topic has a list of regex patterns; first match wins (highest weight).
# Patterns are ordered so more specific topics match before generic ones.
_TOPIC_KEYWORD_MAP: Dict[str, List[re.Pattern]] = {
    "tools": [
        re.compile(
            r"\b(?:tool|app|extension|plugin|chatgpt|gemini|claude|copilot|perplexity|midjourney|dall.?e|sora|cursor|v0|bolt)\b",
            re.IGNORECASE,
        ),
    ],
    "human_impact": [
        re.compile(
            r"\b(?:job|jobs|worker|employee|teacher|doctor|artist|farmer|student|fired|laid off|layoff|replace[ds]?|unemployment|hiring|career)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b\d{1,3}[\s-]?year[\s-]?old\b", re.IGNORECASE),
    ],
    "pop_culture": [
        re.compile(
            r"\b(?:movie|film|music|song|anime|game|celebrity|hollywood|bollywood|netflix|disney|marvel|star wars|harry potter|naruto|ghibli|tiktok|youtube|spotify)\b",
            re.IGNORECASE,
        ),
    ],
    "money": [
        re.compile(r"\$\d[\d,]*\.?\d*\s*[bmkt](?:illion|rillion)?|\$\d[\d,]*\.?\d*", re.IGNORECASE),
        re.compile(
            r"\b(?:funding|valuation|revenue|billion|million|investment|acquisition|ipo|profit|salary|cost|pricing|subscription)\b",
            re.IGNORECASE,
        ),
    ],
    "creative": [
        re.compile(
            r"\b(?:generated|art|image|video|animation|render|deepfake|music|visual|painting|portrait|trailer|poster)\b.*\b(?:ai|gpt|model|diffusion)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:ai|gpt|model|diffusion)\b.*\b(?:generated|art|image|video|animation|render|deepfake|music|visual)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:midjourney|dall.?e|stable.?diffusion|sora|kling|runway|flux|veo|wan\s?\d|hailuo|pika)\b",
            re.IGNORECASE,
        ),
    ],
    "prompts": [
        re.compile(r"\b(?:prompt|prompts|prompting|prompt engineering|system prompt)\b", re.IGNORECASE),
    ],
    "products": [
        re.compile(
            r"\b(?:launch|launched|launches|release|released|feature|features|update|updated|beta|early access|waitlist|product)\b",
            re.IGNORECASE,
        ),
    ],
    "llm": [
        re.compile(
            r"\b(?:gpt[\s-]?\d|claude|gemini|llama|mistral|phi|qwen|deepseek|llm|language model|chatbot|chat\s?bot)\b",
            re.IGNORECASE,
        ),
    ],
    "agents": [
        re.compile(
            r"\b(?:agent|agents|agentic|autonomous|multi.?agent|auto.?gpt|crew.?ai|mcp|computer use|tool use)\b",
            re.IGNORECASE,
        ),
    ],
    "ugc": [
        re.compile(
            r"\b(?:someone|guy|woman|man|kid|user|creator)\s+(?:just\s+)?(?:made|created|built|generated|designed)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\blook what\b|\bwait (?:until|till) you see\b", re.IGNORECASE),
    ],
    "controversial": [
        re.compile(
            r"\b(?:backlash|outrage|scandal|controversy|controversial|debate|sued|lawsuit|ban(?:ned|s)?|censorship|protest|boycott)\b",
            re.IGNORECASE,
        ),
    ],
    "robots": [
        re.compile(
            r"\b(?:robot|humanoid|boston dynamics|figure|optimus|unitree|bipedal|quadruped|exoskeleton)\b",
            re.IGNORECASE,
        ),
    ],
    "safety": [
        re.compile(
            r"\b(?:safety|alignment|guardrail|jailbreak|red.?team|responsible ai|ai risk|existential|doom|agi|superintelligence)\b",
            re.IGNORECASE,
        ),
    ],
    "industry": [
        re.compile(
            r"\b(?:announced?|report|partnership|deal|collaboration|conference|summit|keynote)\b", re.IGNORECASE
        ),
    ],
    "policy": [
        re.compile(
            r"\b(?:regulation|regulatory|legislation|congress|senate|eu ai act|executive order|compliance|gdpr|copyright)\b",
            re.IGNORECASE,
        ),
    ],
    "research": [
        re.compile(
            r"\b(?:paper|arxiv|research|study|findings|peer.?review|authors|abstract|methodology)\b", re.IGNORECASE
        ),
    ],
    "data": [
        re.compile(
            r"\b(?:dataset|benchmark|mmlu|hellaswag|humaneval|leaderboard|evaluation|metrics|accuracy|perplexity)\b",
            re.IGNORECASE,
        ),
    ],
    "infrastructure": [
        re.compile(
            r"\b(?:data.?center|gpu|tpu|chip|semiconductor|nvidia|amd|intel|cloud|compute|cluster|inference|training cost)\b",
            re.IGNORECASE,
        ),
    ],
}

# Relatability signal patterns — additive bonuses applied after topic matching
_RELATABILITY_PATTERNS: Dict[str, re.Pattern] = {
    "has_dollar_amount": re.compile(
        r"\$\d[\d,]*\.?\d*\s*[bmkt](?:illion|rillion)?|\$\d[\d,]*\.?\d*",
        re.IGNORECASE,
    ),
    "has_person_name": re.compile(
        r"\b\d{1,3}[\s-]?year[\s-]?old\b|"
        r"\b(?:elon\s?musk|sam\s?altman|mark\s?zuckerberg|jeff\s?bezos|"
        r"jensen\s?huang|sundar\s?pichai|satya\s?nadella|tim\s?cook|"
        r"dario\s?amodei|demis\s?hassabis|bill\s?gates|"
        r"grandma|grandmother|grandpa|grandfather|teen|student|teacher|farmer)\b",
        re.IGNORECASE,
    ),
    "has_pop_culture_ref": re.compile(
        r"\b(?:game of thrones|naruto|harry potter|marvel|disney|star wars|"
        r"lord of the rings|pokemon|minecraft|studio ghibli|avengers|"
        r"breaking bad|batman|spider.?man|taylor swift|"
        r"beyonce|drake|kanye|kardashian|stranger things|"
        r"squid game|one piece|gta|fortnite|zelda|mario|sonic|"
        r"simpsons|south park|rick and morty|super bowl|oscar|grammy|"
        r"barbie|oppenheimer|deadpool|joker)\b",
        re.IGNORECASE,
    ),
    "has_how_to_angle": re.compile(
        r"\b(?:how to|you can now|try this|here'?s how|step.?by.?step|"
        r"just use|all you need|simple trick|pro tip|hack)\b",
        re.IGNORECASE,
    ),
    "has_emotional_hook": re.compile(
        r"\b(?:incredible|terrifying|mind.?blowing|insane|shocking|"
        r"unbelievable|game.?changer|jaw.?dropping|stunning|amazing|"
        r"heartbreaking|devastating|beautiful|nightmare|horrifying)\b",
        re.IGNORECASE,
    ),
    "has_before_after": re.compile(
        r"\bbefore\s+(?:and\s+)?after\b|\btransformation\b|\bwent from\b",
        re.IGNORECASE,
    ),
    "has_creator_credit": re.compile(
        r"@\w+|\bby\s+\w+\b|\ba\s+\d{1,3}[\s-]?year[\s-]?old\b",
        re.IGNORECASE,
    ),
    "has_ugc_showcase": re.compile(
        r"\b(?:made with|created using|generated with|built with|"
        r"powered by|trained on|fine.?tuned)\b",
        re.IGNORECASE,
    ),
    "has_visual_ai_output": re.compile(
        r"\b(?:ai[\s-]?generated|ai[\s-]?art|ai[\s-]?image|ai[\s-]?video|ai[\s-]?music|"
        r"ai[\s-]?animation|midjourney|dall.?e|stable.?diffusion|sora|flux)\b",
        re.IGNORECASE,
    ),
    "has_controversy": re.compile(
        r"\b(?:debate|backlash|divided|controversial|outrage|scandal|"
        r"sued|lawsuit|protest|boycott|revolt)\b",
        re.IGNORECASE,
    ),
}

# Anti-pattern (deprioritize) signal patterns — applied as penalties
_DEPRIORITIZE_PATTERNS: Dict[str, re.Pattern] = {
    "pure_api_announcement": re.compile(
        r"\b(?:api|sdk|endpoint|webhook|rest\s?api|graphql)\s+"
        r"(?:v\d|version|release|update|changelog|deprecat)\b|"
        r"\breleased?\s+v\d+\.\d+\b",
        re.IGNORECASE,
    ),
    "benchmark_only": re.compile(
        r"\b(?:achieves?|scores?|reaches?|surpass(?:es|ed)?)\s+"
        r"\d+\.?\d*\s*%?\s*(?:on|in|at)\s+"
        r"(?:mmlu|hellaswag|humaneval|gsm|math|winogrande|arc|truthfulqa|"
        r"big.?bench|mt.?bench|chatbot arena|lmsys)\b",
        re.IGNORECASE,
    ),
    "internal_tooling": re.compile(
        r"\b(?:developer console|admin panel|dashboard|internal tool|"
        r"devops|ci.?cd|deployment pipeline|infrastructure update)\b",
        re.IGNORECASE,
    ),
    "corporate_restructuring": re.compile(
        r"\b(?:vp of|head of|chief|cto|cfo|coo|board of directors)\s+"
        r"(?:leaves?|departs?|joins?|appointed|resigned?|steps? down)\b|"
        r"\b(?:restructur|reorganiz|reshuffl)\b",
        re.IGNORECASE,
    ),
    "paper_abstract": re.compile(
        r"\b(?:we propose|we present|our method|this paper|"
        r"in this work|we introduce|we demonstrate|"
        r"state.?of.?the.?art|sota|ablation stud|"
        r"experimental results show|we evaluate)\b",
        re.IGNORECASE,
    ),
}


def detect_topics(item: Dict, topic_config: Dict[str, float]) -> List[Tuple[str, float]]:
    """Detect which topics a story matches using keyword patterns.

    Returns a list of (topic_name, topic_weight) tuples sorted by weight
    descending. Only returns topics that exist in topic_config.
    """
    title = item.get("title", "")
    summary = item.get("summary", "")
    text = f"{title} {summary}".strip()

    if not text:
        return []

    matched = []
    for topic_name, patterns in _TOPIC_KEYWORD_MAP.items():
        if topic_name not in topic_config:
            continue
        for pattern in patterns:
            if pattern.search(text):
                matched.append((topic_name, topic_config[topic_name]))
                break  # One match per topic is enough

    matched.sort(key=lambda x: x[1], reverse=True)
    return matched


def detect_relatability_signals(item: Dict, signal_weights: Dict[str, float]) -> Tuple[float, List[str]]:
    """Detect relatability boost signals in a story's title and summary.

    Returns (total_bonus, list_of_matched_signal_names). Bonuses are additive.
    """
    title = item.get("title", "")
    summary = item.get("summary", "")
    text = f"{title} {summary}".strip()

    if not text:
        return 0.0, []

    total_bonus = 0.0
    matched = []

    for signal_name, pattern in _RELATABILITY_PATTERNS.items():
        weight = signal_weights.get(signal_name, 0.0)
        if weight > 0 and pattern.search(text):
            total_bonus += weight
            matched.append(signal_name)

    return total_bonus, matched


def detect_deprioritize_signals(item: Dict, signal_weights: Dict[str, float]) -> Tuple[float, List[str]]:
    """Detect anti-pattern signals that should penalize a story's score.

    Returns (total_penalty, list_of_matched_signal_names). Penalties are
    negative values (additive).
    """
    title = item.get("title", "")
    summary = item.get("summary", "")
    text = f"{title} {summary}".strip()

    if not text:
        return 0.0, []

    total_penalty = 0.0
    matched = []

    for signal_name, pattern in _DEPRIORITIZE_PATTERNS.items():
        weight = signal_weights.get(signal_name, 0.0)
        if weight < 0 and pattern.search(text):
            total_penalty += weight
            matched.append(signal_name)

    return total_penalty, matched


def compute_topic_multiplier(
    item: Dict,
    topic_config: Dict[str, float],
    relatability_weights: Dict[str, float],
    deprioritize_weights: Dict[str, float],
    min_weight: float = 0.2,
) -> Tuple[float, Dict[str, Any]]:
    """Compute the final topic-based multiplier for a story's composite score.

    The multiplier combines:
    1. Highest matching topic weight (from topic_weights.yaml topics)
    2. Additive relatability bonuses (from relatability_signals)
    3. Additive deprioritize penalties (from deprioritize_signals)

    Returns (multiplier, details_dict) where details_dict contains debug info.
    The multiplier is floored at min_weight and has no ceiling (relatability
    can push it above 1.0).
    """
    # Step 1: Find the highest matching topic weight
    matched_topics = detect_topics(item, topic_config)
    if matched_topics:
        best_topic, best_weight = matched_topics[0]
    else:
        best_topic = "unknown"
        best_weight = 0.5  # Neutral default for unrecognized topics

    # Step 2: Relatability bonuses (additive on top of topic weight)
    rel_bonus, rel_signals = detect_relatability_signals(item, relatability_weights)

    # Step 3: Deprioritize penalties (additive, negative values)
    depri_penalty, depri_signals = detect_deprioritize_signals(item, deprioritize_weights)

    # Combine: topic_weight + relatability_bonus + deprioritize_penalty
    # Capped at 1.5x to prevent unbounded score inflation from stacking signals
    MAX_TOPIC_MULTIPLIER = 1.5
    raw_multiplier = best_weight + rel_bonus + depri_penalty
    final_multiplier = min(MAX_TOPIC_MULTIPLIER, max(min_weight, raw_multiplier))

    details = {
        "topic": best_topic,
        "topic_weight": round(best_weight, 4),
        "all_topics": [t[0] for t in matched_topics],
        "relatability_bonus": round(rel_bonus, 4),
        "relatability_signals": rel_signals,
        "deprioritize_penalty": round(depri_penalty, 4),
        "deprioritize_signals": depri_signals,
        "raw_multiplier": round(raw_multiplier, 4),
        "final_multiplier": round(final_multiplier, 4),
    }

    return final_multiplier, details


# ---------------------------------------------------------------------------
# Minimum Quality Filter — removes low-effort Reddit posts, memes, etc.
# ---------------------------------------------------------------------------

# Minimum title length to be considered a real story (not a meme/shitpost)
MIN_TITLE_LENGTH = 25

# Patterns that indicate low-quality content (questions, memes, reactions)
_LOW_QUALITY_PATTERNS = re.compile(
    r"^(?:haha|lol|lmao|omg|wtf|bruh|rip|oof|yikes|damn)\b|"
    r"^(?:is\s|are\s|can\s|does\s|do\s|should\s|would\s|how\s|what\s|why\s|when\s|where\s|who\s)"
    r"(?!.*\b(?:replace|destroy|kill|end|eliminate|worry|die|dead|doomed)\b)|"
    r"^(?:me when|when you|pov:|mfw|tfw)\b|"
    r"^(?:upvote|help|eli5|ama)\b",
    re.IGNORECASE,
)

# Reddit/aggregator domains where filtering is applied
_FILTER_DOMAINS = {"reddit.com", "old.reddit.com", "www.reddit.com"}


def filter_low_quality(items: List[Dict]) -> Tuple[List[Dict], int]:
    """Remove low-effort posts (short titles, memes, questions) from aggregator sources.

    Only filters items from Reddit-like aggregator domains. Primary sources and
    high-authority sources are never filtered.
    """
    kept = []
    removed = 0
    for item in items:
        domain = item.get("domain", "")
        title = item.get("title", "").strip()

        # Only filter aggregator sources
        if domain in _FILTER_DOMAINS:
            # Too short
            if len(title) < MIN_TITLE_LENGTH:
                removed += 1
                logger.debug("Filtered (short title): '%s'", title[:60])
                continue
            # Matches low-quality pattern
            if _LOW_QUALITY_PATTERNS.search(title):
                removed += 1
                logger.debug("Filtered (low-quality pattern): '%s'", title[:60])
                continue

        kept.append(item)

    if removed > 0:
        logger.info("Quality filter: removed %d low-quality items (%d kept)", removed, len(kept))
    return kept, removed


def rank_items(items: List[Dict], config: Dict[str, Any]) -> List[Dict]:
    """Score and rank all items using authority, recency, novelty, virality_fit, and topic weights.

    Scoring happens in two phases:
    1. Base composite score: weighted sum of authority, recency, novelty, virality_fit
    2. Topic multiplier: the base score is multiplied by a topic-based factor that
       accounts for audience interest (topic weight), relatability boosts, and
       anti-pattern penalties from config/topic_weights.yaml
    """
    weights = config.get(
        "weights",
        {
            "virality_fit": 0.35,
            "recency": 0.25,
            "novelty": 0.20,
            "authority": 0.20,
        },
    )
    authority_map = config.get("authority_map", {"default": 0.4})
    decay_hours = config.get("recency_decay_hours", 24)
    threshold = config.get("novelty_threshold", 0.85)

    # Load topic weights for audience-interest scoring
    topic_weights_config = load_topic_weights()
    topic_config = topic_weights_config.get("topics", {})
    relatability_weights = topic_weights_config.get("relatability_signals", {})
    deprioritize_weights = topic_weights_config.get("deprioritize_signals", {})
    min_topic_weight = topic_weights_config.get("min_weight", 0.2)
    topic_scoring_enabled = bool(topic_config)

    if topic_scoring_enabled:
        logger.info(
            "Topic scoring enabled: %d topics, %d relatability signals, %d deprioritize signals",
            len(topic_config),
            len(relatability_weights),
            len(deprioritize_weights),
        )
    else:
        logger.info("Topic scoring disabled (no topic_weights.yaml or empty topics)")

    virality_matches = 0
    topic_boosted = 0
    topic_penalized = 0

    for item in items:
        auth = score_authority(item, authority_map)
        rec = score_recency(item, decay_hours)
        nov = score_novelty(item, items, threshold)
        vf = score_virality_fit(item, config)

        if vf > 0:
            virality_matches += 1

        item["scores"] = {
            "authority": round(auth, 4),
            "recency": round(rec, 4),
            "novelty": round(nov, 4),
            "virality_fit": round(vf, 4),
        }

        base_score = (
            weights.get("authority", 0.20) * auth
            + weights.get("recency", 0.25) * rec
            + weights.get("novelty", 0.20) * nov
            + weights.get("virality_fit", 0.35) * vf
        )

        # Apply topic multiplier if topic scoring is enabled
        if topic_scoring_enabled:
            multiplier, topic_details = compute_topic_multiplier(
                item,
                topic_config,
                relatability_weights,
                deprioritize_weights,
                min_topic_weight,
            )
            final_score = base_score * multiplier

            item["scores"]["base_composite"] = round(base_score, 4)
            item["scores"]["topic_multiplier"] = round(multiplier, 4)
            item["topic_scoring"] = topic_details

            if multiplier > 1.0:
                topic_boosted += 1
            elif multiplier < 0.5:
                topic_penalized += 1

            logger.debug(
                "Topic %.2fx for '%s': topic=%s (%.2f) + rel=%.2f + depri=%.2f",
                multiplier,
                item.get("title", "")[:50],
                topic_details["topic"],
                topic_details["topic_weight"],
                topic_details["relatability_bonus"],
                topic_details["deprioritize_penalty"],
            )
        else:
            final_score = base_score

        item["composite_score"] = round(final_score, 4)

    logger.info("Virality fit: %d/%d items matched at least one hook formula", virality_matches, len(items))
    if topic_scoring_enabled:
        logger.info(
            "Topic scoring: %d boosted (>1.0x), %d penalized (<0.5x), %d neutral",
            topic_boosted,
            topic_penalized,
            len(items) - topic_boosted - topic_penalized,
        )

    items.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
    for rank, item in enumerate(items, 1):
        item["rank"] = rank

    return items


def main():
    parser = argparse.ArgumentParser(description="Deduplicate and rank parsed items")
    parser.add_argument("--run-id", required=True, help="Unique run identifier")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    run_dir = PROJECT_ROOT / ".tmp" / "runs" / args.run_id
    parsed_path = run_dir / "parsed_items.json"

    if not parsed_path.exists():
        logger.error("parsed_items.json not found at %s. Run parse_extract.py first.", parsed_path)
        sys.exit(1)

    with open(parsed_path) as f:
        parsed = json.load(f)

    items = parsed.get("items", [])
    logger.info("Starting deduplication on %d items", len(items))
    total_start = time.time()

    # Quarantine items with injection flags (security: prevent poisoned content)
    flagged_items = [i for i in items if i.get("injection_flags")]
    if flagged_items:
        items = [i for i in items if not i.get("injection_flags")]
        logger.warning(
            "Injection filter: quarantined %d items with injection flags (%d remaining)",
            len(flagged_items),
            len(items),
        )
        # Write flagged items for manual review
        flagged_path = run_dir / "quarantined_injection_flags.json"
        flagged_path.write_text(
            json.dumps(
                [
                    {"title": i.get("title", "")[:100], "url": i.get("url", ""), "flags": i.get("injection_flags", [])}
                    for i in flagged_items
                ],
                indent=2,
            )
        )
    injection_quarantined = len(flagged_items)

    config = load_weights()
    threshold = config.get("novelty_threshold", 0.85)

    # Three-pass dedup with timing
    t0 = time.time()
    items, url_removed = dedup_pass_url(items)
    url_time = round(time.time() - t0, 2)

    t0 = time.time()
    items, title_removed = dedup_pass_title(items, threshold)
    title_time = round(time.time() - t0, 2)

    # Quality filter: remove low-effort Reddit posts, memes, questions
    t0 = time.time()
    items, quality_removed = filter_low_quality(items)
    quality_filter_time = round(time.time() - t0, 2)

    # Score and rank (before clustering so composite_score is available for cluster ranking)
    t0 = time.time()
    items = rank_items(items, config)
    scoring_time = round(time.time() - t0, 2)

    # Pass 3: Topic clustering (annotates items, does not remove them)
    t0 = time.time()
    items, clustering_stats = cluster_stories(items, config)
    clustering_time = round(time.time() - t0, 2)

    total_time = round(time.time() - total_start, 2)

    # Score distribution for observability
    scores = [item.get("composite_score", 0) for item in items]
    score_stats = {}
    if scores:
        scores_sorted = sorted(scores, reverse=True)
        score_stats = {
            "max": round(scores_sorted[0], 4),
            "min": round(scores_sorted[-1], 4),
            "median": round(scores_sorted[len(scores_sorted) // 2], 4),
            "top5_avg": round(sum(scores_sorted[:5]) / min(5, len(scores_sorted)), 4),
        }

    # Virality distribution for observability
    vf_scores = [item.get("scores", {}).get("virality_fit", 0) for item in items]
    vf_nonzero = [s for s in vf_scores if s > 0]
    virality_stats = {
        "items_with_hooks": len(vf_nonzero),
        "items_without_hooks": len(vf_scores) - len(vf_nonzero),
        "avg_virality_fit": round(sum(vf_scores) / max(1, len(vf_scores)), 4),
        "max_virality_fit": round(max(vf_scores) if vf_scores else 0, 4),
    }

    # Topic scoring distribution for observability
    topic_stats = {}
    topic_multipliers = [item.get("scores", {}).get("topic_multiplier", 1.0) for item in items]
    if any(item.get("topic_scoring") for item in items):
        topic_distribution = defaultdict(int)
        for item in items:
            topic_info = item.get("topic_scoring", {})
            primary_topic = topic_info.get("topic", "unknown")
            topic_distribution[primary_topic] += 1

        boosted_count = sum(1 for m in topic_multipliers if m > 1.0)
        penalized_count = sum(1 for m in topic_multipliers if m < 0.5)
        topic_stats = {
            "avg_multiplier": round(sum(topic_multipliers) / max(1, len(topic_multipliers)), 4),
            "max_multiplier": round(max(topic_multipliers), 4),
            "min_multiplier": round(min(topic_multipliers), 4),
            "boosted_count": boosted_count,
            "penalized_count": penalized_count,
            "topic_distribution": dict(topic_distribution),
        }

    output = {
        "run_id": args.run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input_count": parsed.get("item_count", 0),
        "output_count": len(items),
        "dedup_stats": {
            "injection_quarantined": injection_quarantined,
            "url_duplicates_removed": url_removed,
            "title_duplicates_removed": title_removed,
            "quality_filtered": quality_removed,
            "content_duplicates_removed": 0,
        },
        "clustering_stats": clustering_stats,
        "score_distribution": score_stats,
        "virality_stats": virality_stats,
        "topic_stats": topic_stats,
        "timing_seconds": {
            "url_dedup": url_time,
            "title_dedup": title_time,
            "quality_filter": quality_filter_time,
            "scoring": scoring_time,
            "clustering": clustering_time,
            "total": total_time,
        },
        "stories": items,
    }

    out_path = run_dir / "ranked_stories.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    logger.info(
        "Ranked %d stories -> %s (%.1fs total: url=%.1fs title=%.1fs score=%.1fs cluster=%.1fs)",
        len(items),
        out_path,
        total_time,
        url_time,
        title_time,
        scoring_time,
        clustering_time,
    )


if __name__ == "__main__":
    main()
