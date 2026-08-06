#!/usr/bin/env python3
"""
Test script to verify the created modules can be imported and work correctly.
"""

import sys
import os

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

print("Testing module imports...")

try:
    # Test configuration modules
    print("1. Testing config module...")
    from config.schemas import RetryConfig, Configuration
    from config.settings import get_settings
    from config.validators import validate_config
    print("   ✓ Config module imported successfully")
    
    # Test retry module
    print("2. Testing retry module...")
    from src.scrapling_tool.core.retry import (
        RetryStrategy, ExponentialBackoff, LinearBackoff, ConstantBackoff,
        RetryStrategyType, RetryCondition, create_retry_strategy,
        retryable, with_exponential_backoff
    )
    print("   ✓ Retry module imported successfully")
    
    # Test session module
    print("3. Testing session module...")
    from src.scrapling_tool.core.session import (
        SessionManager, SessionPool, BaseSession, HTTPSession,
        SessionType, SessionState, get_session_manager
    )
    print("   ✓ Session module imported successfully")
    
    # Test proxy module
    print("4. Testing proxy module...")
    from src.scrapling_tool.core.proxy import (
        SmartProxyRotator, ProxyManager, ProxyInfo,
        ProxyStatus, ProxyProtocol, ProxyType, get_proxy_manager
    )
    print("   ✓ Proxy module imported successfully")
    
    # Test cache module
    print("5. Testing cache module...")
    from src.scrapling_tool.core.cache import (
        MultiLevelCache, MemoryCache, DiskCache, CacheKey
    )
    print("   ✓ Cache module imported successfully")
    
    # Test rate limiter module
    print("6. Testing rate limiter module...")
    from src.scrapling_tool.core.rate_limiter import (
        RateLimiter, AdaptiveRateLimiter
    )
    print("   ✓ Rate limiter module imported successfully")
    
    # Test utils module
    print("7. Testing utils module...")
    from src.scrapling_tool.core.utils import (
        clean_url, normalize_url, extract_domain, detect_platform,
        sanitize_content, get_user_agent, parse_headers
    )
    print("   ✓ Utils module imported successfully")
    
    # Test fetcher module
    print("8. Testing fetcher module...")
    from src.scrapling_tool.core.fetcher import (
        EnhancedFetcher, FetcherPool, FetchResult, FetchError
    )
    print("   ✓ Fetcher module imported successfully")
    
    # Test core module imports
    print("9. Testing core module...")
    from src.scrapling_tool.core import (
        EnhancedFetcher, FetcherPool, FetchResult, FetchError,
        SmartProxyRotator, ProxyManager, ProxyHealthChecker,
        MultiLevelCache, DiskCache, MemoryCache, CacheKey,
        SessionManager, SessionPool, BrowserSession,
        RateLimiter, AdaptiveRateLimiter,
        RetryStrategy, ExponentialBackoff, LinearBackoff
    )
    print("   ✓ Core module imported successfully")
    
    # Test actual functionality
    print("10. Testing retry strategy creation...")
    retry_strategy = with_exponential_backoff(max_retries=3, base_delay=1.0)
    print(f"    Created: {retry_strategy}")
    print("   ✓ Retry strategy creation works")
    
    print("11. Testing proxy info creation...")
    proxy_info = ProxyInfo(url="http://proxy.example.com:8080")
    print(f"    Created: {proxy_info}")
    print("   ✓ Proxy info creation works")
    
    print("12. Testing configuration...")
    config = Configuration()
    print(f"    Config has retry: {hasattr(config, 'retry')}")
    print(f"    Retry config type: {type(config.retry)}")
    print("   ✓ Configuration works")
    
    print("\n🎉 All tests passed! Modules are working correctly.")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)