"""
Retry Strategy Module
====================
Advanced retry strategies for handling failed requests with configurable backoff patterns.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar, Union
from contextlib import asynccontextmanager, contextmanager

from ..config.schemas import RetryConfig
from ..config.settings import get_settings

# Type variable for generic function signatures
T = TypeVar('T')

# Logger
_logger = logging.getLogger(__name__)


class RetryStrategyType(Enum):
    """Types of retry strategies"""
    EXPONENTIAL_BACKOFF = "exponential_backoff"
    LINEAR_BACKOFF = "linear_backoff"
    CONSTANT_BACKOFF = "constant_backoff"
    CUSTOM = "custom"


class RetryCondition(Enum):
    """Conditions that can trigger a retry"""
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    HTTP_429 = "http_429"  # Rate limited
    HTTP_5XX = "http_5xx"  # Server errors
    CONNECTION_ERROR = "connection_error"
    SSL_ERROR = "ssl_error"
    PROXY_ERROR = "proxy_error"
    ANY_ERROR = "any_error"


@dataclass
class RetryResult:
    """Result of a retry operation"""
    success: bool
    attempt: int
    total_attempts: int
    delay_used: float = 0.0
    last_error: Optional[Exception] = None
    error_type: Optional[str] = None
    
    def __str__(self) -> str:
        if self.success:
            return f"RetryResult(success=True, attempts={self.attempt}/{self.total_attempts})"
        else:
            return f"RetryResult(success=False, attempts={self.attempt}/{self.total_attempts}, error={self.error_type})"


@dataclass
class RetryContext:
    """Context information for retry operations"""
    url: Optional[str] = None
    method: Optional[str] = None
    attempt: int = 0
    total_attempts: int = 0
    last_error: Optional[Exception] = None
    retry_after: Optional[float] = None  # Respect Retry-After header
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary"""
        return {
            'url': self.url,
            'method': self.method,
            'attempt': self.attempt,
            'total_attempts': self.total_attempts,
            'last_error': str(self.last_error) if self.last_error else None,
            'retry_after': self.retry_after
        }


class RetryError(Exception):
    """Exception raised when all retry attempts are exhausted"""
    
    def __init__(self, message: str, context: Optional[RetryContext] = None, 
                 last_error: Optional[Exception] = None):
        super().__init__(message)
        self.context = context
        self.last_error = last_error
        self.message = message


class MaxRetriesExceeded(RetryError):
    """Exception raised when maximum retry attempts are exceeded"""
    
    def __init__(self, context: RetryContext, last_error: Optional[Exception] = None):
        message = f"Max retries ({context.total_attempts}) exceeded for {context.url or 'unknown'}"
        super().__init__(message, context, last_error)


class RetryableError(Exception):
    """Base class for errors that can be retried"""
    
    retryable = True
    retry_condition = RetryCondition.ANY_ERROR


class NonRetryableError(Exception):
    """Base class for errors that should not be retried"""
    
    retryable = False


