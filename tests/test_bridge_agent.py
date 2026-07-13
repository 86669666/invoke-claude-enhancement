"""Integration tests for bridge module with agent functionality."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

# Add src/python to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "python"))

from bridge import invoke_claude_native, use_native_implementation


class TestInvokeClaudeNativeAgent:
    """Test native invoke_claude with agent tool-use loop."""
    
    @patch("bridge.AnthropicClient")
    def test_basic_call_no_tools(self, mock_client_class, tmp_path):
        """Should handle basic call without tool use."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.model = "claude-sonnet-4-20250514"
        
        mock_client.create_message.return_value = {
            "id": "msg_test",
            "model": "claude-sonnet-4-20250514",
            "content": [{"type": "text", "text": "Task completed"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }
        
        result = invoke_claude_native(
            prompt="What is 2+2?",
            workdir=str(tmp_path),
            model="claude-sonnet-4-20250514",
            timeout=30,
            proxy_url="http://test:4100",
        )
        
        assert result["status"] == "completed"
        assert result["model"] == "claude-sonnet-4-20250514"
        assert "Task completed" in result["output"]
        assert result["native_impl"] is True
        assert "elapsed" in result
        assert result["turns"] == 1

    @patch("bridge.AnthropicClient")
    def test_effort_enables_thinking_without_exposing_it(
        self, mock_client_class, tmp_path
    ):
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.model = "claude-sonnet-5"
        mock_client.create_message.return_value = {
            "model": "claude-sonnet-5",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "private reasoning",
                    "signature": "sig",
                },
                {"type": "text", "text": "public answer"},
            ],
            "usage": {"input_tokens": 1, "output_tokens": 2},
            "stop_reason": "end_turn",
        }

        result = invoke_claude_native(
            prompt="think",
            workdir=str(tmp_path),
            effort="xhigh",
        )

        kwargs = mock_client.create_message.call_args.kwargs
        assert kwargs["extended_thinking"] is True
        assert kwargs["thinking_budget_tokens"] == 30_000
        assert kwargs["max_tokens"] == 64_000
        assert result["output"] == "public answer"
        assert "private reasoning" not in result["output"]

    @patch("bridge.AnthropicClient")
    def test_multi_turn_with_tools(self, mock_client_class, tmp_path):
        """Should handle multi-turn conversation with tool use."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.model = "claude-sonnet-4-20250514"
        
        # First call: Claude requests to read a file
        mock_client.create_message.side_effect = [
            {
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {"type": "text", "text": "Let me read the file"},
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "Read",
                        "input": {"file_path": "test.txt"},
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "stop_reason": "tool_use",
            },
            # Second call: Claude responds after reading
            {
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "The file says: Hello"}],
                "usage": {"input_tokens": 30, "output_tokens": 10},
                "stop_reason": "end_turn",
            },
        ]
        
        # Create test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello")
        
        result = invoke_claude_native(
            prompt="Read test.txt and tell me what it says",
            workdir=str(tmp_path),
            max_turns=10,
        )
        
        assert result["status"] == "completed"
        assert result["turns"] == 2
        assert result["usage"]["input_tokens"] == 40
        assert result["usage"]["output_tokens"] == 30
        assert "The file says: Hello" in result["output"]
    
    @patch("bridge.AnthropicClient")
    def test_write_and_edit_tools(self, mock_client_class, tmp_path):
        """Should handle Write and Edit tool usage."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.model = "claude-sonnet-4-20250514"
        
        # Simulate: Write file, then Edit it
        mock_client.create_message.side_effect = [
            {
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "Write",
                        "input": {"file_path": "code.py", "content": "print('hello')"},
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "stop_reason": "tool_use",
            },
            {
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_2",
                        "name": "Edit",
                        "input": {
                            "file_path": "code.py",
                            "old_string": "hello",
                            "new_string": "world",
                        },
                    },
                ],
                "usage": {"input_tokens": 30, "output_tokens": 20},
                "stop_reason": "tool_use",
            },
            {
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "File created and edited"}],
                "usage": {"input_tokens": 40, "output_tokens": 10},
                "stop_reason": "end_turn",
            },
        ]
        
        result = invoke_claude_native(
            prompt="Create code.py with print hello, then edit it to print world",
            workdir=str(tmp_path),
        )
        
        assert result["status"] == "completed"
        assert result["turns"] == 3
        
        # Verify file was created and edited
        code_file = tmp_path / "code.py"
        assert code_file.exists()
        assert code_file.read_text() == "print('world')"
    
    @patch("bridge.AnthropicClient")
    def test_bash_tool(self, mock_client_class, tmp_path):
        """Should handle Bash tool execution."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.model = "claude-sonnet-4-20250514"
        
        mock_client.create_message.side_effect = [
            {
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "Bash",
                        "input": {"command": "echo 'test output'"},
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "stop_reason": "tool_use",
            },
            {
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "Command executed successfully"}],
                "usage": {"input_tokens": 30, "output_tokens": 10},
                "stop_reason": "end_turn",
            },
        ]
        
        result = invoke_claude_native(
            prompt="Run echo test",
            workdir=str(tmp_path),
        )
        
        assert result["status"] == "completed"
        assert result["turns"] == 2
    
    @patch("bridge.AnthropicClient")
    def test_denied_tool(self, mock_client_class, tmp_path):
        """Should reject disallowed tools."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.model = "claude-sonnet-4-20250514"
        
        mock_client.create_message.side_effect = [
            {
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "Bash",
                        "input": {"command": "rm -rf /"},
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "stop_reason": "tool_use",
            },
            {
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "I see the tool was denied"}],
                "usage": {"input_tokens": 30, "output_tokens": 10},
                "stop_reason": "end_turn",
            },
        ]
        
        result = invoke_claude_native(
            prompt="Run dangerous command",
            workdir=str(tmp_path),
            disallowed_tools=["Bash"],
        )
        
        assert result["status"] == "completed"
        # Tool should have been rejected, but conversation continues
        assert result["turns"] == 2
    
    @patch("bridge.AnthropicClient")
    def test_path_traversal_rejected(self, mock_client_class, tmp_path):
        """Should reject path traversal attempts and continue."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.model = "claude-sonnet-4-20250514"
        
        mock_client.create_message.side_effect = [
            {
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "Read",
                        "input": {"file_path": "../../etc/passwd"},
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "stop_reason": "tool_use",
            },
            {
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "I understand the path was invalid"}],
                "usage": {"input_tokens": 30, "output_tokens": 5},
                "stop_reason": "end_turn",
            },
        ]
        
        result = invoke_claude_native(
            prompt="Try to read /etc/passwd",
            workdir=str(tmp_path),
        )
        
        # Should complete even though tool had error
        assert result["status"] == "completed"
        assert result["turns"] == 2
    
    @patch("bridge.AnthropicClient")
    def test_max_turns_limit(self, mock_client_class, tmp_path):
        """Should respect max_turns limit."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.model = "claude-sonnet-4-20250514"
        
        # Keep requesting tools indefinitely
        mock_client.create_message.return_value = {
            "model": "claude-sonnet-4-20250514",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_1",
                    "name": "Bash",
                    "input": {"command": "echo 'loop'"},
                },
            ],
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "stop_reason": "tool_use",
        }
        
        result = invoke_claude_native(
            prompt="Keep running commands",
            workdir=str(tmp_path),
            max_turns=3,
        )
        
        assert result["status"] == "max_turns_reached"
        assert result["turns"] == 3
        assert "elapsed" in result
    
    @patch("bridge.AnthropicClient")
    def test_api_error_handling(self, mock_client_class, tmp_path):
        """Should handle API errors gracefully."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.model = "claude-sonnet-4-20250514"
        
        mock_client.create_message.side_effect = Exception("API connection failed")
        
        result = invoke_claude_native(
            prompt="Test prompt",
            workdir=str(tmp_path),
        )
        
        assert result["status"] == "error"
        assert "API connection failed" in result["error"]
        assert result["native_impl"] is True
        assert "elapsed" in result
    
    @patch("bridge.AnthropicClient")
    def test_allowed_tools_filter(self, mock_client_class, tmp_path):
        """Should only expose allowed tools to API."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.model = "claude-sonnet-4-20250514"
        
        mock_client.create_message.return_value = {
            "model": "claude-sonnet-4-20250514",
            "content": [{"type": "text", "text": "Done"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }
        
        result = invoke_claude_native(
            prompt="Test",
            workdir=str(tmp_path),
            allowed_tools=["Read", "Write"],
        )
        
        assert result["status"] == "completed"
        
        # Check that create_message was called with filtered tools
        call_args = mock_client.create_message.call_args
        tools = call_args.kwargs.get("tools", [])
        tool_names = {tool["name"] for tool in tools}
        
        # Should only have Read and Write, not Bash or Edit
        assert tool_names == {"Read", "Write"}
    
    @patch("bridge.AnthropicClient")
    def test_import_compatibility(self, mock_client_class, tmp_path):
        """Should work with both package and flat imports."""
        # This test verifies the import logic works
        # The actual import is already done at module level
        assert invoke_claude_native is not None


class TestUseNativeImplementation:
    """Test native implementation detection (from original test_bridge.py)."""
    
    def test_returns_false_by_default(self):
        """Should return False when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            assert use_native_implementation() is False
    
    def test_returns_true_for_1(self):
        """Should return True when INVOKE_CLAUDE_NATIVE=1."""
        with patch.dict(os.environ, {"INVOKE_CLAUDE_NATIVE": "1"}):
            assert use_native_implementation() is True
