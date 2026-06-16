"""Regression pin for the 2026-06-17 LLM required-fields retry.

Before this change, ``_complete_and_parse_json`` would happily return
a parsed JSON dict that was missing ``instagram_caption`` and/or
``threads_content`` — the most-constrained fields the LLM frequently
omitted when length targets were hard to satisfy. The empty-field
cascade was:

  LLM returns no instagram_caption
   → ``ig_caption = ""``
   → ``_adapt_instagram`` early-returns on empty caption
   → ``inject_cta`` no-ops on empty caption
   → published IG has no affiliate CTA

PR #273 added a downstream fallback chain so the cascade was no longer
silent. This PR adds an UPSTREAM retry that re-prompts the LLM when
required fields are missing, so the fallback rarely needs to fire.

Order of operations:
1. Tighten the prompt to say "all 6 fields REQUIRED" (improves
   LLM compliance, reduces retries)
2. On JSON-with-missing-fields, retry once with explicit "you omitted
   X, Y, Z — re-generate with them" message
3. Bump max_tokens 800→1200 for safety headroom
4. On second-attempt-also-missing, log WARN + return what we have;
   downstream PR #273 fallback fills the gap
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from genlab_core.writing.video_content_writer import (
    _REQUIRED_LLM_FIELDS,
    _complete_and_parse_json,
)


def _complete_responses(*responses: str) -> MagicMock:
    """Build an LLM client mock that returns the given responses in order."""
    client = MagicMock()
    client.complete = MagicMock(side_effect=list(responses))
    return client


class TestRequiredFieldsRetry:
    def test_required_fields_set_is_complete(self):
        """Pin the set so accidental key renames in the prompt
        trigger a test failure (not silent drift)."""
        assert _REQUIRED_LLM_FIELDS == frozenset(
            {
                "hook",
                "instagram_caption",
                "twitter_content",
                "youtube_content",
                "facebook_content",
                "threads_content",
            }
        )

    def test_first_attempt_complete_returns_immediately(self):
        """Happy path: LLM returns all 6 fields → no retry."""
        complete_response = json.dumps(
            {
                "hook": "Insane play",
                "instagram_caption": "Body text\n\nCTA?\n\n#tag",
                "twitter_content": "Tweet body",
                "youtube_content": "YT title?",
                "facebook_content": "Facebook content " * 10,
                "threads_content": "Threads hot take " * 8,
            }
        )
        client = _complete_responses(complete_response)
        result = _complete_and_parse_json(
            llm_client=client,
            system="sys",
            user="usr",
            max_tokens=1200,
            temperature=0.65,
            niche_id="gaming",
        )
        assert client.complete.call_count == 1
        assert result["instagram_caption"] == "Body text\n\nCTA?\n\n#tag"

    def test_missing_instagram_caption_triggers_retry(self):
        """Anime/movies/sports failure mode: IG omitted."""
        first = json.dumps(
            {
                "hook": "Hook",
                # no instagram_caption
                "twitter_content": "Tweet",
                "youtube_content": "YT?",
                "facebook_content": "FB",
                "threads_content": "TH",
            }
        )
        second = json.dumps(
            {
                "hook": "Hook",
                "instagram_caption": "Now present",
                "twitter_content": "Tweet",
                "youtube_content": "YT?",
                "facebook_content": "FB",
                "threads_content": "TH",
            }
        )
        client = _complete_responses(first, second)
        result = _complete_and_parse_json(
            llm_client=client,
            system="sys",
            user="usr",
            max_tokens=1200,
            temperature=0.65,
            niche_id="anime",
        )
        assert client.complete.call_count == 2
        assert result["instagram_caption"] == "Now present"
        # The retry prompt MUST explicitly call out the missing field
        retry_call = client.complete.call_args_list[1]
        retry_user = retry_call.kwargs["user"]
        assert "instagram_caption" in retry_user
        assert "OMITTED" in retry_user or "required" in retry_user.lower()

    def test_missing_both_ig_and_threads_triggers_retry(self):
        """The most common observed failure mode in prod (90 of 156
        affiliate-matched blueprints had BOTH IG + Threads empty)."""
        first = json.dumps(
            {
                "hook": "Hook",
                "twitter_content": "Tweet",
                "youtube_content": "YT?",
                "facebook_content": "FB",
            }
        )
        second = json.dumps(
            {
                "hook": "Hook",
                "instagram_caption": "IG now present",
                "twitter_content": "Tweet",
                "youtube_content": "YT?",
                "facebook_content": "FB",
                "threads_content": "TH now present",
            }
        )
        client = _complete_responses(first, second)
        result = _complete_and_parse_json(
            llm_client=client,
            system="sys",
            user="usr",
            max_tokens=1200,
            temperature=0.65,
            niche_id="movies",
        )
        assert client.complete.call_count == 2
        assert result["instagram_caption"] == "IG now present"
        assert result["threads_content"] == "TH now present"
        # Retry user MUST mention BOTH missing fields
        retry_user = client.complete.call_args_list[1].kwargs["user"]
        assert "instagram_caption" in retry_user
        assert "threads_content" in retry_user

    def test_empty_string_treated_as_missing(self):
        """A field returned as "" must trigger retry too — empty
        string is downstream-indistinguishable from missing."""
        first = json.dumps(
            {
                "hook": "Hook",
                "instagram_caption": "",  # explicit empty
                "twitter_content": "Tweet",
                "youtube_content": "YT?",
                "facebook_content": "FB",
                "threads_content": "TH",
            }
        )
        second = json.dumps(
            {
                "hook": "Hook",
                "instagram_caption": "Real content now",
                "twitter_content": "Tweet",
                "youtube_content": "YT?",
                "facebook_content": "FB",
                "threads_content": "TH",
            }
        )
        client = _complete_responses(first, second)
        result = _complete_and_parse_json(
            llm_client=client,
            system="sys",
            user="usr",
            max_tokens=1200,
            temperature=0.65,
            niche_id="sports",
        )
        assert client.complete.call_count == 2
        assert result["instagram_caption"] == "Real content now"

    def test_whitespace_only_treated_as_missing(self):
        first = json.dumps(
            {
                "hook": "Hook",
                "instagram_caption": "   ",  # whitespace only
                "twitter_content": "Tweet",
                "youtube_content": "YT?",
                "facebook_content": "FB",
                "threads_content": "TH",
            }
        )
        second = json.dumps(
            {
                "hook": "Hook",
                "instagram_caption": "Real",
                "twitter_content": "Tweet",
                "youtube_content": "YT?",
                "facebook_content": "FB",
                "threads_content": "TH",
            }
        )
        client = _complete_responses(first, second)
        result = _complete_and_parse_json(
            llm_client=client,
            system="sys",
            user="usr",
            max_tokens=1200,
            temperature=0.65,
            niche_id="anime",
        )
        assert client.complete.call_count == 2
        assert result["instagram_caption"] == "Real"

    def test_second_attempt_still_missing_returns_partial(self):
        """If the LLM stubbornly refuses to comply on retry, we
        return what we have. The downstream PR #273 fallback chain
        will fill from related fields (FB → YT → hook → title).

        Logging a WARN is the canonical signal for prompt regression
        — operators can grep for this in metric_collector logs and
        decide whether to tighten the prompt further.
        """
        partial = json.dumps(
            {
                "hook": "Hook",
                "twitter_content": "Tweet",
                "youtube_content": "YT?",
                "facebook_content": "FB",
                # both IG + Threads still missing
            }
        )
        client = _complete_responses(partial, partial)
        result = _complete_and_parse_json(
            llm_client=client,
            system="sys",
            user="usr",
            max_tokens=1200,
            temperature=0.65,
            niche_id="movies",
        )
        # Two attempts made, partial returned
        assert client.complete.call_count == 2
        assert "hook" in result
        assert "instagram_caption" not in result

    def test_json_parse_failure_still_retries(self):
        """Original behavior preserved: JSON parse errors retry once."""
        bad_json = '{"hook": "X", "instagram_caption": "Y" invalid'
        good_json = json.dumps(
            {
                "hook": "X",
                "instagram_caption": "Y",
                "twitter_content": "Z",
                "youtube_content": "A?",
                "facebook_content": "B",
                "threads_content": "C",
            }
        )
        client = _complete_responses(bad_json, good_json)
        result = _complete_and_parse_json(
            llm_client=client,
            system="sys",
            user="usr",
            max_tokens=1200,
            temperature=0.65,
            niche_id="gaming",
        )
        assert client.complete.call_count == 2
        assert result["instagram_caption"] == "Y"
        # The retry prompt for JSON-parse failure should mention "JSON",
        # not the missing-fields wording
        retry_user = client.complete.call_args_list[1].kwargs["user"]
        assert "JSON" in retry_user
