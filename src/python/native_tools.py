"""Tool implementations for coding-agent with strict workdir confinement."""

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    # Package mode
    from .config import ConfigLoader
except ImportError:
    # Flat sys.path mode
    from config import ConfigLoader


class ToolError(Exception):
    """Base exception for tool errors."""

    pass


class PathTraversalError(ToolError):
    """Raised when path escapes workdir."""

    pass


class ToolNotAllowedError(ToolError):
    """Raised when tool is not in allowed_tools or is in disallowed_tools."""

    pass


class WorkdirValidator:
    """Validates paths for strict workdir confinement."""

    def __init__(self, workdir: str):
        """
        Initialize validator.

        Args:
            workdir: Absolute path to working directory

        Raises:
            ValueError: If workdir does not exist
        """
        self.workdir = Path(workdir).resolve()

        # Workdir must exist - do not silently create
        if not self.workdir.exists():
            raise ValueError(f"Workdir does not exist: {workdir}")
        if not self.workdir.is_dir():
            raise ValueError(f"Workdir is not a directory: {workdir}")

    def validate_path(self, path: str) -> Path:
        """
        Validate that path is within workdir.

        Args:
            path: Path to validate (can be relative or absolute)

        Returns:
            Resolved absolute Path object

        Raises:
            PathTraversalError: If path escapes workdir
        """
        # Handle relative paths
        if not os.path.isabs(path):
            full_path = self.workdir / path
        else:
            full_path = Path(path)

        # Resolve to absolute path (follows symlinks)
        try:
            resolved = full_path.resolve()
        except (OSError, RuntimeError) as e:
            raise PathTraversalError(f"Cannot resolve path '{path}': {e}")

        # Check if resolved path is within workdir
        try:
            resolved.relative_to(self.workdir)
        except ValueError:
            raise PathTraversalError(f"Path '{path}' escapes workdir '{self.workdir}'")

        return resolved


