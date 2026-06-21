"""Anthropic Messages API client with native Python implementation."""

import json
from typing import Any, Dict, List, Optional

import httpx
import structlog

from config import ConfigLoader
from retry import RetryHandler, with_retry

logger = structlog.get_logger(__name__)


class AnthropicClient:
    """
    Native Python client for Anthropic Messages API.

    Replaces Node.js Claude Code CLI with direct API calls for better performance.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
        config_loader: Optional[ConfigLoader] = None,
    ):
        """
        Initialize Anthropic client.

        Args:
            api_key: Anthropic API key (or proxy key)
            base_url: Base URL for API (supports LiteLLM proxy)
            model: Default model to use
            timeout: Request timeout in seconds
            config_loader: Optional config loader (creates default if None)
        """
        self.config_loader = config_loader or ConfigLoader()
        config = self.config_loader.load()

        self.api_key = api_key or self._get_api_key()
        self.base_url = (base_url or config["proxy"]["url"]).rstrip("/")
        self.model = model or config["claude"]["default_model"]
        self.timeout = timeout or config["claude"]["default_timeout"]

        self.retry_handler = RetryHandler()

        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )

    def _get_api_key(self) -> str:
        """Get API key from environment or config."""
        import os

        # Try environment variable first
        key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
        if key:
            return key

        raise ValueError(
            "No API key found. Set ANTHROPIC_API_KEY or CLAUDE_API_KEY environment variable."
        )

    def _build_headers(self, extended_thinking: bool = False) -> Dict[str, str]:
        """Build request headers."""
        headers = {
            "x-api-key": self.api_key,
        }

        if extended_thinking:
            headers["anthropic-beta"] = (
                "prompt-caching-2024-07-31,extended-thinking-2024-12-12"
            )

        return headers

    @with_retry(max_attempts=5, initial_delay=2.0, max_delay=60.0)
    def create_message(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        max_tokens: int = 16000,
        temperature: float = 1.0,
        system: Optional[str] = None,
        extended_thinking: bool = False,
        thinking_budget_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create a message using Anthropic Messages API.

        Args:
            prompt: User prompt
            model: Model to use (defaults to instance default)
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            system: System prompt
            extended_thinking: Enable extended thinking (reasoning)
            thinking_budget_tokens: Token budget for thinking (requires extended_thinking)

        Returns:
            API response dict with 'content', 'usage', etc.

        Raises:
            httpx.HTTPError: On API errors
        """
        model = model or self.model

        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }

        if system:
            payload["system"] = system

        if extended_thinking:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget_tokens or 10000,
            }

        logger.info(
            "api_request",
            model=model,
            prompt_length=len(prompt),
            max_tokens=max_tokens,
            extended_thinking=extended_thinking,
        )

        try:
            response = self.client.post(
                "/v1/messages",
                json=payload,
                headers=self._build_headers(extended_thinking=extended_thinking),
            )
            response.raise_for_status()

            result = response.json()

            logger.info(
                "api_response",
                model=result.get("model"),
                stop_reason=result.get("stop_reason"),
                input_tokens=result.get("usage", {}).get("input_tokens"),
                output_tokens=result.get("usage", {}).get("output_tokens"),
            )

            return result

        except httpx.HTTPStatusError as e:
            logger.error(
                "api_error",
                status_code=e.response.status_code,
                response=e.response.text[:500],
            )
            raise

    def extract_text(self, response: Dict[str, Any]) -> str:
        """
        Extract text content from API response.

        Args:
            response: API response dict

        Returns:
            Concatenated text from all text content blocks
        """
        content_blocks = response.get("content", [])
        text_parts = []

        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))

        return "\n".join(text_parts)

    def extract_thinking(self, response: Dict[str, Any]) -> Optional[str]:
        """
        Extract thinking content from API response (if extended thinking was enabled).

        Args:
            response: API response dict

        Returns:
            Thinking text if present, None otherwise
        """
        content_blocks = response.get("content", [])

        for block in content_blocks:
            if block.get("type") == "thinking":
                return block.get("thinking", "")

        return None

    def close(self):
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


def quick_call(
    prompt: str,
    *,
    model: Optional[str] = None,
    extended_thinking: bool = False,
    **kwargs,
) -> str:
    """
    Quick one-shot API call convenience function.

    Args:
        prompt: User prompt
        model: Model to use
        extended_thinking: Enable extended thinking
        **kwargs: Additional arguments passed to create_message

    Returns:
        Response text content

    Example:
        result = quick_call("What is 2+2?", model="claude-opus-4")
    """
    with AnthropicClient(model=model) as client:
        response = client.create_message(
            prompt, extended_thinking=extended_thinking, **kwargs
        )
        return client.extract_text(response)
