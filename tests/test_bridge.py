"""Integration tests for bridge module."""

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

# Add src/python to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "python"))

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
        mock_client.model = "claude-sonnet-5"
        mock_client.create_message.return_value = {
            "id": "msg_test",
            "model": "test-model",
            "content": [{"type": "text", "text": "Test response"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }

        result = invoke_claude_native(
            prompt="Test prompt",
            workdir="/tmp",
            model="test-model",
            timeout=30,
            proxy_url="http://test:4100",
        )

        assert result["status"] == "completed"
        assert result["ok"] is True
        assert result["model"] == "test-model"
        assert "Test response" in result["output"]
        assert result["native_impl"] is True
        mock_client.create_message.assert_called_once()
        mock_client_class.assert_called_once_with(
            api_key=None,
            base_url="http://test:4100",
            model="test-model",
            timeout=30,
        )

    @patch("bridge.AnthropicClient")
    def test_api_key_passed_to_client(self, mock_client_class):
        """Should pass explicit api_key through to AnthropicClient."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.model = "claude-sonnet-5"
        mock_client.create_message.return_value = {
            "id": "msg_test",
            "model": "test-model",
            "content": [{"type": "text", "text": "Test response"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }

        result = invoke_claude_native(
            prompt="Test prompt",
            workdir="/tmp",
            api_key="explicit-test-key",
        )

        assert result["status"] == "completed"
        mock_client_class.assert_called_once_with(
            api_key="explicit-test-key",
            base_url=None,
            model=None,
            timeout=900,
        )

    @patch("bridge.AnthropicClient")
    def test_error_handling(self, mock_client_class):
        """Should handle API errors gracefully."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.model = "claude-sonnet-5"
        mock_client.create_message.side_effect = Exception("API error")

        result = invoke_claude_native(
            prompt="Test prompt",
            workdir="/tmp",
        )

        assert result["status"] == "error"
        assert result["ok"] is False
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