class ToolExecutor:
    """Executes tools with proper validation and confinement."""

    def __init__(
        self,
        workdir: str,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
        bash_timeout: int = 300,
        output_size_limit: int = 1_000_000,  # 1MB
    ):
        """
        Initialize tool executor.

        Args:
            workdir: Working directory for file operations
            allowed_tools: Whitelist of allowed tools (None = all allowed)
            disallowed_tools: Blacklist of disallowed tools
            bash_timeout: Timeout for bash commands in seconds
            output_size_limit: Maximum output size in bytes
        """
        self.validator = WorkdirValidator(workdir)
        self.workdir = self.validator.workdir
        self.allowed_tools = allowed_tools
        self.disallowed_tools = disallowed_tools or []
        self.bash_timeout = bash_timeout
        self.output_size_limit = output_size_limit

        # Check if bubblewrap is available for strict sandboxing
        self.has_bubblewrap = shutil.which("bwrap") is not None

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if tool is allowed to be executed."""
        if tool_name in self.disallowed_tools:
            return False

        if self.allowed_tools is not None:
            return tool_name in self.allowed_tools

        return True

    def execute_tool(
        self, tool_name: str, tool_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute a tool with given input.

        Args:
            tool_name: Name of the tool to execute
            tool_input: Tool input parameters

        Returns:
            Tool result dict

        Raises:
            ToolNotAllowedError: If tool is not allowed
            PathTraversalError: If path escapes workdir
            ToolError: On other execution errors
        """
        if not self.is_tool_allowed(tool_name):
            raise ToolNotAllowedError(
                f"Tool '{tool_name}' is not allowed. "
                f"Allowed: {self.allowed_tools}, "
                f"Disallowed: {self.disallowed_tools}"
            )

        # Dispatch to tool implementation
        tool_methods = {
            "Read": self._read,
            "Write": self._write,
            "Edit": self._edit,
            "Bash": self._bash,
        }

        method = tool_methods.get(tool_name)
        if method is None:
            raise ToolError(f"Unknown tool: {tool_name}")

        return method(tool_input)

    def _read(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """Read file contents."""
        file_path = tool_input.get("file_path")
        if not file_path:
            raise ToolError("Read: missing 'file_path' parameter")

        resolved_path = self.validator.validate_path(file_path)

        if not resolved_path.exists():
            return {"error": f"File not found: {file_path}"}

        if not resolved_path.is_file():
            return {"error": f"Not a file: {file_path}"}

        try:
            # Check file size
            size = resolved_path.stat().st_size
            if size > self.output_size_limit:
                return {
                    "error": f"File too large: {size} bytes (limit: {self.output_size_limit})"
                }

            content = resolved_path.read_text(encoding="utf-8", errors="replace")
            return {"content": content}
        except Exception as e:
            return {"error": f"Failed to read file: {e}"}

    def _write(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """Write content to file."""
        file_path = tool_input.get("file_path")
        content = tool_input.get("content", "")

        if not file_path:
            raise ToolError("Write: missing 'file_path' parameter")

        resolved_path = self.validator.validate_path(file_path)

        # Check content size
        if len(content) > self.output_size_limit:
            return {
                "error": f"Content too large: {len(content)} bytes (limit: {self.output_size_limit})"
            }

        try:
            # Create parent directories if needed
            resolved_path.parent.mkdir(parents=True, exist_ok=True)

            resolved_path.write_text(content, encoding="utf-8")
            return {"success": True, "bytes_written": len(content)}
        except Exception as e:
            return {"error": f"Failed to write file: {e}"}

    def _edit(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """Edit file by replacing old_string with new_string."""
        file_path = tool_input.get("file_path")
        old_string = tool_input.get("old_string")
        new_string = tool_input.get("new_string")

        if not file_path:
            raise ToolError("Edit: missing 'file_path' parameter")
        if old_string is None:
            raise ToolError("Edit: missing 'old_string' parameter")
        if new_string is None:
            raise ToolError("Edit: missing 'new_string' parameter")

        resolved_path = self.validator.validate_path(file_path)

        if not resolved_path.exists():
            return {"error": f"File not found: {file_path}"}

        try:
            content = resolved_path.read_text(encoding="utf-8", errors="replace")

            if old_string not in content:
                return {"error": f"String not found in file: {old_string[:100]}..."}

            new_content = content.replace(old_string, new_string, 1)

            if len(new_content) > self.output_size_limit:
                return {
                    "error": f"Result too large: {len(new_content)} bytes (limit: {self.output_size_limit})"
                }

            resolved_path.write_text(new_content, encoding="utf-8")
            return {"success": True, "replacements": 1}
        except Exception as e:
            return {"error": f"Failed to edit file: {e}"}

    def _bash(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute bash command in sandboxed workdir."""
        command = tool_input.get("command")

        if not command:
            raise ToolError("Bash: missing 'command' parameter")

        # Use bubblewrap for strict sandboxing if available
        if self.has_bubblewrap:
            return self._bash_sandboxed(command)
        else:
            # Fail closed: deny Bash if no sandboxing available
            return {
                "error": "Bash execution requires bubblewrap (bwrap) for sandboxing. Install bubblewrap to enable.",
                "exit_code": -1,
            }

    def _bash_sandboxed(self, command: str) -> Dict[str, Any]:
        """Execute bash command with bubblewrap sandboxing."""
        proc = None
        try:
            # Build bubblewrap command for strict sandboxing
            bwrap_args = [
                "bwrap",
                "--ro-bind",
                "/usr",
                "/usr",  # Read-only system binaries
                "--ro-bind",
                "/lib",
                "/lib",  # Read-only libraries
                "--ro-bind",
                "/lib64",
                "/lib64",  # Read-only 64-bit libraries
                "--ro-bind",
                "/bin",
                "/bin",  # Read-only binaries
                "--ro-bind",
                "/sbin",
                "/sbin",  # Read-only system binaries
                "--proc",
                "/proc",  # Proc filesystem
                "--dev",
                "/dev",  # Device filesystem (minimal)
                "--tmpfs",
                "/tmp",  # Ephemeral tmp
                "--bind",
                str(self.workdir),
                str(self.workdir),  # Workdir read-write
                "--chdir",
                str(self.workdir),  # Start in workdir
                "--unshare-all",  # Unshare all namespaces
                "--die-with-parent",  # Kill sandbox when parent dies
                "--new-session",  # New process session for clean termination
                "/bin/bash",
                "-c",
                command,
            ]

            # Execute with timeout and process group kill
            proc = subprocess.Popen(
                bwrap_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                start_new_session=True,  # Create new process group
            )
            stdout, stderr = proc.communicate(timeout=self.bash_timeout)

            # Truncate output if too large
            if len(stdout) > self.output_size_limit:
                stdout = stdout[: self.output_size_limit] + "\n... (truncated)"
            if len(stderr) > self.output_size_limit:
                stderr = stderr[: self.output_size_limit] + "\n... (truncated)"

            return {
                "exit_code": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }
        except subprocess.TimeoutExpired:
            # Kill the entire sandbox process group, including grandchildren.
            if proc is not None:
                import signal

                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
            return {
                "error": f"Command timed out after {self.bash_timeout} seconds",
                "exit_code": -1,
            }
        except Exception as e:
            return {"error": f"Failed to execute command: {e}", "exit_code": -1}


def get_tool_definitions() -> List[Dict[str, Any]]:
    """
    Get Anthropic-compatible tool definitions.

    Returns:
        List of tool definition dicts
    """
    return [
        {
            "name": "Read",
            "description": "Read the contents of a file from the working directory.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to read (relative to workdir)",
                    }
                },
                "required": ["file_path"],
            },
        },
        {
            "name": "Write",
            "description": "Write content to a file in the working directory. Creates the file if it doesn't exist.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to write (relative to workdir)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
        {
            "name": "Edit",
            "description": "Edit a file by replacing the first occurrence of old_string with new_string.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to edit (relative to workdir)",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact string to replace",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement string",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
        {
            "name": "Bash",
            "description": "Execute a bash command in a sandboxed working directory. Requires bubblewrap for security.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute",
                    }
                },
                "required": ["command"],
            },
        },
    ]