@dataclass
class RetryStrategy:
    """
    Base retry strategy with configurable parameters.
    
    This class provides the foundation for different retry strategies.
    Subclasses implement specific backoff algorithms.
    """
    
    # Maximum number of retry attempts
    max_retries: int = 3
    
    # Base delay between retries (in seconds)
    base_delay: float = 1.0
    
    # Maximum delay between retries (in seconds)
    max_delay: float = 60.0
    
    # Minimum delay between retries (in seconds)
    min_delay: float = 0.1
    
    # Exponential backoff multiplier
    exponential_base: float = 2.0
    
    # Jitter factor to randomize delays (0.0 to 1.0)
    jitter: float = 0.1
    
    # Specific HTTP status codes to retry
    retryable_status_codes: List[int] = field(default_factory=lambda: [
        429,  # Too Many Requests
        500,  # Internal Server Error
        502,  # Bad Gateway
        503,  # Service Unavailable
        504,  # Gateway Timeout
    ])
    
    # Error types that should trigger retries
    retryable_exceptions: List[Type[Exception]] = field(default_factory=lambda: [
        ConnectionError,
        TimeoutError,
        OSError,
    ])
    
    # Conditions that should trigger retries
    retry_conditions: List[RetryCondition] = field(default_factory=lambda: [
        RetryCondition.NETWORK_ERROR,
        RetryCondition.TIMEOUT,
        RetryCondition.HTTP_429,
        RetryCondition.HTTP_5XX,
        RetryCondition.CONNECTION_ERROR,
    ])
    
    # Whether to respect Retry-After header
    respect_retry_after: bool = True
    
    # Whether to use jitter
    use_jitter: bool = True
    
    def __post_init__(self):
        """Initialize and validate configuration"""
        # Ensure jitter is within valid range
        self.jitter = max(0.0, min(1.0, self.jitter))
        
        # Ensure delays are positive
        self.base_delay = max(0.0, self.base_delay)
        self.max_delay = max(self.base_delay, self.max_delay)
        self.min_delay = max(0.0, min(self.base_delay, self.min_delay))
        
        # Ensure exponential base is valid
        self.exponential_base = max(1.0, self.exponential_base)
    
    def calculate_delay(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """
        Calculate delay before next retry attempt.
        
        Args:
            attempt: Current attempt number (0-indexed)
            retry_after: Optional Retry-After value from response
            
        Returns:
            Delay in seconds before next attempt
        """
        # If Retry-After header is present and we should respect it
        if retry_after is not None and self.respect_retry_after:
            return min(retry_after, self.max_delay)
        
        # Calculate base delay based on strategy
        delay = self._calculate_base_delay(attempt)
        
        # Apply jitter if enabled
        if self.use_jitter and self.jitter > 0:
            jitter_range = delay * self.jitter
            delay = delay + random.uniform(-jitter_range, jitter_range)
        
        # Ensure delay is within bounds
        delay = max(self.min_delay, min(self.max_delay, delay))
        
        return delay
    
    def _calculate_base_delay(self, attempt: int) -> float:
        """
        Calculate the base delay for a given attempt.
        To be implemented by subclasses.
        """
        raise NotImplementedError("Subclasses must implement _calculate_base_delay")
    
    def should_retry(self, error: Exception, status_code: Optional[int] = None, 
                     context: Optional[RetryContext] = None) -> bool:
        """
        Determine if a request should be retried based on the error.
        
        Args:
            error: The exception that occurred
            status_code: Optional HTTP status code
            context: Optional retry context
            
        Returns:
            True if the request should be retried, False otherwise
        """
        # Check if error is explicitly non-retryable
        if hasattr(error, 'retryable') and not error.retryable:
            return False
        
        # Check if it's a RetryableError
        if isinstance(error, RetryableError):
            return True
        
        # Check specific conditions
        if hasattr(error, 'retry_condition'):
            return error.retry_condition in self.retry_conditions
        
        # Check if error type is in retryable exceptions
        if any(isinstance(error, exc_type) for exc_type in self.retryable_exceptions):
            return True
        
        # Check status code if provided
        if status_code is not None and status_code in self.retryable_status_codes:
            return True
        
        # Check for common error patterns
        error_str = str(error).lower()
        if ("timeout" in error_str and RetryCondition.TIMEOUT in self.retry_conditions) or \
           ("connection" in error_str and RetryCondition.CONNECTION_ERROR in self.retry_conditions):
            return True
        
        return False
    
    def get_attempts_remaining(self, context: RetryContext) -> int:
        """Get the number of attempts remaining"""
        return max(0, self.max_retries - context.attempt)
    
    def create_context(self, url: Optional[str] = None, method: Optional[str] = None) -> RetryContext:
        """Create a new retry context"""
        return RetryContext(url=url, method=method, total_attempts=self.max_retries)
    
    def reset(self) -> None:
        """Reset any internal state"""
        pass


class ExponentialBackoff(RetryStrategy):
    """
    Exponential backoff retry strategy.
    
    Delays increase exponentially with each attempt:
    delay = base_delay * (exponential_base ** attempt)
    """
    
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0, 
                 max_delay: float = 60.0, exponential_base: float = 2.0,
                 jitter: float = 0.1, **kwargs):
        super().__init__(
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            exponential_base=exponential_base,
            jitter=jitter,
            **kwargs
        )
    
    def _calculate_base_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay"""
        return self.base_delay * (self.exponential_base ** attempt)
    
    def __str__(self) -> str:
        return f"ExponentialBackoff(max_retries={self.max_retries}, base_delay={self.base_delay}, max_delay={self.max_delay})"


class LinearBackoff(RetryStrategy):
    """
    Linear backoff retry strategy.
    
    Delays increase linearly with each attempt:
    delay = base_delay + (attempt * increment)
    """
    
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0,
                 max_delay: float = 60.0, increment: float = 1.0,
                 jitter: float = 0.1, **kwargs):
        super().__init__(
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            jitter=jitter,
            **kwargs
        )
        self.increment = max(0.0, increment)
    
    def _calculate_base_delay(self, attempt: int) -> float:
        """Calculate linear backoff delay"""
        return self.base_delay + (attempt * self.increment)
    
    def __str__(self) -> str:
        return f"LinearBackoff(max_retries={self.max_retries}, base_delay={self.base_delay}, increment={self.increment})"


class ConstantBackoff(RetryStrategy):
    """
    Constant backoff retry strategy.
    
    Delays remain constant for all attempts:
    delay = base_delay
    """
    
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0,
                 max_delay: float = 60.0, jitter: float = 0.1, **kwargs):
        super().__init__(
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            jitter=jitter,
            **kwargs
        )
    
    def _calculate_base_delay(self, attempt: int) -> float:
        """Calculate constant delay"""
        return self.base_delay
    
    def __str__(self) -> str:
        return f"ConstantBackoff(max_retries={self.max_retries}, delay={self.base_delay})"


class AdaptiveBackoff(RetryStrategy):
    """
    Adaptive backoff retry strategy that adjusts based on response patterns.
    
    This strategy starts with exponential backoff but can adjust based on
    observed response times and error patterns.
    """
    
    def __init__(self, max_retries: int = 5, base_delay: float = 1.0,
                 max_delay: float = 120.0, exponential_base: float = 2.0,
                 jitter: float = 0.2, **kwargs):
        super().__init__(
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            exponential_base=exponential_base,
            jitter=jitter,
            **kwargs
        )
        self._attempt_history: List[Tuple[float, float]] = []  # (attempt, delay_used)
        self._success_rate: float = 1.0
        self._recent_failures: int = 0
    
    def _calculate_base_delay(self, attempt: int) -> float:
        """Calculate adaptive delay based on history"""
        # Base exponential calculation
        base_delay = self.base_delay * (self.exponential_base ** attempt)
        
        # Adjust based on recent failure rate
        if self._attempt_history:
            recent_attempts = self._attempt_history[-min(5, len(self._attempt_history)):]
            if recent_attempts:
                # If recent attempts are failing, increase delay more aggressively
                failure_rate = self._recent_failures / len(recent_attempts) if recent_attempts else 0
                if failure_rate > 0.5:  # More than 50% recent failures
                    base_delay *= (1 + failure_rate)  # Increase by failure rate
        
        return base_delay
    
    def record_attempt(self, success: bool, delay: float) -> None:
        """Record the result of a retry attempt"""
        attempt_num = len(self._attempt_history) + 1
        self._attempt_history.append((attempt_num, delay))
        
        if not success:
            self._recent_failures += 1
        else:
            self._recent_failures = max(0, self._recent_failures - 1)
        
        # Keep history size limited
        if len(self._attempt_history) > 10:
            self._attempt_history = self._attempt_history[-5:]
            self._recent_failures = max(0, self._recent_failures - 1)
    
    def reset(self) -> None:
        """Reset attempt history"""
        self._attempt_history.clear()
        self._recent_failures = 0
    
    def __str__(self) -> str:
        return f"AdaptiveBackoff(max_retries={self.max_retries}, base_delay={self.base_delay}, history_size={len(self._attempt_history)})"


def create_retry_strategy(strategy_type: Union[str, RetryStrategyType] = RetryStrategyType.EXPONENTIAL_BACKOFF,
                          config: Optional[RetryConfig] = None, **kwargs) -> RetryStrategy:
    """
    Factory function to create retry strategies.
    
    Args:
        strategy_type: Type of retry strategy to create
        config: Optional retry configuration
        **kwargs: Additional parameters for the strategy
        
    Returns:
        Configured retry strategy instance
    """
    if config is None:
        # Try to get from settings
        try:
            settings = get_settings()
            config = settings.retry
        except Exception:
            config = RetryConfig()
    
    # Merge config with kwargs
    if config:
        kwargs = {**config.to_dict(), **kwargs}
    
    if isinstance(strategy_type, str):
        strategy_type = RetryStrategyType(strategy_type)
    
    if strategy_type == RetryStrategyType.EXPONENTIAL_BACKOFF:
        return ExponentialBackoff(**kwargs)
    elif strategy_type == RetryStrategyType.LINEAR_BACKOFF:
        return LinearBackoff(**kwargs)
    elif strategy_type == RetryStrategyType.CONSTANT_BACKOFF:
        return ConstantBackoff(**kwargs)
    elif strategy_type == RetryStrategyType.CUSTOM:
        # Allow custom strategy classes to be passed
        custom_class = kwargs.get('strategy_class')
        if custom_class and isinstance(custom_class, type) and issubclass(custom_class, RetryStrategy):
            return custom_class(**{k: v for k, v in kwargs.items() if k != 'strategy_class'})
        return ExponentialBackoff(**kwargs)
    else:
        return ExponentialBackoff(**kwargs)


# Decorator for retryable functions
def retryable(func: Optional[Callable] = None, 
             max_retries: int = 3, 
             strategy: Optional[RetryStrategy] = None,
             retry_conditions: Optional[List[RetryCondition]] = None,
             **kwargs) -> Callable:
    """
    Decorator to make a function retryable.
    
    Args:
        func: Function to wrap
        max_retries: Maximum number of retry attempts
        strategy: Retry strategy to use (defaults to ExponentialBackoff)
        retry_conditions: Specific conditions to retry
        **kwargs: Additional parameters for the retry strategy
        
    Returns:
        Wrapped function with retry logic
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def sync_wrapper(*args, **fn_kwargs) -> Any:
            return _retry_sync(fn, args, fn_kwargs, max_retries, strategy, retry_conditions, **kwargs)
        
        @wraps(fn)
        async def async_wrapper(*args, **fn_kwargs) -> Any:
            return await _retry_async(fn, args, fn_kwargs, max_retries, strategy, retry_conditions, **kwargs)
        
        # Return the appropriate wrapper based on whether the function is async
        if asyncio.iscoroutinefunction(fn):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def _retry_sync(func: Callable, args: tuple, kwargs: Dict, max_retries: int,
                strategy: Optional[RetryStrategy], retry_conditions: Optional[List[RetryCondition]],
                **strategy_kwargs) -> Any:
    """Synchronous retry implementation"""
    if strategy is None:
        strategy = ExponentialBackoff(max_retries=max_retries, **strategy_kwargs)
    
    if retry_conditions:
        strategy.retry_conditions = retry_conditions
    
    context = strategy.create_context()
    last_error = None
    
    while context.attempt <= strategy.max_retries:
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            last_error = e
            context.last_error = e
            context.attempt += 1
            
            # Check if we should retry
            if context.attempt <= strategy.max_retries and strategy.should_retry(e):
                delay = strategy.calculate_delay(context.attempt - 1)
                _logger.debug(f"Retry attempt {context.attempt}/{strategy.max_retries} for {func.__name__}: {e}")
                
                if delay > 0:
                    time.sleep(delay)
            else:
                break
    
    # All retries exhausted
    raise MaxRetriesExceeded(context, last_error)


