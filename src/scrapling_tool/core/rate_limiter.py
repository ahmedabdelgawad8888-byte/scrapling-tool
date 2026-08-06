"""
Rate Limiter Module
===================
Advanced rate limiting for controlling request frequency and preventing overloading.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager
import threading

from ..config.schemas import Configuration
from ..config.settings import get_settings

# Logger
_logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting"""
    requests: int = 100  # Number of requests
    period: float = 60.0  # Time period in seconds
    per_domain: bool = True  # Apply rate limit per domain
    per_ip: bool = False  # Apply rate limit per IP
    burst_size: int = 10  # Maximum burst size
    burst_refill_rate: int = 1  # Tokens added per second during burst
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'requests': self.requests,
            'period': self.period,
            'per_domain': self.per_domain,
            'per_ip': self.per_ip,
            'burst_size': self.burst_size,
            'burst_refill_rate': self.burst_refill_rate,
        }


class RateLimitError(Exception):
    """Rate limit exceeded error"""
    
    def __init__(self, message: str = "Rate limit exceeded", 
                 retry_after: Optional[float] = None, limit: int = 0, remaining: int = 0):
        super().__init__(message)
        self.retry_after = retry_after
        self.limit = limit
        self.remaining = remaining
    
    def __str__(self) -> str:
        parts = [f"RateLimitError: {super().__str__()}"]
        if self.limit:
            parts.append(f"Limit: {self.limit}")
        if self.remaining >= 0:
            parts.append(f"Remaining: {self.remaining}")
        if self.retry_after:
            parts.append(f"Retry-After: {self.retry_after:.2f}s")
        return " | ".join(parts)


@dataclass
class RateLimitStatus:
    """Current rate limit status"""
    allowed: int = 0
    used: int = 0
    remaining: int = 0
    reset_time: float = 0.0
    burst_tokens: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'allowed': self.allowed,
            'used': self.used,
            'remaining': self.remaining,
            'reset_time': self.reset_time,
            'burst_tokens': self.burst_tokens,
            'reset_in': max(0, self.reset_time - time.time()),
        }
    
    def is_allowed(self) -> bool:
        return self.remaining > 0 or self.burst_tokens > 0


