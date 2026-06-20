"""Retry handler with error classification and exponential backoff."""

import time
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional, Tuple, Type

import structlog

logger = structlog.get_logger(__name__)


class ErrorCategory(Enum):
    """Classification of errors for retry decisions."""
    
    TRANSIENT = "transient"  # Temporary failures, safe to retry
    RATE_LIMIT = "rate_limit"  # Rate limiting, needs longer backoff
    AUTH = "auth"  # Authentication/authorization, likely permanent
    INVALID_REQUEST = "invalid_request"  # Bad input, no point retrying
    FATAL = "fatal"  # Unrecoverable errors
    UNKNOWN = "unknown"  # Cannot classify


class RetryConfig:
    """Configuration for retry behavior."""
    
    def __init__(
        self,
        max_attempts: int = 5,
        initial_delay: float = 2.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ):
        """
        Initialize retry configuration.
        
        Args:
            max_attempts: Maximum number of retry attempts (including initial)
            initial_delay: Initial delay in seconds before first retry
            max_delay: Maximum delay cap in seconds
            exponential_base: Base for exponential backoff calculation
            jitter: Add random jitter to prevent thundering herd
        """
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter


class RetryHandler:
    """Handles retry logic with error classification."""
    
    # HTTP status codes that are safe to retry
    RETRYABLE_STATUS_CODES = {
        408,  # Request Timeout
        429,  # Too Many Requests
        500,  # Internal Server Error
        502,  # Bad Gateway
        503,  # Service Unavailable
        504,  # Gateway Timeout
    }
    
    # Error messages indicating transient failures
    TRANSIENT_ERROR_PATTERNS = [
        "connection",
        "timeout",
        "temporary",
        "unavailable",
        "overloaded",
        "try again",
    ]
    
    def __init__(self, config: Optional[RetryConfig] = None):
        """
        Initialize retry handler.
        
        Args:
            config: Retry configuration (uses defaults if None)
        """
        self.config = config or RetryConfig()
    
    def classify_error(self, error: Exception) -> ErrorCategory:
        """
        Classify an error to determine retry strategy.
        
        Args:
            error: The exception to classify
            
        Returns:
            ErrorCategory indicating how to handle the error
        """
        error_str = str(error).lower()
        
        # Check for rate limiting
        if "rate limit" in error_str or "429" in error_str:
            return ErrorCategory.RATE_LIMIT
        
        # Check for authentication errors
        if any(keyword in error_str for keyword in ["unauthorized", "forbidden", "401", "403", "invalid api key"]):
            return ErrorCategory.AUTH
        
        # Check for invalid request
        if any(keyword in error_str for keyword in ["bad request", "invalid", "400", "422"]):
            return ErrorCategory.INVALID_REQUEST
        
        # Check for transient errors
        if any(pattern in error_str for pattern in self.TRANSIENT_ERROR_PATTERNS):
            return ErrorCategory.TRANSIENT
        
        # Check HTTP status code if available
        if hasattr(error, "status_code"):
            if error.status_code in self.RETRYABLE_STATUS_CODES:
                return ErrorCategory.RATE_LIMIT if error.status_code == 429 else ErrorCategory.TRANSIENT
            elif 400 <= error.status_code < 500:
                return ErrorCategory.INVALID_REQUEST
        
        return ErrorCategory.UNKNOWN
    
    def should_retry(self, error: Exception, attempt: int) -> bool:
        """
        Determine if an error should be retried.
        
        Args:
            error: The exception that occurred
            attempt: Current attempt number (1-indexed)
            
        Returns:
            True if should retry, False otherwise
        """
        if attempt >= self.config.max_attempts:
            return False
        
        category = self.classify_error(error)
        
        # Never retry auth or invalid request errors
        if category in (ErrorCategory.AUTH, ErrorCategory.INVALID_REQUEST, ErrorCategory.FATAL):
            return False
        
        # Retry transient, rate limit, and unknown errors
        return category in (ErrorCategory.TRANSIENT, ErrorCategory.RATE_LIMIT, ErrorCategory.UNKNOWN)
    
    def calculate_delay(self, attempt: int, category: ErrorCategory) -> float:
        """
        Calculate delay before next retry.
        
        Args:
            attempt: Current attempt number (1-indexed)
            category: Error category
            
        Returns:
            Delay in seconds
        """
        # Base exponential backoff
        delay = self.config.initial_delay * (self.config.exponential_base ** (attempt - 1))
        
        # Rate limit errors get longer delays
        if category == ErrorCategory.RATE_LIMIT:
            delay *= 2
        
        # Cap at max delay
        delay = min(delay, self.config.max_delay)
        
        # Add jitter if enabled
        if self.config.jitter:
            import random
            jitter_range = delay * 0.1  # ±10% jitter
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0.1, delay)  # Minimum 100ms
    
    def execute_with_retry(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any
    ) -> Any:
        """
        Execute a function with retry logic.
        
        Args:
            func: Function to execute
            *args: Positional arguments to pass to func
            **kwargs: Keyword arguments to pass to func
            
        Returns:
            Result from successful function execution
            
        Raises:
            Last exception if all retries exhausted
        """
        last_error: Optional[Exception] = None
        
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                result = func(*args, **kwargs)
                
                if attempt > 1:
                    logger.info(
                        "retry_success",
                        attempt=attempt,
                        total_attempts=self.config.max_attempts
                    )
                
                return result
                
            except Exception as error:
                last_error = error
                category = self.classify_error(error)
                
                logger.warning(
                    "retry_attempt_failed",
                    attempt=attempt,
                    max_attempts=self.config.max_attempts,
                    error_type=type(error).__name__,
                    error_category=category.value,
                    error_message=str(error)
                )
                
                if not self.should_retry(error, attempt):
                    logger.error(
                        "retry_abandoned",
                        reason="non_retryable_error",
                        error_category=category.value
                    )
                    raise
                
                if attempt < self.config.max_attempts:
                    delay = self.calculate_delay(attempt, category)
                    logger.info("retry_waiting", delay_seconds=delay)
                    time.sleep(delay)
        
        # All retries exhausted
        logger.error("retry_exhausted", attempts=self.config.max_attempts)
        raise last_error  # type: ignore


def with_retry(
    max_attempts: int = 5,
    initial_delay: float = 2.0,
    max_delay: float = 60.0,
) -> Callable:
    """
    Decorator to add retry logic to a function.
    
    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay cap in seconds
        
    Returns:
        Decorated function with retry logic
        
    Example:
        @with_retry(max_attempts=3, initial_delay=1.0)
        def call_api():
            return requests.get("https://api.example.com")
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            config = RetryConfig(
                max_attempts=max_attempts,
                initial_delay=initial_delay,
                max_delay=max_delay
            )
            handler = RetryHandler(config)
            return handler.execute_with_retry(func, *args, **kwargs)
        return wrapper
    return decorator
