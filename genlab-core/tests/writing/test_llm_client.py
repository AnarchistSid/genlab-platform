"""Tests for AnthropicLLMClient adapter."""

from unittest.mock import MagicMock, patch

from genlab_core.writing.llm_client import AnthropicLLMClient


class TestAnthropicLLMClient:
    def test_init_with_explicit_key(self):
        client = AnthropicLLMClient(api_key="sk-test-123", model="claude-haiku-4-5-20251001")
        assert client._api_key == "sk-test-123"
        assert client._model == "claude-haiku-4-5-20251001"
        assert client._client is None  # lazy init

    def test_init_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
        client = AnthropicLLMClient()
        assert client._api_key == "sk-from-env"

    def test_init_default_model(self):
        client = AnthropicLLMClient(api_key="sk-test")
        assert client._model == "claude-haiku-4-5-20251001"

    def test_is_available_true(self):
        client = AnthropicLLMClient(api_key="sk-test")
        assert client.is_available is True

    def test_is_available_false(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        client = AnthropicLLMClient(api_key="")
        assert client.is_available is False

    @patch("genlab_core.writing.llm_client.anthropic", create=True)
    def test_complete_calls_api(self, mock_anthropic_module):
        """Test that complete() calls the Anthropic Messages API correctly."""
        # Set up the mock chain
        mock_client_instance = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client_instance

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Generated content here")]
        mock_client_instance.messages.create.return_value = mock_response

        # Patch the import inside _ensure_client
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            client = AnthropicLLMClient(api_key="sk-test", model="claude-haiku-4-5-20251001")
            result = client.complete(
                system="You are a writer.",
                user="Write something.",
                max_tokens=500,
                temperature=0.7,
            )

        assert result == "Generated content here"
        mock_client_instance.messages.create.assert_called_once_with(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            temperature=0.7,
            system="You are a writer.",
            messages=[{"role": "user", "content": "Write something."}],
        )

    @patch("genlab_core.writing.llm_client.anthropic", create=True)
    def test_client_initialized_once(self, mock_anthropic_module):
        """Test that the Anthropic client is created only once (lazy singleton)."""
        mock_client_instance = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client_instance

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="text")]
        mock_client_instance.messages.create.return_value = mock_response

        with patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            client = AnthropicLLMClient(api_key="sk-test")
            client.complete(system="s", user="u")
            client.complete(system="s2", user="u2")

        # Anthropic() should be called only once
        assert mock_anthropic_module.Anthropic.call_count == 1

    def test_complete_default_params(self):
        """Test that default max_tokens and temperature are applied."""
        client = AnthropicLLMClient(api_key="sk-test")
        mock_sdk_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_sdk_client.messages.create.return_value = mock_response
        client._client = mock_sdk_client

        client.complete(system="sys", user="usr")

        call_kwargs = mock_sdk_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 1024
        assert call_kwargs["temperature"] == 0.7