async def _retry_async(func: Callable, args: tuple, kwargs: Dict, max_retries: int,
                       strategy: Optional[RetryStrategy], retry_conditions: Optional[List[RetryCondition]],
                       **strategy_kwargs) -> Any:
    """Asynchronous retry implementation"""
    if strategy is None:
        strategy = ExponentialBackoff(max_retries=max_retries, **strategy_kwargs)
    
    if retry_conditions:
        strategy.retry_conditions = retry_conditions
    
    context = strategy.create_context()
    last_error = None
    
    while context.attempt <= strategy.max_retries:
        try:
            result = await func(*args, **kwargs)
            return result
        except Exception as e:
            last_error = e
            context.last_error = e
            context.attempt += 1
            
            # Check if we should retry
            if context.attempt <= strategy.max_retries and strategy.should_retry(e):
                delay = strategy.calculate_delay(context.attempt - 1)
                _logger.debug(f"Async retry attempt {context.attempt}/{strategy.max_retries} for {func.__name__}: {e}")
                
                if delay > 0:
                    await asyncio.sleep(delay)
            else:
                break
    
    # All retries exhausted
    raise MaxRetriesExceeded(context, last_error)


@contextmanager
def retry_context(strategy: Optional[RetryStrategy] = None, **kwargs) -> Any:
    """
    Context manager for retry operations.
    
    Usage:
        with retry_context() as retry:
            for attempt in retry:
                try:
                    do_something()
                    break  # Success, exit loop
                except Exception as e:
                    retry.record_failure(e)
    
    Args:
        strategy: Retry strategy to use
        **kwargs: Parameters for the strategy
        
    Yields:
        RetryIterator for managing retry attempts
    """
    if strategy is None:
        strategy = ExponentialBackoff(**kwargs)
    
    class RetryIterator:
        def __init__(self, strategy: RetryStrategy):
            self.strategy = strategy
            self.context = strategy.create_context()
            self.attempt = 0
            self.last_error = None
        
        def __iter__(self):
            return self
        
        def __next__(self):
            if self.attempt <= self.strategy.max_retries:
                self.attempt += 1
                self.context.attempt = self.attempt - 1
                return self
            else:
                raise StopIteration
        
        def record_failure(self, error: Exception, status_code: Optional[int] = None) -> bool:
            """Record a failure and determine if retry should continue"""
            self.last_error = error
            self.context.last_error = error
            
            if self.strategy.should_retry(error, status_code, self.context):
                delay = self.strategy.calculate_delay(self.attempt - 1)
                if delay > 0:
                    time.sleep(delay)
                return True
            return False
        
        def get_delay(self) -> float:
            """Get the delay for the next retry"""
            return self.strategy.calculate_delay(self.attempt)
    
    yield RetryIterator(strategy)


