"""Bridge module to integrate native Python client into existing invoke_claude tool."""

import os
from typing import Any, Dict, Optional

# Import native Python implementation
try:
    from client import AnthropicClient
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
    **kwargs
) -> Dict[str, Any]:
    """
    Native Python implementation of invoke_claude.
    
    Args:
        prompt: Task instruction for Claude
        workdir: Working directory (informational, not used by native impl)
        model: Claude model name
        timeout: Request timeout in seconds
        proxy_url: LiteLLM proxy URL
        effort: Reasoning effort (low/medium/high/max/auto)
        **kwargs: Additional parameters (ignored)
        
    Returns:
        Result dict with 'output', 'status', 'model', 'usage'
    """
    if not NATIVE_IMPL_AVAILABLE:
        raise RuntimeError(
            "Native implementation not available. Install dependencies: "
            "httpx, tenacity, structlog, tomli (Python < 3.11)"
        )
    
    # Map effort to thinking budget
    effort_to_budget = {
        "low": 2000,
        "medium": 10000,
        "high": 20000,
        "max": 50000,
        "auto": 10000,
    }
    
    extended_thinking = effort is not None
    thinking_budget = effort_to_budget.get(effort or "auto", 10000) if extended_thinking else None
    
    # Build system prompt with workdir context
    system_prompt = f"""You are Claude Code, working in project directory: {workdir}

Complete the task described in the user prompt. Be thorough and verify your work."""
    
    try:
        with AnthropicClient(
            base_url=proxy_url,
            model=model,
            timeout=timeout,
        ) as client:
            response = client.create_message(
                prompt,
                system=system_prompt,
                extended_thinking=extended_thinking,
                thinking_budget_tokens=thinking_budget,
                max_tokens=16000,
            )
            
            text_output = client.extract_text(response)
            thinking_output = client.extract_thinking(response) if extended_thinking else None
            
            # Format output similar to Claude Code CLI
            output_parts = []
            if thinking_output:
                output_parts.append(f"[Thinking]\n{thinking_output}\n")
            output_parts.append(text_output)
            
            return {
                "output": "\n".join(output_parts),
                "status": "completed",
                "model": response.get("model"),
                "usage": response.get("usage", {}),
                "stop_reason": response.get("stop_reason"),
                "native_impl": True,
            }
    
    except Exception as e:
        return {
            "output": "",
            "status": "error",
            "error": str(e),
            "error_type": type(e).__name__,
            "native_impl": True,
        }
