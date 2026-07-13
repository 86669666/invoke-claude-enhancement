"""Bridge module to integrate native Python client into existing invoke_claude tool."""

import os
import time
from typing import Any, Dict, List, Optional

# Import native Python implementation (support both package and flat mode)
try:
    from client import AnthropicClient
    from native_tools import ToolExecutor, get_tool_definitions, ToolNotAllowedError, PathTraversalError
    NATIVE_IMPL_AVAILABLE = True
except ImportError:
    try:
        from .client import AnthropicClient
        from .native_tools import ToolExecutor, get_tool_definitions, ToolNotAllowedError, PathTraversalError
        NATIVE_IMPL_AVAILABLE = True
    except ImportError:
        NATIVE_IMPL_AVAILABLE = False


def use_native_implementation() -> bool:
    """
    Determine if native Python implementation should be used.

    Returns:
        True if INVOKE_CLAUDE_NATIVE=1 or INVOKE_CLAUDE_NATIVE=true
    """
    env_value = os.getenv("INVOKE_CLAUDE_NATIVE", "").lower()
    return env_value in ("1", "true", "yes")


def invoke_claude_native(
    prompt: str,
    workdir: str,
    *,
    model: Optional[str] = None,
    timeout: int = 900,
    proxy_url: Optional[str] = None,
    effort: Optional[str] = None,
    max_turns: int = 25,
    allowed_tools: Optional[List[str]] = None,
    disallowed_tools: Optional[List[str]] = None,
    output_format: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Native Python implementation of invoke_claude with tool-use loop.

    Args:
        prompt: Task instruction for Claude
        workdir: Working directory for file operations
        model: Claude model name (default: claude-sonnet-5)
        timeout: Overall timeout in seconds
        proxy_url: LiteLLM proxy URL (default: http://10.10.10.111:4100)
        effort: Reasoning effort (low/medium/high/xhigh/max/auto)
        max_turns: Maximum conversation turns (default: 25)
        allowed_tools: Whitelist of allowed tools (None = all allowed)
        disallowed_tools: Blacklist of disallowed tools
        output_format: Output format preference (unused)
        **kwargs: Additional parameters (ignored)

    Returns:
        Result dict with 'ok', 'output', 'status', 'model', 'usage', 'native_impl', 'elapsed'
    """
    if not NATIVE_IMPL_AVAILABLE:
        raise RuntimeError(
            "Native implementation not available. Install dependencies: "
            "httpx, tenacity, structlog, tomli (Python < 3.11)"
        )

    start_time = time.time()

    try:
        # Initialize tool executor
        tool_executor = ToolExecutor(
            workdir=workdir,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
        )

        # Get filtered tool definitions
        all_tools = get_tool_definitions()
        available_tools = [
            tool for tool in all_tools
            if tool_executor.is_tool_allowed(tool["name"])
        ]

        # Build system prompt
        system_prompt = f"""You are Claude Code, a helpful coding assistant working in: {workdir}

You have access to tools for reading, writing, editing files, and running bash commands.
Complete the task described by the user. Be thorough and verify your work."""

        effort_to_budget = {
            "low": 2_000,
            "medium": 10_000,
            "high": 20_000,
            "xhigh": 30_000,
            "max": 50_000,
            "auto": 10_000,
        }
        extended_thinking = effort is not None
        thinking_budget = effort_to_budget.get(effort or "auto", 10_000)

        # Initialize conversation
        messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]

        total_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
        }

        with AnthropicClient(
            base_url=proxy_url,
            model=model,
            timeout=timeout,
        ) as client:

            for turn in range(max_turns):
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    return {
                        "ok": False,
                        "output": _format_conversation_output(messages),
                        "status": "timeout",
                        "error": f"Overall timeout exceeded: {timeout}s",
                        "model": model or client.model,
                        "usage": total_usage,
                        "native_impl": True,
                        "elapsed": elapsed,
                        "turns": turn,
                    }

                # Make API call with full conversation history
                try:
                    response = client.create_message(
                        messages=messages,
                        system=system_prompt,
                        max_tokens=64_000,
                        extended_thinking=extended_thinking,
                        thinking_budget_tokens=(thinking_budget if extended_thinking else None),
                        tools=available_tools if available_tools else None,
                    )
                except Exception as api_error:
                    elapsed = time.time() - start_time
                    return {
                        "ok": False,
                        "output": _format_conversation_output(messages),
                        "status": "error",
                        "error": str(api_error),
                        "error_type": type(api_error).__name__,
                        "model": model or client.model,
                        "usage": total_usage,
                        "native_impl": True,
                        "elapsed": elapsed,
                        "turns": turn,
                    }

                # Update usage
                usage = response.get("usage", {})
                total_usage["input_tokens"] += usage.get("input_tokens", 0)
                total_usage["output_tokens"] += usage.get("output_tokens", 0)

                # Add assistant message to conversation
                assistant_message = {
                    "role": "assistant",
                    "content": response.get("content", []),
                }
                messages.append(assistant_message)

                # Check stop reason
                stop_reason = response.get("stop_reason")

                if stop_reason == "end_turn":
                    # Task complete
                    elapsed = time.time() - start_time
                    return {
                        "ok": True,
                        "output": _format_conversation_output(messages),
                        "status": "completed",
                        "model": response.get("model"),
                        "usage": total_usage,
                        "stop_reason": stop_reason,
                        "native_impl": True,
                        "elapsed": elapsed,
                        "turns": turn + 1,
                    }

                elif stop_reason == "tool_use":
                    # Execute tools and continue
                    tool_results = []

                    for content_block in response.get("content", []):
                        if content_block.get("type") == "tool_use":
                            tool_name = content_block.get("name")
                            tool_input = content_block.get("input", {})
                            tool_use_id = content_block.get("id")

                            try:
                                result = tool_executor.execute_tool(tool_name, tool_input)
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": tool_use_id,
                                    "content": str(result),
                                })
                            except Exception as e:
                                # Catch all tool errors and return as tool_result error
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": tool_use_id,
                                    "content": f"Error: {e}",
                                    "is_error": True,
                                })

                    # Add tool results as user message
                    if tool_results:
                        messages.append({
                            "role": "user",
                            "content": tool_results,
                        })
                    else:
                        # No tools executed, break to avoid infinite loop
                        break

                elif stop_reason == "max_tokens":
                    # Continue if we have turns left
                    continue

                else:
                    # Unknown stop reason, break
                    break

            # Max turns reached
            elapsed = time.time() - start_time
            return {
                "ok": False,
                "output": _format_conversation_output(messages),
                "status": "max_turns_reached",
                "model": response.get("model") if 'response' in locals() else (model or client.model),
                "usage": total_usage,
                "stop_reason": "max_turns",
                "native_impl": True,
                "elapsed": elapsed,
                "turns": max_turns,
            }

    except Exception as e:
        elapsed = time.time() - start_time
        return {
            "ok": False,
            "output": "",
            "status": "error",
            "error": str(e),
            "error_type": type(e).__name__,
            "native_impl": True,
            "elapsed": elapsed,
        }


def _format_conversation_output(messages: List[Dict[str, Any]]) -> str:
    """
    Format conversation messages into readable output.

    Args:
        messages: List of conversation messages

    Returns:
        Formatted output string
    """
    output_parts = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "assistant":
            # Extract text and tool uses from assistant messages
            if isinstance(content, list):
                for block in content:
                    block_type = block.get("type", "")

                    if block_type == "text":
                        text = block.get("text", "")
                        if text.strip():
                            output_parts.append(text)

                    elif block_type == "tool_use":
                        tool_name = block.get("name", "")
                        tool_input = block.get("input", {})
                        # Don't include tool uses in output to avoid clutter
                        # output_parts.append(f"[Using tool: {tool_name}]")
            elif isinstance(content, str):
                if content.strip():
                    output_parts.append(content)

    return "\n\n".join(output_parts)
