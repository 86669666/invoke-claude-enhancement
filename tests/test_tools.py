"""Tests for tool implementations."""

import os
import sys
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add src/python to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "python"))

from native_tools import (
    WorkdirValidator,
    ToolExecutor,
    PathTraversalError,
    ToolNotAllowedError,
    ToolError,
    get_tool_definitions,
)


class TestWorkdirValidator:
    """Test workdir path validation."""
    
    def test_validate_relative_path(self, tmp_path):
        """Should resolve relative paths within workdir."""
        validator = WorkdirValidator(str(tmp_path))
        
        result = validator.validate_path("test.txt")
        assert result == tmp_path / "test.txt"
    
    def test_validate_absolute_path_inside(self, tmp_path):
        """Should accept absolute paths inside workdir."""
        validator = WorkdirValidator(str(tmp_path))
        
        test_file = tmp_path / "test.txt"
        result = validator.validate_path(str(test_file))
        assert result == test_file
    
    def test_reject_parent_traversal(self, tmp_path):
        """Should reject paths using .. to escape workdir."""
        validator = WorkdirValidator(str(tmp_path))
        
        with pytest.raises(PathTraversalError, match="escapes workdir"):
            validator.validate_path("../../../etc/passwd")
    
    def test_reject_absolute_outside(self, tmp_path):
        """Should reject absolute paths outside workdir."""
        validator = WorkdirValidator(str(tmp_path))
        
        with pytest.raises(PathTraversalError, match="escapes workdir"):
            validator.validate_path("/etc/passwd")
    
    def test_reject_symlink_escape(self, tmp_path):
        """Should reject symlinks that escape workdir."""
        validator = WorkdirValidator(str(tmp_path))
        
        # Create symlink to outside workdir
        outside_file = tmp_path.parent / "outside.txt"
        outside_file.write_text("secret")
        
        symlink = tmp_path / "link"
        symlink.symlink_to(outside_file)
        
        with pytest.raises(PathTraversalError, match="escapes workdir"):
            validator.validate_path("link")
    
    def test_create_workdir_if_not_exists(self, tmp_path):
        """Should reject non-existent workdir instead of creating it."""
        new_dir = tmp_path / "new" / "nested" / "dir"
        with pytest.raises(ValueError, match="does not exist"):
            validator = WorkdirValidator(str(new_dir))


