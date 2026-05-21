"""Compilation planning logic — pure deterministic functions.

Receives scored clips and config dicts, returns a compilation plan.
No I/O — all data passed as arguments for testability.

Transplanted from Gaming Clips with import updates (stable_ids inlined,
setup_logging replaced with standard logging).
"""

import hashlib
import logging
import random
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def _compilation_id(theme: str, date: str, sequence_number: int) -> str:
    """Generate deterministic compilation ID: sha256(theme + date + sequence_number)."""
    raw = f"{theme}{date}{sequence_number}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def filter_eligible_clips(clips: list[dict]) -> list[dict]:
    """Filter clips to those eligible for compilation.

    Excludes: RED tier, no local_path, zero/missing duration.
    """
    eligible = []
    for clip in clips:
        if clip.get("publisher_tier") == "RED":
            continue
        if not clip.get("local_path"):
            continue
        if not clip.get("duration_seconds", 0) > 0:
            continue
        eligible.append(clip)
    return eligible


def detect_clip_theme(clip: dict, theme_config: dict) -> str:
    """Detect the theme of a clip based on keyword matching.

    Combines clip title + description into a lowercase search string and
    checks each theme's keywords (in order: fails, clutch, funny).
    Returns the first matching theme or "mixed" if none match.
    Skips themes with empty keyword lists.
    """
    search_text = (clip.get("title", "") + " " + clip.get("description", "")).lower()

    for theme_name in ["fails", "clutch", "funny"]:
        theme_def = theme_config.get(theme_name, {})
        keywords = theme_def.get("keywords", [])
        if not keywords:
            continue
        for keyword in keywords:
            if keyword.lower() in search_text:
                return theme_name

    return "mixed"


def group_clips_by_theme(
    clips: list[dict],
    theme_config: dict,
) -> dict[str, list]:
    """Group clips by detected theme.

    Returns a dict mapping theme names to lists of clips.
    Only includes themes that have at least 1 clip.
    """
    groups: dict[str, list] = {}
    for clip in clips:
        theme = detect_clip_theme(clip, theme_config)
        if theme not in groups:
            groups[theme] = []
        groups[theme].append(clip)
    return groups


def select_clips_for_compilation(
    clips: list[dict],
    compilation_type: str,
    rules: dict,
    overrides: dict | None = None,
) -> list[dict]:
    """Select clips for a compilation respecting count and duration constraints.

    Prefers game diversity (3+ unique games).
    Returns empty list if minimum requirements not met.

    Args:
        overrides: Optional dict of per-platform overrides. Supported keys:
            target_duration_seconds, min_clips, max_clips.
    """
    type_rules = rules.get("compilation_types", {}).get(compilation_type, {})
    min_clips = type_rules.get("min_clips", 4)
    max_clips = type_rules.get("max_clips", 8)
    target_dur = type_rules.get("target_duration_seconds", 45)
    max_per_game = type_rules.get("max_clips_per_game", None)

    if overrides:
        min_clips = overrides.get("min_clips", min_clips)
        max_clips = overrides.get("max_clips", max_clips)
        target_dur = overrides.get("target_duration_seconds", target_dur)

    eligible = filter_eligible_clips(clips)
    if len(eligible) < min_clips:
        logger.warning("Not enough eligible clips (%d < %d)", len(eligible), min_clips)
        return []

    sorted_clips = sorted(eligible, key=lambda c: c.get("final_score", 0), reverse=True)

    selected = []
    games_seen = set()
    game_counts: dict[str, int] = {}
    total_dur = 0.0

    # First pass: one clip per unique game (for diversity)
    for clip in sorted_clips:
        game = clip.get("game_title", "Unknown")
        if game not in games_seen and len(selected) < max_clips:
            if max_per_game is not None and game_counts.get(game, 0) >= max_per_game:
                continue
            selected.append(clip)
            games_seen.add(game)
            game_counts[game] = game_counts.get(game, 0) + 1
            total_dur += clip.get("duration_seconds", 0)
            if total_dur >= target_dur and len(selected) >= min_clips:
                break

    # Second pass: fill remaining slots
    for clip in sorted_clips:
        if clip in selected:
            continue
        if len(selected) >= max_clips:
            break
        if total_dur >= target_dur and len(selected) >= min_clips:
            break
        game = clip.get("game_title", "Unknown")
        if max_per_game is not None and game_counts.get(game, 0) >= max_per_game:
            continue
        selected.append(clip)
        game_counts[game] = game_counts.get(game, 0) + 1
        total_dur += clip.get("duration_seconds", 0)

    if len(selected) < min_clips:
        return []

    return selected


