"""Integration tests for bridge module."""

import os
import pytest
from unittest.mock import Mock, patch

from bridge import use_native_implementation, invoke_claude_native


class TestUseNativeImplementation:
    """Test native implementation detection."""

    def test_returns_false_by_default(self):
        """Should return False when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            assert use_native_implementation() is False

    def test_returns_true_for_1(self):
        """Should return True when INVOKE_CLAUDE_NATIVE=1."""
        with patch.dict(os.environ, {"INVOKE_CLAUDE_NATIVE": "1"}):
            assert use_native_implementation() is True

    def test_returns_true_for_true(self):
        """Should return True when INVOKE_CLAUDE_NATIVE=true."""
        with patch.dict(os.environ, {"INVOKE_CLAUDE_NATIVE": "true"}):
            assert use_native_implementation() is True

    def test_returns_true_for_yes(self):
        """Should return True when INVOKE_CLAUDE_NATIVE=yes."""
        with patch.dict(os.environ, {"INVOKE_CLAUDE_NATIVE": "yes"}):
            assert use_native_implementation() is True

    def test_returns_false_for_other_values(self):
        """Should return False for other env var values."""
        for value in ["0", "false", "no", "random"]:
            with patch.dict(os.environ, {"INVOKE_CLAUDE_NATIVE": value}):
                assert use_native_implementation() is False


class TestInvokeClaudeNative:
    """Test native invoke_claude implementation."""

    @patch("bridge.AnthropicClient")
    def test_basic_call(self, mock_client_class):
        """Should make basic API call with correct parameters."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.create_message.return_value = {
            "id": "msg_test",
            "model": "test-model",
            "content": [{"type": "text", "text": "Test response"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }
        mock_client.extract_text.return_value = "Test response"
        mock_client.extract_thinking.return_value = None

        result = invoke_claude_native(
            prompt="Test prompt",
            workdir="/tmp",
            model="test-model",
            timeout=30,
            proxy_url="http://test:4100",
        )

        assert result["status"] == "completed"
        assert result["model"] == "test-model"
        assert result["output"] == "Test response"
        assert result["native_impl"] is True
        mock_client.create_message.assert_called_once()

    @patch("bridge.AnthropicClient")
    def test_extended_thinking(self, mock_client_class):
        """Should include thinking output when effort is set."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.create_message.return_value = {
            "id": "msg_test",
            "content": [
                {"type": "thinking", "thinking": "Let me think..."},
                {"type": "text", "text": "Answer"},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }
        mock_client.extract_text.return_value = "Answer"
        mock_client.extract_thinking.return_value = "Let me think..."

        result = invoke_claude_native(
            prompt="Test prompt",
            workdir="/tmp",
            effort="medium",
        )

        assert "[Thinking]" in result["output"]
        assert "Let me think..." in result["output"]
        assert "Answer" in result["output"]

    @patch("bridge.AnthropicClient")
    def test_error_handling(self, mock_client_class):
        """Should handle API errors gracefully."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.create_message.side_effect = Exception("API error")

        result = invoke_claude_native(
            prompt="Test prompt",
            workdir="/tmp",
        )

        assert result["status"] == "error"
        assert "API error" in result["error"]
        assert result["native_impl"] is True

    def test_native_impl_unavailable(self):
        """Should raise error when native implementation not available."""
        with patch("bridge.NATIVE_IMPL_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="Native implementation not available"):
                invoke_claude_native(
                    prompt="Test prompt",
                    workdir="/tmp",
                )
