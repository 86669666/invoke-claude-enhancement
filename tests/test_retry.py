"""Tests for RetryHandler."""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

# Add src/python to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "python"))

from retry import ErrorCategory, RetryConfig, RetryHandler, with_retry


class MockHTTPError(Exception):
    """Mock HTTP error with status_code attribute."""
    
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class TestRetryHandler:
    """Test suite for RetryHandler."""
    
    def test_classify_transient_error(self):
        """Test classification of transient errors."""
        handler = RetryHandler()
        
        assert handler.classify_error(Exception("connection timeout")) == ErrorCategory.TRANSIENT
        assert handler.classify_error(Exception("service unavailable")) == ErrorCategory.TRANSIENT
        assert handler.classify_error(MockHTTPError("Server error", 503)) == ErrorCategory.TRANSIENT
    
    def test_classify_rate_limit_error(self):
        """Test classification of rate limit errors."""
        handler = RetryHandler()
        
        assert handler.classify_error(Exception("rate limit exceeded")) == ErrorCategory.RATE_LIMIT
        assert handler.classify_error(MockHTTPError("Too many requests", 429)) == ErrorCategory.RATE_LIMIT
    
    def test_classify_auth_error(self):
        """Test classification of authentication errors."""
        handler = RetryHandler()
        
        assert handler.classify_error(Exception("unauthorized")) == ErrorCategory.AUTH
        assert handler.classify_error(Exception("invalid api key")) == ErrorCategory.AUTH
        assert handler.classify_error(MockHTTPError("Forbidden", 403)) == ErrorCategory.AUTH  # 403 contains "forbidden"
    
    def test_classify_invalid_request(self):
        """Test classification of invalid request errors."""
        handler = RetryHandler()
        
        assert handler.classify_error(Exception("bad request")) == ErrorCategory.INVALID_REQUEST
        assert handler.classify_error(MockHTTPError("Invalid input", 400)) == ErrorCategory.INVALID_REQUEST
    
    def test_should_retry_transient(self):
        """Test that transient errors are retried."""
        handler = RetryHandler(RetryConfig(max_attempts=3))
        
        error = Exception("connection timeout")
        assert handler.should_retry(error, attempt=1) is True
        assert handler.should_retry(error, attempt=2) is True
        assert handler.should_retry(error, attempt=3) is False  # Max attempts reached
    
    def test_should_not_retry_auth_errors(self):
        """Test that auth errors are not retried."""
        handler = RetryHandler()
        
        error = Exception("unauthorized")
        assert handler.should_retry(error, attempt=1) is False
    
    def test_should_not_retry_invalid_request(self):
        """Test that invalid request errors are not retried."""
        handler = RetryHandler()
        
        error = Exception("bad request: invalid parameter")
        assert handler.should_retry(error, attempt=1) is False
    
    def test_calculate_delay_exponential(self):
        """Test exponential backoff delay calculation."""
        config = RetryConfig(initial_delay=1.0, exponential_base=2.0, jitter=False)
        handler = RetryHandler(config)
        
        # Attempt 1: 1 * 2^0 = 1.0
        assert handler.calculate_delay(1, ErrorCategory.TRANSIENT) == 1.0
        
        # Attempt 2: 1 * 2^1 = 2.0
        assert handler.calculate_delay(2, ErrorCategory.TRANSIENT) == 2.0
        
        # Attempt 3: 1 * 2^2 = 4.0
        assert handler.calculate_delay(3, ErrorCategory.TRANSIENT) == 4.0
    
    def test_calculate_delay_rate_limit_multiplier(self):
        """Test that rate limit errors get longer delays."""
        config = RetryConfig(initial_delay=1.0, exponential_base=2.0, jitter=False)
        handler = RetryHandler(config)
        
        transient_delay = handler.calculate_delay(2, ErrorCategory.TRANSIENT)
        rate_limit_delay = handler.calculate_delay(2, ErrorCategory.RATE_LIMIT)
        
        assert rate_limit_delay == transient_delay * 2
    
    def test_calculate_delay_max_cap(self):
        """Test that delay is capped at max_delay."""
        config = RetryConfig(initial_delay=10.0, max_delay=30.0, jitter=False)
        handler = RetryHandler(config)
        
        # Should cap at 30.0 even if exponential would be higher
        delay = handler.calculate_delay(10, ErrorCategory.TRANSIENT)
        assert delay == 30.0
    
    def test_execute_with_retry_success_first_attempt(self):
        """Test successful execution on first attempt."""
        handler = RetryHandler()
        mock_func = Mock(return_value="success")
        
        result = handler.execute_with_retry(mock_func, arg1="test")
        
        assert result == "success"
        assert mock_func.call_count == 1
        mock_func.assert_called_with(arg1="test")
    
    def test_execute_with_retry_success_after_failures(self):
        """Test successful execution after transient failures."""
        config = RetryConfig(max_attempts=3, initial_delay=0.1)
        handler = RetryHandler(config)
        
        mock_func = Mock(side_effect=[
            Exception("connection timeout"),
            Exception("service unavailable"),
            "success"
        ])
        
        result = handler.execute_with_retry(mock_func)
        
        assert result == "success"
        assert mock_func.call_count == 3
    
    def test_execute_with_retry_exhausted(self):
        """Test that exception is raised when retries exhausted."""
        config = RetryConfig(max_attempts=2, initial_delay=0.1)
        handler = RetryHandler(config)
        
        mock_func = Mock(side_effect=Exception("persistent error"))
        
        with pytest.raises(Exception, match="persistent error"):
            handler.execute_with_retry(mock_func)
        
        assert mock_func.call_count == 2
    
    def test_execute_with_retry_non_retryable(self):
        """Test that non-retryable errors fail immediately."""
        handler = RetryHandler()
        mock_func = Mock(side_effect=Exception("unauthorized"))
        
        with pytest.raises(Exception, match="unauthorized"):
            handler.execute_with_retry(mock_func)
        
        assert mock_func.call_count == 1  # No retries
    
    def test_with_retry_decorator(self):
        """Test the with_retry decorator."""
        call_count = {"value": 0}
        
        @with_retry(max_attempts=3, initial_delay=0.1)
        def flaky_function():
            call_count["value"] += 1
            if call_count["value"] < 3:
                raise Exception("temporary failure")
            return "success"
        
        result = flaky_function()
        
        assert result == "success"
        assert call_count["value"] == 3
