"""Tests for AnthropicClient."""

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add src/python to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "python"))

from client import AnthropicClient, quick_call


class TestAnthropicClient:
    """Test suite for AnthropicClient."""
    
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    def test_client_initialization(self):
        """Test client initialization with default config."""
        client = AnthropicClient()
        
        assert client.api_key == "test-key-123"
        assert client.base_url in ["http://10.10.10.111:4100", "http://127.0.0.1:4100", "https://llm.bai.one"]
        assert client.model == "claude-sonnet-5"
        assert client.timeout == 900
    
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    def test_client_custom_params(self):
        """Test client initialization with custom parameters."""
        client = AnthropicClient(
            api_key="custom-key",
            base_url="http://custom:8080",
            model="claude-sonnet-4",
            timeout=600,
        )
        
        assert client.api_key == "custom-key"
        assert client.base_url == "http://custom:8080"
        assert client.model == "claude-sonnet-4"
        assert client.timeout == 600
    
    def test_client_no_api_key_raises(self):
        """Test that missing API key raises error."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="No API key found"):
                AnthropicClient()
    
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    def test_build_headers_basic(self):
        """Test building basic request headers."""
        client = AnthropicClient()
        headers = client._build_headers(extended_thinking=False)
        
        assert headers["x-api-key"] == "test-key-123"
        assert "anthropic-beta" not in headers
    
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    def test_build_headers_extended_thinking(self):
        """Test building headers with extended thinking."""
        client = AnthropicClient()
        headers = client._build_headers(extended_thinking=True)
        
        assert headers["x-api-key"] == "test-key-123"
        assert "anthropic-beta" in headers
        assert "extended-thinking" in headers["anthropic-beta"]
    
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    @patch("httpx.Client.post")
    def test_create_message_success(self, mock_post):
        """Test successful message creation."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "msg_123",
            "model": "claude-opus-4",
            "content": [{"type": "text", "text": "Hello!"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        client = AnthropicClient()
        response = client.create_message("Hi there")
        
        assert response["id"] == "msg_123"
        assert response["model"] == "claude-opus-4"
        assert mock_post.call_count == 1
        
        # Verify request payload
        call_args = mock_post.call_args
        payload = call_args.kwargs["json"]
        assert payload["messages"][0]["content"] == "Hi there"
        assert payload["model"] == "claude-sonnet-5"
    
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    @patch("httpx.Client.post")
    def test_create_message_with_extended_thinking(self, mock_post):
        """Test message creation with extended thinking."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "content": [
                {"type": "thinking", "thinking": "Let me think..."},
                {"type": "text", "text": "The answer is 4."},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        client = AnthropicClient()
        response = client.create_message(
            "What is 2+2?",
            extended_thinking=True,
            thinking_budget_tokens=5000,
        )
        
        # Verify thinking payload
        call_args = mock_post.call_args
        payload = call_args.kwargs["json"]
        assert payload["thinking"]["type"] == "enabled"
        assert payload["thinking"]["budget_tokens"] == 5000
        
        # Verify headers include beta flag
        headers = call_args.kwargs["headers"]
        assert "anthropic-beta" in headers
    
    def test_extract_text(self):
        """Test extracting text from response."""
        client = AnthropicClient(api_key="test")
        
        response = {
            "content": [
                {"type": "text", "text": "First part."},
                {"type": "thinking", "thinking": "Internal reasoning"},
                {"type": "text", "text": "Second part."},
            ]
        }
        
        text = client.extract_text(response)
        assert text == "First part.\nSecond part."
    
    def test_extract_thinking(self):
        """Test extracting thinking from response."""
        client = AnthropicClient(api_key="test")
        
        response = {
            "content": [
                {"type": "thinking", "thinking": "Let me analyze..."},
                {"type": "text", "text": "The answer is X."},
            ]
        }
        
        thinking = client.extract_thinking(response)
        assert thinking == "Let me analyze..."
    
    def test_extract_thinking_not_present(self):
        """Test extracting thinking when not present."""
        client = AnthropicClient(api_key="test")
        
        response = {
            "content": [
                {"type": "text", "text": "Just text, no thinking."},
            ]
        }
        
        thinking = client.extract_thinking(response)
        assert thinking is None
    
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    def test_context_manager(self):
        """Test client as context manager."""
        with AnthropicClient() as client:
            assert client.client is not None
        
        # Client should be closed after context exit
        # (We can't easily test this without mocking httpx.Client)
    
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    @patch("httpx.Client.post")
    def test_quick_call(self, mock_post):
        """Test quick_call convenience function."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "content": [{"type": "text", "text": "Quick response"}],
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        result = quick_call("Test prompt", model="claude-sonnet-4")
        
        assert result == "Quick response"
        assert mock_post.call_count == 1