@asynccontextmanager
async def async_retry_context(strategy: Optional[RetryStrategy] = None, **kwargs) -> Any:
    """
    Async context manager for retry operations.
    
    Usage:
        async with async_retry_context() as retry:
            for attempt in retry:
                try:
                    await do_something_async()
                    break  # Success, exit loop
                except Exception as e:
                    if not await retry.record_failure(e):
                        break
    
    Args:
        strategy: Retry strategy to use
        **kwargs: Parameters for the strategy
        
    Yields:
        AsyncRetryIterator for managing retry attempts
    """
    if strategy is None:
        strategy = ExponentialBackoff(**kwargs)
    
    class AsyncRetryIterator:
        def __init__(self, strategy: RetryStrategy):
            self.strategy = strategy
            self.context = strategy.create_context()
            self.attempt = 0
            self.last_error = None
        
        def __iter__(self):
            return self
        
        def __next__(self):
            if self.attempt <= self.strategy.max_retries:
                self.attempt += 1
                self.context.attempt = self.attempt - 1
                return self
            else:
                raise StopIteration
        
        async def record_failure(self, error: Exception, status_code: Optional[int] = None) -> bool:
            """Record a failure and determine if retry should continue"""
            self.last_error = error
            self.context.last_error = error
            
            if self.strategy.should_retry(error, status_code, self.context):
                delay = self.strategy.calculate_delay(self.attempt - 1)
                if delay > 0:
                    await asyncio.sleep(delay)
                return True
            return False
        
        def get_delay(self) -> float:
            """Get the delay for the next retry"""
            return self.strategy.calculate_delay(self.attempt)
    
    yield AsyncRetryIterator(strategy)


