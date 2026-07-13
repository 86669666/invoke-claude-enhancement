"""invoke_claude - Python native implementation"""

__version__ = "0.2.5"

# Export main interfaces for both package and flat import modes
try:
    from .client import AnthropicClient, quick_call
    from .config import ConfigLoader
    from .retry import RetryHandler, with_retry
    from .bridge import invoke_claude_native, use_native_implementation
    from .tools import ToolExecutor, get_tool_definitions
except ImportError:
    # Flat import mode - these will be imported directly
    pass
