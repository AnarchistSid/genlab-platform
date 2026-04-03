"""Tests for LLM retry logic in write_gaming_content.py."""

from unittest.mock import MagicMock, patch

import anthropic


class TestLLMRetryOnTransientError:
    """Verify LLM calls retry on transient errors and fall back on exhaustion."""

    @patch("niches.gaming.stages.write_gaming_content.anthropic.Anthropic")
    @patch("niches.gaming.stages.write_gaming_content._load_templates")
    @patch("niches.gaming.stages.write_gaming_content.settings")
    @patch("genlab_core.http.retry.time.sleep")
    def test_retries_on_rate_limit_then_succeeds(
        self, mock_sleep, mock_settings, mock_templates, mock_anthropic_cls,
    ):
        """RateLimitError on first call, success on second."""
        from niches.gaming.stages.write_gaming_content import WriteGamingContent

        mock_settings.anthropic_api_key = "sk-test"
        mock_templates.return_value = {
            "hooks": {"formulas": ["Test {game}"]},
            "captions": {"cta_library": ["follow"], "hashtag_pool": ["#gaming"]},
        }

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        valid_hook = "This changes everything for TestGame"
        ok_resp = MagicMock()
        ok_resp.content = [MagicMock(text='{"hook":"' + valid_hook + '","instagram":{"caption":"c","hashtags":["#g"]},"youtube":{"title":"What?","description":"d"},"x_twitter":{"tweet":"t","hashtags":["#g"]},"facebook":{"caption":"f"},"tiktok":{"caption":"t","hashtags":["#g"]}}')]

        mock_client.messages.create.side_effect = [
            anthropic.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429, headers={}),
                body={"error": {"message": "rate limited"}},
            ),
            ok_resp,
        ]

        context = {"stories": [{"title": "TestGame", "summary": "A test"}]}
        stage = WriteGamingContent()
        result = stage.execute(context)

        assert result["stories"][0].get("content") is not None
        assert result["stories"][0]["content"]["hook"] == valid_hook
        assert mock_client.messages.create.call_count == 2

    @patch("niches.gaming.stages.write_gaming_content.anthropic.Anthropic")
    @patch("niches.gaming.stages.write_gaming_content._load_templates")
    @patch("niches.gaming.stages.write_gaming_content.settings")
    @patch("genlab_core.http.retry.time.sleep")
    def test_falls_back_after_3_failures(
        self, mock_sleep, mock_settings, mock_templates, mock_anthropic_cls,
    ):
        """3 transient failures -> falls back to template."""
        from niches.gaming.stages.write_gaming_content import WriteGamingContent

        mock_settings.anthropic_api_key = "sk-test"
        mock_templates.return_value = {
            "hooks": {"formulas": []},
            "captions": {"cta_library": ["follow"], "hashtag_pool": ["#gaming"]},
        }

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = anthropic.InternalServerError(
            message="server error",
            response=MagicMock(status_code=500, headers={}),
            body={"error": {"message": "server error"}},
        )

        context = {"stories": [{"title": "TestGame", "summary": "A test"}]}
        stage = WriteGamingContent()
        result = stage.execute(context)

        # Should have used fallback
        content = result["stories"][0]["content"]
        assert "TestGame" in content["hook"]
        assert mock_client.messages.create.call_count == 3
        assert result["run_stats"]["content_writing"]["failed_count"] == 1

    @patch("niches.gaming.stages.write_gaming_content.anthropic.Anthropic")
    @patch("niches.gaming.stages.write_gaming_content._load_templates")
    @patch("niches.gaming.stages.write_gaming_content.settings")
    def test_does_not_retry_on_auth_error(
        self, mock_settings, mock_templates, mock_anthropic_cls,
    ):
        """AuthenticationError should surface immediately, no retry."""
        from niches.gaming.stages.write_gaming_content import WriteGamingContent

        mock_settings.anthropic_api_key = "sk-bad"
        mock_templates.return_value = {
            "hooks": {"formulas": []},
            "captions": {"cta_library": ["follow"], "hashtag_pool": ["#gaming"]},
        }

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = anthropic.AuthenticationError(
            message="invalid api key",
            response=MagicMock(status_code=401, headers={}),
            body={"error": {"message": "invalid api key"}},
        )

        context = {"stories": [{"title": "TestGame", "summary": "A test"}]}
        stage = WriteGamingContent()
        result = stage.execute(context)

        # Only 1 call — no retry on auth error
        assert mock_client.messages.create.call_count == 1
        assert result["run_stats"]["content_writing"]["failed_count"] == 1