class TestToolExecutor:
    """Test tool execution."""
    
    def test_read_file_success(self, tmp_path):
        """Should read file contents."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, world!")
        
        executor = ToolExecutor(str(tmp_path))
        result = executor.execute_tool("Read", {"file_path": "test.txt"})
        
        assert result["content"] == "Hello, world!"
    
    def test_read_file_not_found(self, tmp_path):
        """Should return error for non-existent file."""
        executor = ToolExecutor(str(tmp_path))
        result = executor.execute_tool("Read", {"file_path": "missing.txt"})
        
        assert "error" in result
        assert "not found" in result["error"].lower()
    
    def test_read_rejects_path_traversal(self, tmp_path):
        """Should reject path traversal in Read."""
        executor = ToolExecutor(str(tmp_path))
        
        with pytest.raises(ToolError, match="escapes workdir"):
            executor.execute_tool("Read", {"file_path": "../../etc/passwd"})
    
    def test_write_file_success(self, tmp_path):
        """Should write file contents."""
        executor = ToolExecutor(str(tmp_path))
        result = executor.execute_tool("Write", {
            "file_path": "output.txt",
            "content": "Test content"
        })
        
        assert result["success"] is True
        assert result["bytes_written"] == 12
        
        written_file = tmp_path / "output.txt"
        assert written_file.read_text() == "Test content"
    
    def test_write_creates_directories(self, tmp_path):
        """Should create parent directories."""
        executor = ToolExecutor(str(tmp_path))
        result = executor.execute_tool("Write", {
            "file_path": "nested/dir/file.txt",
            "content": "Nested"
        })
        
        assert result["success"] is True
        assert (tmp_path / "nested" / "dir" / "file.txt").read_text() == "Nested"
    
    def test_write_rejects_path_traversal(self, tmp_path):
        """Should reject path traversal in Write."""
        executor = ToolExecutor(str(tmp_path))
        
        with pytest.raises(ToolError, match="escapes workdir"):
            executor.execute_tool("Write", {
                "file_path": "../outside.txt",
                "content": "Evil"
            })
    
    def test_edit_file_success(self, tmp_path):
        """Should edit file by replacing string."""
        test_file = tmp_path / "code.py"
        test_file.write_text("def hello():\n    print('world')")
        
        executor = ToolExecutor(str(tmp_path))
        result = executor.execute_tool("Edit", {
            "file_path": "code.py",
            "old_string": "world",
            "new_string": "universe"
        })
        
        assert result["success"] is True
        assert result["replacements"] == 1
        assert test_file.read_text() == "def hello():\n    print('universe')"
    
    def test_edit_string_not_found(self, tmp_path):
        """Should return error if string not found."""
        test_file = tmp_path / "code.py"
        test_file.write_text("def hello():\n    print('world')")
        
        executor = ToolExecutor(str(tmp_path))
        result = executor.execute_tool("Edit", {
            "file_path": "code.py",
            "old_string": "missing",
            "new_string": "replacement"
        })
        
        assert "error" in result
        assert "not found" in result["error"].lower()
    
    def test_bash_success(self, tmp_path):
        """Should execute bash command."""
        executor = ToolExecutor(str(tmp_path))
        result = executor.execute_tool("Bash", {"command": "echo 'Hello from bash'"})
        
        assert result["exit_code"] == 0
        assert "Hello from bash" in result["stdout"]
    
    def test_bash_with_exit_code(self, tmp_path):
        """Should capture non-zero exit codes."""
        executor = ToolExecutor(str(tmp_path))
        result = executor.execute_tool("Bash", {"command": "exit 42"})
        
        assert result["exit_code"] == 42
    
    def test_bash_timeout(self, tmp_path):
        """Should timeout long-running commands."""
        executor = ToolExecutor(str(tmp_path), bash_timeout=1)
        result = executor.execute_tool("Bash", {"command": "sleep 10"})
        
        assert "error" in result
        assert "timed out" in result["error"].lower()
        assert result["exit_code"] == -1
    
    def test_bash_runs_in_workdir(self, tmp_path):
        """Should execute commands in workdir."""
        test_file = tmp_path / "marker.txt"
        test_file.write_text("I am here")
        
        executor = ToolExecutor(str(tmp_path))
        result = executor.execute_tool("Bash", {"command": "cat marker.txt"})
        
        assert result["exit_code"] == 0
        assert "I am here" in result["stdout"]
    
    def test_bash_output_size_limit(self, tmp_path):
        """Should truncate large output."""
        executor = ToolExecutor(str(tmp_path), output_size_limit=100)
        result = executor.execute_tool("Bash", {
            "command": "python3 -c 'print(\"x\" * 1000)'"
        })
        
        assert "truncated" in result["stdout"]
        assert len(result["stdout"]) <= 200  # Some room for truncation message
    
    def test_allowed_tools_whitelist(self, tmp_path):
        """Should only allow whitelisted tools."""
        executor = ToolExecutor(str(tmp_path), allowed_tools=["Read", "Write"])
        
        assert executor.is_tool_allowed("Read") is True
        assert executor.is_tool_allowed("Write") is True
        assert executor.is_tool_allowed("Bash") is False
        assert executor.is_tool_allowed("Edit") is False
    
    def test_disallowed_tools_blacklist(self, tmp_path):
        """Should block blacklisted tools."""
        executor = ToolExecutor(str(tmp_path), disallowed_tools=["Bash"])
        
        assert executor.is_tool_allowed("Read") is True
        assert executor.is_tool_allowed("Write") is True
        assert executor.is_tool_allowed("Edit") is True
        assert executor.is_tool_allowed("Bash") is False
    
    def test_execute_disallowed_tool_raises(self, tmp_path):
        """Should raise error for disallowed tools."""
        executor = ToolExecutor(str(tmp_path), allowed_tools=["Read"])
        
        with pytest.raises(ToolNotAllowedError, match="not allowed"):
            executor.execute_tool("Bash", {"command": "ls"})
    
    def test_unknown_tool_raises(self, tmp_path):
        """Should raise error for unknown tools."""
        executor = ToolExecutor(str(tmp_path))
        
        with pytest.raises(ToolError, match="Unknown tool"):
            executor.execute_tool("UnknownTool", {})


class TestGetToolDefinitions:
    """Test tool definitions."""
    
    def test_returns_all_tools(self):
        """Should return all four tool definitions."""
        tools = get_tool_definitions()
        
        assert len(tools) == 4
        tool_names = {tool["name"] for tool in tools}
        assert tool_names == {"Read", "Write", "Edit", "Bash"}
    
    def test_tools_have_required_fields(self):
        """Should have name, description, and input_schema."""
        tools = get_tool_definitions()
        
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert "properties" in tool["input_schema"]
            assert "required" in tool["input_schema"]


class TestBashSandboxing:
    """Test Bash sandboxing and security."""

    def test_workdir_must_exist(self):
        """Should reject non-existent workdir."""
        with pytest.raises(ValueError, match="does not exist"):
            ToolExecutor("/nonexistent/path/12345")

    def test_bash_without_bubblewrap(self, tmp_path, monkeypatch):
        """Should deny Bash execution when bubblewrap unavailable."""
        # Mock bubblewrap as unavailable
        import shutil
        original_which = shutil.which
        monkeypatch.setattr(shutil, "which", lambda cmd: None if cmd == "bwrap" else original_which(cmd))

        executor = ToolExecutor(str(tmp_path))
        result = executor.execute_tool("Bash", {"command": "echo test"})

        assert "error" in result
        assert "bubblewrap" in result["error"].lower()
        assert result["exit_code"] == -1

    def test_bash_cannot_read_outside_workdir(self, tmp_path):
        """Should prevent reading files outside workdir via Bash."""
        import shutil
        if not shutil.which("bwrap"):
            pytest.skip("bubblewrap not available")

        # Create a file outside workdir
        outside_file = tmp_path.parent / "secret.txt"
        outside_file.write_text("SECRET DATA")

        executor = ToolExecutor(str(tmp_path))
        result = executor.execute_tool("Bash", {
            "command": f"cat {outside_file}"
        })

        # Should fail - file not accessible in sandbox
        assert result["exit_code"] != 0 or "SECRET DATA" not in result.get("stdout", "")

    def test_bash_timeout_kills_children(self, tmp_path):
        """Should kill entire process group on timeout."""
        import shutil
        if not shutil.which("bwrap"):
            pytest.skip("bubblewrap not available")

        executor = ToolExecutor(str(tmp_path), bash_timeout=1)

        # Command that spawns child processes
        result = executor.execute_tool("Bash", {
            "command": "sleep 5 & sleep 5 & sleep 5 & wait"
        })

        assert "timed out" in result.get("error", "").lower()
        assert result["exit_code"] == -1

        # Give time for cleanup
        import time
        time.sleep(0.5)

        # Check no sleep processes remain (best effort check)
        check = subprocess.run(
            ["pgrep", "-f", "sleep 5"],
            capture_output=True,
        )
        # Should have no matching processes (or very few unrelated ones)
        assert len(check.stdout.strip()) < 10  # Allow for some noise
