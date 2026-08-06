"""
Core Module for Scrapper Magic
==============================
Core functionality including enhanced fetcher, caching, proxy rotation, and utilities.
"""

from .fetcher import EnhancedFetcher, FetcherPool, FetchResult, FetchError
from .proxy import SmartProxyRotator, ProxyManager, ProxyHealthChecker
from .cache import MultiLevelCache, DiskCache, MemoryCache, CacheKey
from .session import SessionManager, SessionPool, BrowserSession
from .rate_limiter import RateLimiter, AdaptiveRateLimiter
from .retry import RetryStrategy, ExponentialBackoff, LinearBackoff

__all__ = [
    # Fetcher
    "EnhancedFetcher",
    "FetcherPool", 
    "FetchResult",
    "FetchError",
    
    # Proxy
    "SmartProxyRotator",
    "ProxyManager",
    "ProxyHealthChecker",
    
    # Cache
    "MultiLevelCache",
    "DiskCache",
    "MemoryCache",
    "CacheKey",
    
    # Session
    "SessionManager",
    "SessionPool",
    "BrowserSession",
    
    # Rate Limiter
    "RateLimiter",
    "AdaptiveRateLimiter",
    
    # Retry
    "RetryStrategy",
    "ExponentialBackoff",
    "LinearBackoff",
]

# Convenience imports
from .utils import (
    clean_url,
    normalize_url,
    extract_domain,
    detect_platform,
    sanitize_content,
    get_user_agent,
    parse_headers,
)

__all__.extend([
    "clean_url",
    "normalize_url", 
    "extract_domain",
    "detect_platform",
    "sanitize_content",
    "get_user_agent",
    "parse_headers",
])