def order_by_pacing(clips: list[dict], pacing: str) -> list[dict]:
    """Order clips according to pacing strategy."""
    if pacing == "escalating":
        return sorted(clips, key=lambda c: c.get("final_score", 0))
    elif pacing == "wave":
        sorted_clips = sorted(clips, key=lambda c: c.get("final_score", 0))
        wave = []
        low_idx, high_idx = 0, len(sorted_clips) - 1
        toggle = True
        while low_idx <= high_idx:
            if toggle:
                wave.append(sorted_clips[low_idx])
                low_idx += 1
            else:
                wave.append(sorted_clips[high_idx])
                high_idx -= 1
            toggle = not toggle
        return wave
    elif pacing == "front_loaded":
        # Best clip first, then ascending order for the rest
        sorted_clips = sorted(clips, key=lambda c: c.get("final_score", 0))
        if not sorted_clips:
            return []
        best = sorted_clips.pop()  # remove the highest
        return [best] + sorted_clips  # best first, rest ascending
    else:
        return list(clips)


def assign_transitions(
    clips: list[dict],
    transition_weights: dict,
) -> list[dict]:
    """Assign transition_in to each clip based on configured weights."""
    if not clips:
        return []

    transitions = list(transition_weights.keys())
    weights = [transition_weights[t] for t in transitions]

    result = []
    for i, clip in enumerate(clips):
        clip = clip.copy()
        if i == 0:
            clip["transition_in"] = "hard_cut"
        else:
            clip["transition_in"] = random.choices(transitions, weights=weights, k=1)[0]
        result.append(clip)
    return result


def calculate_trim_points(
    clips: list[dict],
    target_duration: float,
    max_single_ratio: float,
) -> list[dict]:
    """Calculate start/end trim points for each clip."""
    max_clip_dur = target_duration * max_single_ratio
    result = []

    for clip in clips:
        clip = clip.copy()
        dur = min(clip.get("duration_seconds", 0), max_clip_dur)
        clip["start_trim_seconds"] = 0.0
        clip["end_trim_seconds"] = dur
        clip["effective_duration_seconds"] = dur
        result.append(clip)

    total = sum(c["effective_duration_seconds"] for c in result)
    if total > target_duration and total > 0:
        scale = target_duration / total
        for clip in result:
            clip["effective_duration_seconds"] = round(
                clip["effective_duration_seconds"] * scale, 2
            )
            clip["end_trim_seconds"] = (
                clip["start_trim_seconds"] + clip["effective_duration_seconds"]
            )

    return result


def generate_commentary_slots(
    clips: list[dict],
    min_lines: int = 3,
) -> list[dict]:
    """Generate commentary slot timestamps and styles."""
    if not clips:
        return []

    slots = []
    total_dur = sum(
        c.get("effective_duration_seconds", c.get("duration_seconds", 0)) for c in clips
    )

    slots.append(
        {
            "timestamp_seconds": 0.0,
            "text": "",
            "style": "intro",
        }
    )

    running_time = 0.0
    styles_cycle = ["hype", "reaction"]
    style_idx = 0
    for _i, clip in enumerate(clips[:-1]):
        running_time += clip.get("effective_duration_seconds", clip.get("duration_seconds", 0))
        if len(slots) < min_lines - 1:
            slots.append(
                {
                    "timestamp_seconds": round(running_time, 2),
                    "text": "",
                    "style": styles_cycle[style_idx % len(styles_cycle)],
                }
            )
            style_idx += 1

    outro_time = max(0.0, total_dur - 3.0)
    slots.append(
        {
            "timestamp_seconds": round(outro_time, 2),
            "text": "",
            "style": "outro",
        }
    )

    return slots


def compute_diversity_score(clips: list[dict]) -> float:
    """Compute diversity multiplier based on unique game count."""
    games = {c.get("game_title", "") for c in clips}
    return 1.15 if len(games) >= 3 else 1.0


def compute_pacing_score(clips: list[dict]) -> float:
    """Score how well clips are ordered (escalating = ideal)."""
    if len(clips) < 2:
        return 1.0

    scores = [c.get("final_score", 0) for c in clips]
    ascending_pairs = sum(1 for i in range(len(scores) - 1) if scores[i] <= scores[i + 1])
    ratio = ascending_pairs / (len(scores) - 1)
    return 0.5 + ratio * 0.5