class TokenBucket:
    """
    Token bucket algorithm for rate limiting.
    
    This is a classic rate limiting algorithm that allows for burst traffic
    while maintaining a long-term average rate.
    """
    
    def __init__(self, capacity: int, refill_rate: float, timestamp: Optional[float] = None):
        """
        Initialize TokenBucket.
        
        Args:
            capacity: Maximum number of tokens (bucket size)
            refill_rate: Tokens added per second
            timestamp: Current timestamp (defaults to current time)
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity  # Start with full bucket
        self.timestamp = timestamp or time.time()
        self._lock = threading.RLock()
        self._async_lock = asyncio.Lock()
    
    def _refill(self, timestamp: Optional[float] = None):
        """Refill tokens based on elapsed time"""
        now = timestamp or time.time()
        elapsed = now - self.timestamp
        
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.timestamp = now
    
    def consume(self, tokens: int = 1) -> bool:
        """
        Consume tokens from the bucket.
        
        Args:
            tokens: Number of tokens to consume
            
        Returns:
            True if tokens were consumed, False if not enough tokens
        """
        with self._lock:
            self._refill()
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            
            return False
    
    async def consume_async(self, tokens: int = 1) -> bool:
        """
        Async version of consume.
        
        Args:
            tokens: Number of tokens to consume
            
        Returns:
            True if tokens were consumed, False if not enough tokens
        """
        async with self._async_lock:
            self._refill()
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            
            return False
    
    def available(self) -> int:
        """Get number of available tokens"""
        with self._lock:
            self._refill()
            return int(self.tokens)
    
    def wait_time(self, tokens: int = 1) -> float:
        """
        Calculate time to wait for requested tokens.
        
        Args:
            tokens: Number of tokens needed
            
        Returns:
            Time in seconds to wait for tokens
        """
        with self._lock:
            self._refill()
            
            if self.tokens >= tokens:
                return 0.0
            
            deficit = tokens - self.tokens
            return deficit / self.refill_rate
    
    async def wait_for_tokens(self, tokens: int = 1, timeout: Optional[float] = None) -> bool:
        """
        Wait until tokens are available.
        
        Args:
            tokens: Number of tokens needed
            timeout: Maximum time to wait (None = wait forever)
            
        Returns:
            True if tokens became available, False if timeout occurred
        """
        start_time = time.time()
        
        while True:
            async with self._async_lock:
                self._refill()
                
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True
                
                # Calculate wait time
                wait_time = self.wait_time(tokens)
                
                # Check timeout
                if timeout is not None:
                    elapsed = time.time() - start_time
                    if elapsed >= timeout:
                        return False
                    
                    # Adjust wait time for timeout
                    remaining_timeout = timeout - elapsed
                    wait_time = min(wait_time, remaining_timeout)
                
                # Wait and allow other coroutines to run
                if wait_time > 0:
                    # Release lock during wait
                    pass
            
            if wait_time > 0:
                await asyncio.sleep(wait_time)
    
    def reset(self) -> None:
        """Reset bucket to full capacity"""
        with self._lock:
            self.tokens = self.capacity
            self.timestamp = time.time()


class LeakyBucket:
    """
    Leaky bucket algorithm for rate limiting.
    
    This algorithm smooths out burst traffic by processing requests
    at a fixed rate.
    """
    
    def __init__(self, capacity: int, leak_rate: float, timestamp: Optional[float] = None):
        """
        Initialize LeakyBucket.
        
        Args:
            capacity: Maximum bucket size (queue size)
            leak_rate: Items processed per second
            timestamp: Current timestamp
        """
        self.capacity = capacity
        self.leak_rate = leak_rate
        self.queue = 0.0  # Current queue size
        self.timestamp = timestamp or time.time()
        self._lock = threading.RLock()
        self._async_lock = asyncio.Lock()
    
    def _update(self, timestamp: Optional[float] = None):
        """Update queue size based on elapsed time"""
        now = timestamp or time.time()
        elapsed = now - self.timestamp
        
        if elapsed > 0:
            # Leak items from the queue
            leaked = elapsed * self.leak_rate
            self.queue = max(0, self.queue - leaked)
            self.timestamp = now
    
    def add(self) -> bool:
        """
        Add item to the bucket.
        
        Returns:
            True if item was added, False if bucket is full
        """
        with self._lock:
            self._update()
            
            if self.queue < self.capacity:
                self.queue += 1
                return True
            
            return False
    
    async def add_async(self) -> bool:
        """
        Async version of add.
        
        Returns:
            True if item was added, False if bucket is full
        """
        async with self._async_lock:
            self._update()
            
            if self.queue < self.capacity:
                self.queue += 1
                return True
            
            return False
    
    def queue_size(self) -> float:
        """Get current queue size"""
        with self._lock:
            self._update()
            return self.queue
    
    def wait_time(self) -> float:
        """Calculate time to wait for next available slot"""
        with self._lock:
            self._update()
            
            if self.queue < self.capacity:
                return 0.0
            
            # Time to process one item
            return 1.0 / self.leak_rate
    
    def reset(self) -> None:
        """Reset bucket to empty"""
        with self._lock:
            self.queue = 0
            self.timestamp = time.time()


class FixedWindow:
    """
    Fixed window rate limiting algorithm.
    
    This algorithm divides time into fixed windows and allows a certain
    number of requests per window.
    """
    
    def __init__(self, requests: int, period: float, timestamp: Optional[float] = None):
        """
        Initialize FixedWindow.
        
        Args:
            requests: Number of requests allowed per window
            period: Window size in seconds
            timestamp: Current timestamp
        """
        self.requests = requests
        self.period = period
        self.count = 0
        self.window_start = timestamp or time.time()
        self._lock = threading.RLock()
        self._async_lock = asyncio.Lock()
    
    def _new_window(self, timestamp: Optional[float] = None):
        """Start a new window if current one has expired"""
        now = timestamp or time.time()
        elapsed = now - self.window_start
        
        if elapsed >= self.period:
            self.window_start = now
            self.count = 0
    
    def acquire(self) -> bool:
        """
        Acquire a request slot.
        
        Returns:
            True if slot was acquired, False if window limit reached
        """
        with self._lock:
            self._new_window()
            
            if self.count < self.requests:
                self.count += 1
                return True
            
            return False
    
    async def acquire_async(self) -> bool:
        """
        Async version of acquire.
        
        Returns:
            True if slot was acquired, False if window limit reached
        """
        async with self._async_lock:
            self._new_window()
            
            if self.count < self.requests:
                self.count += 1
                return True
            
            return False
    
    def remaining(self) -> int:
        """Get remaining requests in current window"""
        with self._lock:
            self._new_window()
            return max(0, self.requests - self.count)
    
    def reset_time(self) -> float:
        """Get time when current window resets"""
        with self._lock:
            return self.window_start + self.period
    
    def wait_time(self) -> float:
        """Calculate time to wait for next window"""
        with self._lock:
            self._new_window()
            return max(0, (self.window_start + self.period) - time.time())
    
    def reset(self) -> None:
        """Reset counter to zero"""
        with self._lock:
            self.count = 0
            self.window_start = time.time()


class SlidingWindow:
    """
    Sliding window rate limiting algorithm.
    
    This algorithm provides smoother rate limiting by considering
    requests in a sliding time window rather than fixed windows.
    """
    
    def __init__(self, requests: int, period: float):
        """
        Initialize SlidingWindow.
        
        Args:
            requests: Number of requests allowed in the period
            period: Time period in seconds
        """
        self.requests = requests
        self.period = period
        self.timestamps: List[float] = []
        self._lock = threading.RLock()
        self._async_lock = asyncio.Lock()
    
    def acquire(self) -> bool:
        """
        Acquire a request slot.
        
        Returns:
            True if slot was acquired, False if rate limit exceeded
        """
        with self._lock:
            now = time.time()
            
            # Remove timestamps outside the current window
            cutoff = now - self.period
            self.timestamps = [ts for ts in self.timestamps if ts > cutoff]
            
            if len(self.timestamps) < self.requests:
                self.timestamps.append(now)
                return True
            
            return False
    
    async def acquire_async(self) -> bool:
        """
        Async version of acquire.
        
        Returns:
            True if slot was acquired, False if rate limit exceeded
        """
        async with self._async_lock:
            now = time.time()
            
            # Remove timestamps outside the current window
            cutoff = now - self.period
            self.timestamps = [ts for ts in self.timestamps if ts > cutoff]
            
            if len(self.timestamps) < self.requests:
                self.timestamps.append(now)
                return True
            
            return False
    
    def count(self) -> int:
        """Get number of requests in current window"""
        with self._lock:
            now = time.time()
            cutoff = now - self.period
            return len([ts for ts in self.timestamps if ts > cutoff])
    
    def reset(self) -> None:
        """Reset all timestamps"""
        with self._lock:
            self.timestamps.clear()


class RateLimiter:
    """
    Advanced rate limiter with multiple algorithms and domain-specific limits.
    """
    
    def __init__(self, requests: int = 100, period: float = 60.0,
                 algorithm: str = 'token_bucket', per_domain: bool = True,
                 burst_size: Optional[int] = None, burst_refill_rate: Optional[int] = None):
        """
        Initialize RateLimiter.
        
        Args:
            requests: Number of requests allowed
            period: Time period in seconds
            algorithm: Rate limiting algorithm ('token_bucket', 'leaky_bucket', 'fixed_window', 'sliding_window')
            per_domain: Whether to apply rate limits per domain
            burst_size: Maximum burst size (for token bucket)
            burst_refill_rate: Tokens added per second during burst (for token bucket)
        """
        self.requests = requests
        self.period = period
        self.algorithm = algorithm.lower()
        self.per_domain = per_domain
        self.burst_size = burst_size or requests
        self.burst_refill_rate = burst_refill_rate or (requests / period)
        
        # Domain-specific limiters
        self._domain_limiters: Dict[str, Any] = {}
        self._global_limiter: Any = None
        self._lock = threading.RLock()
        self._async_lock = asyncio.Lock()
        
        # Statistics
        self.stats = {
            'allowed': 0,
            'blocked': 0,
            'waited': 0,
            'errors': 0,
        }
        
        # Initialize limiters
        self._init_limiters()
    
    def _init_limiters(self):
        """Initialize the rate limiting algorithm"""
        if self.algorithm == 'token_bucket':
            self._global_limiter = TokenBucket(
                capacity=self.burst_size,
                refill_rate=self.burst_refill_rate
            )
        elif self.algorithm == 'leaky_bucket':
            self._global_limiter = LeakyBucket(
                capacity=self.requests,
                leak_rate=self.requests / self.period
            )
        elif self.algorithm == 'fixed_window':
            self._global_limiter = FixedWindow(
                requests=self.requests,
                period=self.period
            )
        elif self.algorithm == 'sliding_window':
            self._global_limiter = SlidingWindow(
                requests=self.requests,
                period=self.period
            )
        else:
            # Default to token bucket
            self._global_limiter = TokenBucket(
                capacity=self.burst_size,
                refill_rate=self.burst_refill_rate
            )
    
    def _get_limiter(self, domain: Optional[str] = None) -> Any:
        """Get limiter for a specific domain or the global limiter"""
        if not self.per_domain or not domain:
            return self._global_limiter
        
        with self._lock:
            if domain not in self._domain_limiters:
                if self.algorithm == 'token_bucket':
                    self._domain_limiters[domain] = TokenBucket(
                        capacity=self.burst_size,
                        refill_rate=self.burst_refill_rate
                    )
                elif self.algorithm == 'leaky_bucket':
                    self._domain_limiters[domain] = LeakyBucket(
                        capacity=self.requests,
                        leak_rate=self.requests / self.period
                    )
                elif self.algorithm == 'fixed_window':
                    self._domain_limiters[domain] = FixedWindow(
                        requests=self.requests,
                        period=self.period
                    )
                elif self.algorithm == 'sliding_window':
                    self._domain_limiters[domain] = SlidingWindow(
                        requests=self.requests,
                        period=self.period
                    )
                else:
                    self._domain_limiters[domain] = TokenBucket(
                        capacity=self.burst_size,
                        refill_rate=self.burst_refill_rate
                    )
            
            return self._domain_limiters[domain]
    
    def extract_domain(self, url: str) -> str:
        """Extract domain from URL"""
        from urllib.parse import urlparse
        from ..core.utils import extract_domain
        
        if not url:
            return "global"
        
        try:
            parsed = urlparse(url)
            if parsed.netloc:
                return extract_domain(url)
            return "global"
        except Exception:
            return "global"
    
    def acquire(self, url: Optional[str] = None) -> bool:
        """
        Acquire a request slot.
        
        Args:
            url: URL for domain-specific rate limiting (optional)
            
        Returns:
            True if slot was acquired, False if rate limit exceeded
        """
        domain = self.extract_domain(url) if url else None
        limiter = self._get_limiter(domain)
        
        if self.algorithm == 'token_bucket':
            result = limiter.consume()
        elif self.algorithm == 'leaky_bucket':
            result = limiter.add()
        elif self.algorithm == 'fixed_window':
            result = limiter.acquire()
        elif self.algorithm == 'sliding_window':
            result = limiter.acquire()
        else:
            result = limiter.consume()
        
        if result:
            self.stats['allowed'] += 1
        else:
            self.stats['blocked'] += 1
        
        return result
    
    async def acquire_async(self, url: Optional[str] = None) -> bool:
        """
        Async version of acquire.
        
        Args:
            url: URL for domain-specific rate limiting (optional)
            
        Returns:
            True if slot was acquired, False if rate limit exceeded
        """
        domain = self.extract_domain(url) if url else None
        limiter = self._get_limiter(domain)
        
        if self.algorithm == 'token_bucket':
            result = await limiter.consume_async()
        elif self.algorithm == 'leaky_bucket':
            result = await limiter.add_async()
        elif self.algorithm == 'fixed_window':
            result = await limiter.acquire_async()
        elif self.algorithm == 'sliding_window':
            result = await limiter.acquire_async()
        else:
            result = await limiter.consume_async()
        
        if result:
            self.stats['allowed'] += 1
        else:
            self.stats['blocked'] += 1
        
        return result
    
    async def wait(self, url: Optional[str] = None, timeout: Optional[float] = None) -> bool:
        """
        Wait until a request slot is available.
        
        Args:
            url: URL for domain-specific rate limiting (optional)
            timeout: Maximum time to wait (None = wait forever)
            
        Returns:
            True if slot became available, False if timeout occurred
        """
        domain = self.extract_domain(url) if url else None
        limiter = self._get_limiter(domain)
        
        if self.algorithm == 'token_bucket':
            result = await limiter.wait_for_tokens(1, timeout)
        elif self.algorithm == 'leaky_bucket':
            # For leaky bucket, we need to wait for space
            while not await limiter.add_async():
                if timeout is not None and time.time() > (time.time() + timeout):
                    return False
                await asyncio.sleep(0.1)
            result = True
        elif self.algorithm == 'fixed_window':
            wait_time = limiter.wait_time()
            if wait_time > 0:
                if timeout is not None and wait_time > timeout:
                    return False
                await asyncio.sleep(wait_time)
            result = await limiter.acquire_async()
        elif self.algorithm == 'sliding_window':
            wait_time = limiter.wait_time() if hasattr(limiter, 'wait_time') else 0.1
            if wait_time > 0:
                if timeout is not None and wait_time > timeout:
                    return False
                await asyncio.sleep(wait_time)
            result = await limiter.acquire_async()
        else:
            result = await limiter.consume_async()
        
        if result:
            self.stats['allowed'] += 1
            self.stats['waited'] += 1
        else:
            self.stats['blocked'] += 1
        
        return result
    
    @asynccontextmanager
    async def acquire_context(self, url: Optional[str] = None, wait: bool = True,
                             timeout: Optional[float] = None):
        """
        Context manager for acquiring and releasing rate limit slots.
        
        Args:
            url: URL for domain-specific rate limiting
            wait: Whether to wait if rate limit is exceeded
            timeout: Maximum time to wait
            
        Yields:
            None when rate limit slot is acquired
        """
        acquired = False
        start_time = time.time()
        
        try:
            if wait:
                acquired = await self.wait(url, timeout)
            else:
                acquired = await self.acquire_async(url)
            
            if not acquired:
                raise RateLimitError(
                    "Rate limit exceeded",
                    retry_after=self.get_wait_time(url),
                    limit=self.requests,
                    remaining=self.get_remaining(url)
                )
            
            yield
            
        except Exception as e:
            # If we acquired but there was an error, we don't release
            # because we want to count this as a used slot
            self.stats['errors'] += 1
            raise e
    
    @contextmanager
    def acquire_sync_context(self, url: Optional[str] = None, wait: bool = True,
                            timeout: Optional[float] = None):
        """
        Synchronous context manager for rate limiting.
        
        Args:
            url: URL for domain-specific rate limiting
            wait: Whether to wait if rate limit is exceeded
            timeout: Maximum time to wait
            
        Yields:
            None when rate limit slot is acquired
        """
        acquired = False
        start_time = time.time()
        
        try:
            if wait:
                # For sync waiting, we'll use a simple approach
                while not acquired:
                    acquired = self.acquire(url)
                    if not acquired:
                        if timeout is not None and (time.time() - start_time) >= timeout:
                            raise RateLimitError(
                                "Rate limit exceeded",
                                retry_after=self.get_wait_time(url),
                                limit=self.requests,
                                remaining=self.get_remaining(url)
                            )
                        time.sleep(0.1)
            else:
                acquired = self.acquire(url)
                if not acquired:
                    raise RateLimitError(
                        "Rate limit exceeded",
                        retry_after=self.get_wait_time(url),
                        limit=self.requests,
                        remaining=self.get_remaining(url)
                    )
            
            yield
            
        except Exception as e:
            self.stats['errors'] += 1
            raise e
    
    def get_wait_time(self, url: Optional[str] = None) -> float:
        """
        Get time to wait for next available slot.
        
        Args:
            url: URL for domain-specific rate limiting (optional)
            
        Returns:
            Time in seconds to wait
        """
        domain = self.extract_domain(url) if url else None
        limiter = self._get_limiter(domain)
        
        if self.algorithm == 'token_bucket':
            return limiter.wait_time()
        elif self.algorithm == 'leaky_bucket':
            return limiter.wait_time()
        elif self.algorithm == 'fixed_window':
            return limiter.wait_time()
        elif self.algorithm == 'sliding_window':
            return 0.1  # Sliding window doesn't have a simple wait time
        else:
            return 0.0
    
    def get_remaining(self, url: Optional[str] = None) -> int:
        """
        Get remaining requests allowed.
        
        Args:
            url: URL for domain-specific rate limiting (optional)
            
        Returns:
            Number of remaining requests
        """
        domain = self.extract_domain(url) if url else None
        limiter = self._get_limiter(domain)
        
        if self.algorithm == 'token_bucket':
            return limiter.available()
        elif self.algorithm == 'leaky_bucket':
            return max(0, self.requests - int(limiter.queue_size()))
        elif self.algorithm == 'fixed_window':
            return limiter.remaining()
        elif self.algorithm == 'sliding_window':
            return max(0, self.requests - limiter.count())
        else:
            return 0
    
    def get_status(self, url: Optional[str] = None) -> RateLimitStatus:
        """
        Get current rate limit status.
        
        Args:
            url: URL for domain-specific rate limiting (optional)
            
        Returns:
            RateLimitStatus object
        """
        domain = self.extract_domain(url) if url else None
        limiter = self._get_limiter(domain)
        
        if self.algorithm == 'token_bucket':
            return RateLimitStatus(
                allowed=self.requests,
                used=self.requests - limiter.available(),
                remaining=limiter.available(),
                reset_time=time.time() + (self.requests / self.burst_refill_rate),
                burst_tokens=limiter.available()
            )
        elif self.algorithm == 'leaky_bucket':
            return RateLimitStatus(
                allowed=self.requests,
                used=int(limiter.queue_size()),
                remaining=max(0, self.requests - int(limiter.queue_size())),
                reset_time=time.time() + (1.0 / (self.requests / self.period)),
                burst_tokens=0
            )
        elif self.algorithm == 'fixed_window':
            return RateLimitStatus(
                allowed=self.requests,
                used=self.requests - limiter.remaining(),
                remaining=limiter.remaining(),
                reset_time=limiter.reset_time(),
                burst_tokens=0
            )
        elif self.algorithm == 'sliding_window':
            return RateLimitStatus(
                allowed=self.requests,
                used=limiter.count(),
                remaining=max(0, self.requests - limiter.count()),
                reset_time=time.time() + self.period,
                burst_tokens=0
            )
        else:
            return RateLimitStatus()
    
    def reset(self, domain: Optional[str] = None) -> None:
        """
        Reset rate limiter.
        
        Args:
            domain: Specific domain to reset (None = reset all)
        """
        if domain:
            with self._lock:
                if domain in self._domain_limiters:
                    self._domain_limiters[domain].reset()
        else:
            # Reset global limiter
            if self._global_limiter:
                self._global_limiter.reset()
            
            # Reset all domain limiters
            with self._lock:
                for limiter in self._domain_limiters.values():
                    limiter.reset()
            
            # Reset statistics
            self.stats = {
                'allowed': 0,
                'blocked': 0,
                'waited': 0,
                'errors': 0,
            }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get rate limiter statistics"""
        return self.stats.copy()


