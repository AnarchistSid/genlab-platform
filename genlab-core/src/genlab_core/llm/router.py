"""Multi-provider LLM router with automatic fallback.

Routes tasks to the cheapest appropriate model:
  CREATIVE (Haiku $1/$5)  → hooks, narration, engagement replies
  BULK (GPT-4o-mini $0.15/$0.60) → scoring, classification, adaptation
  VIDEO (Gemini 2.5 Flash) → video analysis
  NANO (GPT-4.1-nano $0.10/$0.40) → trivial extraction

Usage:
    from genlab_core.llm.router import llm_call
    result = llm_call("hook_generation", system_prompt, user_prompt)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# Task → tier mapping
TASK_TIER: dict[str, str] = {
    # CREATIVE — audience-facing, quality-critical
    "generate_hooks": "creative",
    "hook_generation": "creative",
    "write_post_content": "creative",
    "narration_script": "creative",
    "engagement_reply": "creative",
    "brand_voice_caption": "creative",
    # BULK — internal processing, not audience-facing
    "affiliate_matching": "bulk",
    "content_scoring": "bulk",
    "caption_variants": "bulk",
    "platform_adaptation": "bulk",
    "trending_classification": "bulk",
    "source_relevance": "bulk",
    "adapt_for_platforms": "bulk",
    "enrich_metadata": "bulk",
    "summarise_article": "bulk",
    # VIDEO — requires video/image input
    "video_analysis": "video",
    "frame_description": "video",
    "product_detection": "video",
    # NANO — trivial extraction
    "extract_entities": "nano",
    "classify_sentiment": "nano",
    "is_english": "nano",
    "extract_game_name": "nano",
    "classify_content_type": "nano",
}

# Tier → model config
TIER_CONFIG: dict[str, dict[str, Any]] = {
    "creative": {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "env_key": "ANTHROPIC_API_KEY",
        "input_cost_per_m": 1.00,
        "output_cost_per_m": 5.00,
    },
    "bulk": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "env_key": "OPENAI_API_KEY",
        "input_cost_per_m": 0.15,
        "output_cost_per_m": 0.60,
    },
    "video": {
        "provider": "google",
        "model": "gemini-2.5-flash",
        "env_key": "GOOGLE_API_KEY",
        "input_cost_per_m": 0.15,
        "output_cost_per_m": 0.60,
    },
    "nano": {
        "provider": "openai",
        "model": "gpt-4.1-nano",
        "env_key": "OPENAI_API_KEY",
        "input_cost_per_m": 0.10,
        "output_cost_per_m": 0.40,
    },
}

# Fallback chain: creative → bulk → nano
FALLBACK_CHAIN = ["creative", "bulk", "nano"]


def get_tier(task: str) -> str:
    """Get the tier for a task. Defaults to 'bulk' for unknown tasks."""
    return TASK_TIER.get(task, "bulk")


def get_model_for_task(task: str) -> tuple[str, str]:
    """Get (provider, model) for a task. Returns the tier's default model."""
    tier = get_tier(task)
    config = TIER_CONFIG.get(tier, TIER_CONFIG["bulk"])
    return config["provider"], config["model"]


def llm_call(
    task: str,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    json_mode: bool = False,
) -> str:
    """Route an LLM call to the appropriate model with automatic fallback.

    Returns the response text. Raises on total failure after all fallbacks.
    """
    tier = get_tier(task)
    tiers_to_try = (
        FALLBACK_CHAIN[FALLBACK_CHAIN.index(tier) :]
        if tier in FALLBACK_CHAIN
        else [tier] + FALLBACK_CHAIN
    )

    last_error = None
    for try_tier in tiers_to_try:
        config = TIER_CONFIG.get(try_tier)
        if not config:
            continue

        api_key = os.environ.get(config["env_key"], "")
        if not api_key:
            logger.debug("[llm-router] No %s for tier %s, trying next", config["env_key"], try_tier)
            continue

        start = time.monotonic()
        try:
            if config["provider"] == "anthropic":
                result = _call_anthropic(
                    config["model"], system_prompt, user_prompt, max_tokens, temperature, api_key
                )
            elif config["provider"] == "openai":
                result = _call_openai(
                    config["model"],
                    system_prompt,
                    user_prompt,
                    max_tokens,
                    temperature,
                    api_key,
                    json_mode,
                )
            elif config["provider"] == "google":
                result = _call_google(
                    config["model"], system_prompt, user_prompt, max_tokens, temperature, api_key
                )
            else:
                continue

            elapsed = time.monotonic() - start
            logger.info("[llm-router] %s → %s/%s (%.1fs)", task, try_tier, config["model"], elapsed)
            return result

        except Exception as e:
            last_error = e
            logger.warning(
                "[llm-router] %s/%s failed: %s — trying next tier",
                try_tier,
                config["model"],
                str(e)[:80],
            )

    raise RuntimeError(f"All LLM tiers failed for task '{task}': {last_error}")


def _call_anthropic(
    model: str, system: str, user: str, max_tokens: int, temperature: float, api_key: str
) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text


def _call_openai(
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    api_key: str,
    json_mode: bool = False,
) -> str:
    import openai

    client = openai.OpenAI(api_key=api_key)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content


def _call_google(
    model: str, system: str, user: str, max_tokens: int, temperature: float, api_key: str
) -> str:
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    gmodel = genai.GenerativeModel(model, system_instruction=system)
    response = gmodel.generate_content(
        user,
        generation_config=genai.GenerationConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        ),
    )
    return response.text