def build_compilation_plan(
    scored_clips: list[dict],
    compilation_type: str,
    rules: dict,
    scoring_config: dict,
    theme: str = "best_of_day",
    sequence_number: int = 1,
    overrides: dict | None = None,
) -> dict | None:
    """Build a complete compilation plan from scored clips.

    Args:
        scored_clips: Clips with scores and metadata.
        compilation_type: Type key from compilation_rules (e.g. "short_compilation").
        rules: Full compilation rules dict.
        scoring_config: Scoring configuration dict.
        theme: Theme name for this compilation (default "best_of_day").
        sequence_number: Sequence number for ID generation (default 1).
        overrides: Optional dict of per-platform overrides. Supported keys:
            target_duration_seconds, max_single_clip_ratio, pacing,
            min_commentary_lines, min_clips, max_clips.
    """
    type_rules = rules.get("compilation_types", {}).get(compilation_type, {})
    target_dur = type_rules.get("target_duration_seconds", 45)
    max_ratio = type_rules.get("max_single_clip_ratio", 0.4)
    pacing_style = type_rules.get("pacing", "escalating")
    min_commentary = type_rules.get("min_commentary_lines", 3)

    if overrides:
        target_dur = overrides.get("target_duration_seconds", target_dur)
        max_ratio = overrides.get("max_single_clip_ratio", max_ratio)
        pacing_style = overrides.get("pacing", pacing_style)
        min_commentary = overrides.get("min_commentary_lines", min_commentary)

    selected = select_clips_for_compilation(
        scored_clips, compilation_type, rules, overrides=overrides
    )
    if not selected:
        return None

    ordered = order_by_pacing(selected, pacing_style)
    with_transitions = assign_transitions(ordered, rules.get("transitions", {}))
    trimmed = calculate_trim_points(with_transitions, target_dur, max_ratio)
    commentary = generate_commentary_slots(trimmed, min_commentary)

    diversity = compute_diversity_score(trimmed)
    pacing = compute_pacing_score(trimmed)
    total_dur = sum(c["effective_duration_seconds"] for c in trimmed)

    plan_clips = []
    for i, clip in enumerate(trimmed):
        plan_clips.append(
            {
                "clip_id": clip["clip_id"],
                "order": i + 1,
                "start_trim_seconds": clip["start_trim_seconds"],
                "end_trim_seconds": clip["end_trim_seconds"],
                "effective_duration_seconds": clip["effective_duration_seconds"],
                "transition_in": clip["transition_in"],
                "overlay_text": None,
            }
        )

    now = datetime.now(UTC)
    comp_id = _compilation_id(
        theme=theme,
        date=now.strftime("%Y-%m-%d"),
        sequence_number=sequence_number,
    )

    return {
        "compilation_id": comp_id,
        "compilation_type": compilation_type,
        "theme": theme,
        "target_duration_seconds": target_dur,
        "target_platforms": ["youtube_shorts", "instagram_reels", "tiktok", "facebook_reels"],
        "clips": plan_clips,
        "commentary_slots": commentary,
        "music_track_id": None,
        "hook_text": "",
        "estimated_total_duration_seconds": round(total_dur, 2),
        "diversity_score": diversity,
        "pacing_score": pacing,
        "created_at": now.isoformat(),
    }


def build_multi_compilation_plans(
    scored_clips: list[dict],
    compilation_type: str,
    rules: dict,
    scoring_config: dict,
) -> list[dict]:
    """Build multiple themed compilation plans from scored clips.

    Groups clips by theme and builds separate plans for themes with enough
    clips. Remaining clips go into a "mixed" plan. Caps total plans at
    max_compilations from the multi_compilation config.

    Returns a list of 0 to max_compilations plan dicts.
    """
    multi_config = rules.get("multi_compilation", {})
    max_compilations = multi_config.get("max_compilations", 3)
    min_clips_per = multi_config.get("min_clips_per_compilation", 4)

    theme_config = rules.get("themes", {})

    eligible = filter_eligible_clips(scored_clips)
    if len(eligible) < min_clips_per:
        return []

    groups = group_clips_by_theme(eligible, theme_config)

    plans: list[dict] = []
    used_clip_ids: set[str] = set()
    sequence = 1

    # Sort themed groups (non-mixed) by clip count descending to prioritize
    # themes with the most clips
    themed_groups = [
        (theme_name, theme_clips)
        for theme_name, theme_clips in groups.items()
        if theme_name != "mixed"
    ]
    themed_groups.sort(key=lambda x: len(x[1]), reverse=True)

    for theme_name, theme_clips in themed_groups:
        if len(plans) >= max_compilations:
            break
        if len(theme_clips) < min_clips_per:
            continue

        plan = build_compilation_plan(
            theme_clips,
            compilation_type,
            rules,
            scoring_config,
            theme=theme_name,
            sequence_number=sequence,
        )
        if plan is not None:
            plans.append(plan)
            used_clip_ids.update(c["clip_id"] for c in theme_clips)
            sequence += 1

    # Gather remaining clips (mixed group + unused themed clips)
    if len(plans) < max_compilations:
        remaining = [c for c in eligible if c["clip_id"] not in used_clip_ids]
        if len(remaining) >= min_clips_per:
            plan = build_compilation_plan(
                remaining,
                compilation_type,
                rules,
                scoring_config,
                theme="mixed",
                sequence_number=sequence,
            )
            if plan is not None:
                plans.append(plan)

    return plans