class AdaptiveRateLimiter:
    """
    Adaptive rate limiter that adjusts limits based on system performance.
    """
    
    def __init__(self, initial_requests: int = 100, initial_period: float = 60.0,
                 min_requests: int = 10, max_requests: int = 1000,
                 adjustment_factor: float = 0.1, adjustment_interval: float = 10.0):
        """
        Initialize AdaptiveRateLimiter.
        
        Args:
            initial_requests: Initial number of requests allowed
            initial_period: Initial time period in seconds
            min_requests: Minimum requests per period
            max_requests: Maximum requests per period
            adjustment_factor: Factor to adjust limits by
            adjustment_interval: Time between adjustments in seconds
        """
        self.current_requests = initial_requests
        self.period = initial_period
        self.min_requests = min_requests
        self.max_requests = max_requests
        self.adjustment_factor = adjustment_factor
        self.adjustment_interval = adjustment_interval
        
        # Rate limiter instance
        self.rate_limiter = RateLimiter(
            requests=initial_requests,
            period=initial_period
        )
        
        # Performance tracking
        self.success_count = 0
        self.failure_count = 0
        self.last_adjustment_time = time.time()
        self._lock = threading.RLock()
        
        # Statistics
        self.stats = {
            'adjustments': 0,
            'increases': 0,
            'decreases': 0,
            'current_limit': initial_requests,
        }
    
    def record_success(self) -> None:
        """Record a successful request"""
        with self._lock:
            self.success_count += 1
            self._maybe_adjust()
    
    def record_failure(self) -> None:
        """Record a failed request"""
        with self._lock:
            self.failure_count += 1
            self._maybe_adjust()
    
    def _maybe_adjust(self):
        """Check if it's time to adjust limits"""
        now = time.time()
        if now - self.last_adjustment_time >= self.adjustment_interval:
            self._adjust_limits()
            self.last_adjustment_time = now
    
    def _adjust_limits(self):
        """Adjust rate limits based on performance"""
        total_requests = self.success_count + self.failure_count
        
        if total_requests == 0:
            return
        
        # Calculate success rate
        success_rate = self.success_count / total_requests
        
        # Adjust limits based on success rate
        if success_rate > 0.95:  # High success rate, increase limits
            new_limit = min(
                self.max_requests,
                int(self.current_requests * (1 + self.adjustment_factor))
            )
            if new_limit > self.current_requests:
                self.current_requests = new_limit
                self.stats['increases'] += 1
                self.stats['current_limit'] = new_limit
        elif success_rate < 0.7:  # Low success rate, decrease limits
            new_limit = max(
                self.min_requests,
                int(self.current_requests * (1 - self.adjustment_factor))
            )
            if new_limit < self.current_requests:
                self.current_requests = new_limit
                self.stats['decreases'] += 1
                self.stats['current_limit'] = new_limit
        
        # Update the rate limiter
        self.rate_limiter = RateLimiter(
            requests=self.current_requests,
            period=self.period
        )
        
        self.stats['adjustments'] += 1
        self.success_count = 0
        self.failure_count = 0
        
        _logger.info(f"Adjusted rate limit to {self.current_requests} requests/{self.period}s")
    
    def acquire(self, url: Optional[str] = None) -> bool:
        """Acquire a request slot"""
        return self.rate_limiter.acquire(url)
    
    async def acquire_async(self, url: Optional[str] = None) -> bool:
        """Async acquire a request slot"""
        return await self.rate_limiter.acquire_async(url)
    
    async def wait(self, url: Optional[str] = None, timeout: Optional[float] = None) -> bool:
        """Wait for a request slot"""
        return await self.rate_limiter.wait(url, timeout)
    
    @asynccontextmanager
    async def acquire_context(self, url: Optional[str] = None, wait: bool = True,
                             timeout: Optional[float] = None):
        """Context manager for rate limiting"""
        async with self.rate_limiter.acquire_context(url, wait, timeout) as ctx:
            yield ctx
    
    def get_current_limit(self) -> int:
        """Get current rate limit"""
        return self.current_requests
    
    def get_stats(self) -> Dict[str, Any]:
        """Get adaptive rate limiter statistics"""
        stats = self.stats.copy()
        stats.update({
            'success_count': self.success_count,
            'failure_count': self.failure_count,
            'success_rate': self.success_count / (self.success_count + self.failure_count) if (self.success_count + self.failure_count) > 0 else 0,
        })
        return stats


# Global rate limiter instance
_global_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get global rate limiter instance"""
    global _global_rate_limiter
    if _global_rate_limiter is None:
        config = get_settings().config.performance
        _global_rate_limiter = RateLimiter(
            requests=config.rate_limit_requests,
            period=config.rate_limit_period,
            per_domain=config.rate_limit_per_domain
        )
    return _global_rate_limiter


def reset_rate_limiter() -> None:
    """Reset global rate limiter"""
    global _global_rate_limiter
    if _global_rate_limiter:
        _global_rate_limiter.reset()
        _global_rate_limiter = None


# Convenience functions
async def rate_limit(url: Optional[str] = None, wait: bool = True, 
                    timeout: Optional[float] = None) -> bool:
    """Apply rate limiting to a request"""
    limiter = get_rate_limiter()
    
    if wait:
        return await limiter.wait(url, timeout)
    else:
        return await limiter.acquire_async(url)