# Convenience functions for common use cases

def with_exponential_backoff(max_retries: int = 3, base_delay: float = 1.0, 
                            max_delay: float = 60.0, **kwargs) -> RetryStrategy:
    """Create an exponential backoff retry strategy"""
    return ExponentialBackoff(max_retries=max_retries, base_delay=base_delay, 
                             max_delay=max_delay, **kwargs)


def with_linear_backoff(max_retries: int = 3, base_delay: float = 1.0,
                        increment: float = 1.0, max_delay: float = 60.0, **kwargs) -> RetryStrategy:
    """Create a linear backoff retry strategy"""
    return LinearBackoff(max_retries=max_retries, base_delay=base_delay,
                        increment=increment, max_delay=max_delay, **kwargs)


def with_constant_backoff(max_retries: int = 3, delay: float = 1.0, **kwargs) -> RetryStrategy:
    """Create a constant backoff retry strategy"""
    return ConstantBackoff(max_retries=max_retries, base_delay=delay, **kwargs)


# Module exports
__all__ = [
    # Strategy classes
    'RetryStrategy',
    'ExponentialBackoff', 
    'LinearBackoff',
    'ConstantBackoff',
    'AdaptiveBackoff',
    
    # Result and context classes
    'RetryResult',
    'RetryContext',
    'RetryError',
    'MaxRetriesExceeded',
    'RetryableError',
    'NonRetryableError',
    
    # Enums
    'RetryStrategyType',
    'RetryCondition',
    
    # Factory functions
    'create_retry_strategy',
    'with_exponential_backoff',
    'with_linear_backoff', 
    'with_constant_backoff',
    
    # Decorators
    'retryable',
    
    # Context managers
    'retry_context',
    'async_retry_context',
]