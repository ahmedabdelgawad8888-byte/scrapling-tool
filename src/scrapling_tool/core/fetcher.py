"""
Enhanced Fetcher Module
=======================
Advanced fetching capabilities with intelligent mode selection, fallback, and error handling.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union, AsyncIterator
from contextlib import asynccontextmanager, contextmanager

# Import Scrapling components
try:
    from scrapling import (
        Fetcher, AsyncFetcher, DynamicFetcher, StealthyFetcher,
        FetcherSession, AsyncFetcherSession, DynamicSession, AsyncDynamicSession,
        StealthySession, AsyncStealthySession
    )
    from scrapling.parser import Selector
    HAS_SCRAPLING = True
except ImportError:
    HAS_SCRAPLING = False
    Fetcher = None
    AsyncFetcher = None

from ..config.schemas import FetchMode, BrowserType
from ..config.settings import get_settings
from .cache import MultiLevelCache
from .proxy import SmartProxyRotator
from .rate_limiter import RateLimiter
from .retry import RetryStrategy, ExponentialBackoff

# Logger
_logger = logging.getLogger(__name__)


class FetchError(Exception):
    """Base exception for fetch errors"""
    
    def __init__(self, message: str, url: str = "", status_code: Optional[int] = None, 
                 mode: str = "unknown", retryable: bool = False):
        super().__init__(message)
        self.url = url
        self.status_code = status_code
        self.mode = mode
        self.retryable = retryable
    
    def __str__(self) -> str:
        parts = [f"FetchError: {super().__str__()}"]
        if self.url:
            parts.append(f"URL: {self.url}")
        if self.status_code:
            parts.append(f"Status: {self.status_code}")
        if self.mode:
            parts.append(f"Mode: {self.mode}")
        return " | ".join(parts)


class FetchTimeoutError(FetchError):
    """Timeout error for fetch operations"""
    pass


class FetchConnectionError(FetchError):
    """Connection error for fetch operations"""
    pass


class FetchRateLimitError(FetchError):
    """Rate limit error for fetch operations"""
    pass


class FetchProxyError(FetchError):
    """Proxy error for fetch operations"""
    pass


class FetchAuthenticationError(FetchError):
    """Authentication error for fetch operations"""
    pass


@dataclass
class FetchOptions:
    """Options for fetch operations"""
    mode: FetchMode = FetchMode.AUTO
    timeout: int = 30
    retries: int = 3
    retry_backoff: float = 1.5
    follow_redirects: bool = True
    verify_ssl: bool = True
    impersonate: str = "chrome"
    stealthy_headers: bool = True
    headless: bool = True
    browser_type: BrowserType = BrowserType.CHROMIUM
    use_proxy: bool = False
    proxy: Optional[str] = None
    user_agent: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    cookies: Dict[str, str] = field(default_factory=dict)
    body: Optional[bytes] = None
    form_data: Optional[Dict[str, Any]] = None
    json_data: Optional[Dict[str, Any]] = None
    method: str = "GET"
    
    # Browser-specific options
    disable_resources: bool = True
    network_idle: bool = True
    solve_cloudflare: bool = True
    
    # Caching options
    use_cache: bool = True
    cache_ttl: Optional[int] = None
    
    # Rate limiting
    respect_rate_limit: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'mode': self.mode.value if hasattr(self.mode, 'value') else self.mode,
            'timeout': self.timeout,
            'retries': self.retries,
            'retry_backoff': self.retry_backoff,
            'follow_redirects': self.follow_redirects,
            'verify_ssl': self.verify_ssl,
            'impersonate': self.impersonate,
            'stealthy_headers': self.stealthy_headers,
            'headless': self.headless,
            'browser_type': self.browser_type.value if hasattr(self.browser_type, 'value') else self.browser_type,
            'use_proxy': self.use_proxy,
            'proxy': self.proxy,
            'user_agent': self.user_agent,
            'headers': self.headers,
            'cookies': self.cookies,
            'method': self.method,
            'disable_resources': self.disable_resources,
            'network_idle': self.network_idle,
            'solve_cloudflare': self.solve_cloudflare,
            'use_cache': self.use_cache,
            'cache_ttl': self.cache_ttl,
            'respect_rate_limit': self.respect_rate_limit,
        }


@dataclass
class FetchResult:
    """Result of a fetch operation"""
    url: str
    content: bytes
    text: str
    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)
    cookies: Dict[str, str] = field(default_factory=dict)
    timing: Dict[str, float] = field(default_factory=dict)
    mode: str = "unknown"
    selector: Optional[Selector] = None
    from_cache: bool = False
    proxy_used: Optional[str] = None
    retry_count: int = 0
    error: Optional[str] = None
    
    def __post_init__(self):
        # Ensure text is decoded from content if not provided
        if self.content and not self.text:
            try:
                self.text = self.content.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    self.text = self.content.decode('latin-1')
                except UnicodeDecodeError:
                    self.text = str(self.content)
        
        # Parse content to Selector if not provided
        if not self.selector and self.text:
            try:
                self.selector = Selector(self.text)
            except Exception:
                self.selector = None
    
    def get_selector(self) -> Optional[Selector]:
        """Get Selector instance for parsed content"""
        if not self.selector and self.text:
            try:
                self.selector = Selector(self.text)
            except Exception:
                pass
        return self.selector
    
    def css(self, selector: str) -> List[Any]:
        """Apply CSS selector to content"""
        sel = self.get_selector()
        if sel:
            return sel.css(selector)
        return []
    
    def xpath(self, selector: str) -> List[Any]:
        """Apply XPath selector to content"""
        sel = self.get_selector()
        if sel:
            return sel.xpath(selector)
        return []
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'url': self.url,
            'status_code': self.status_code,
            'headers': self.headers,
            'cookies': self.cookies,
            'timing': self.timing,
            'mode': self.mode,
            'from_cache': self.from_cache,
            'proxy_used': self.proxy_used,
            'retry_count': self.retry_count,
            'error': self.error,
            'content_length': len(self.content) if self.content else 0,
            'text_length': len(self.text) if self.text else 0,
        }


class ModeDetector:
    """
    Intelligent mode detection for fetching based on URL and requirements.
    """
    
    def __init__(self):
        # Compile regex patterns for known anti-bot systems
        self.cloudflare_patterns = [
            re.compile(r'cloudflare\.com'),
            re.compile(r'turnstile\.cloudflare\.com'),
            re.compile(r'cf-ray[\s-]?'),
            re.compile(r'/cdn-cgi/'),
        ]
        
        self.anti_bot_patterns = [
            re.compile(r'bot[\s-]?detection', re.I),
            re.compile(r'anti[\s-]?bot', re.I),
            re.compile(r'security[\s-]?check', re.I),
            re.compile(r'verify[\s-]?you[\s-]?are[\s-]?human', re.I),
            re.compile(r'capcha', re.I),
        ]
        
        # Platform-specific patterns that may need browser
        self.browser_required_patterns = [
            re.compile(r'(tiktok|douyin)\.com'),
            re.compile(r'instagram\.com'),
            re.compile(r'snapchat\.com'),
            re.compile(r'twitter\.com|x\.com'),
            re.compile(r'facebook\.com'),
            re.compile(r'youtube\.com'),
        ]
        
        # Patterns that can use HTTP
        self.http_ok_patterns = [
            re.compile(r'api\.', re.I),
            re.compile(r'\.json$', re.I),
            re.compile(r'\.xml$', re.I),
            re.compile(r'graphql', re.I),
            re.compile(r'rest\.', re.I),
        ]
    
    def detect_required_mode(self, url: str, hints: Optional[Dict[str, Any]] = None) -> FetchMode:
        """
        Detect the required fetch mode for a given URL.
        
        Args:
            url: URL to analyze
            hints: Additional hints about requirements
            
        Returns:
            Recommended FetchMode
        """
        hints = hints or {}
        parsed = urllib.parse.urlparse(url)
        
        # If mode is explicitly specified in hints, use it
        if 'mode' in hints:
            mode = hints['mode']
            if isinstance(mode, str):
                try:
                    return FetchMode[mode.upper()]
                except KeyError:
                    pass
            elif isinstance(mode, FetchMode):
                return mode
        
        # Check if JavaScript rendering is required
        if hints.get('javascript', False) or hints.get('render_js', False):
            return FetchMode.BROWSER
        
        # Check for anti-bot patterns in URL
        url_lower = url.lower()
        for pattern in self.cloudflare_patterns + self.anti_bot_patterns:
            if pattern.search(url_lower):
                return FetchMode.STEALTH
        
        # Check for browser-required platforms
        for pattern in self.browser_required_patterns:
            if pattern.search(url_lower):
                # These platforms often have anti-bot measures
                return FetchMode.STEALTH
        
        # Check if HTTP might be sufficient
        for pattern in self.http_ok_patterns:
            if pattern.search(url_lower):
                return FetchMode.HTTP
        
        # Default to AUTO or SESSION based on configuration
        return FetchMode.AUTO
    
    def get_mode_priority(self, url: str) -> List[FetchMode]:
        """
        Get prioritized list of modes to try for a URL.
        
        Args:
            url: URL to analyze
            
        Returns:
            List of FetchMode in priority order
        """
        required_mode = self.detect_required_mode(url)
        
        if required_mode == FetchMode.AUTO:
            # Default priority: HTTP -> Session -> Browser -> Stealth
            return [
                FetchMode.HTTP,
                FetchMode.SESSION, 
                FetchMode.BROWSER,
                FetchMode.STEALTH
            ]
        else:
            # Start with required mode, then fallback to others
            modes = [m for m in FetchMode if m != FetchMode.AUTO]
            if required_mode in modes:
                modes.remove(required_mode)
                return [required_mode] + modes
            return modes


class FetcherFactory:
    """
    Factory for creating fetcher instances based on mode.
    """
    
    def __init__(self, config=None):
        self.config = config or get_settings().config
        self._fetcher_cache: Dict[str, Any] = {}
    
    def create_fetcher(self, mode: FetchMode) -> Any:
        """
        Create a fetcher instance for the specified mode.
        
        Args:
            mode: FetchMode to create fetcher for
            
        Returns:
            Fetcher instance
        """
        cache_key = f"{mode.value}_fetcher"
        
        if cache_key in self._fetcher_cache:
            return self._fetcher_cache[cache_key]
        
        # Get configuration for this mode
        http_config = self.config.http
        browser_config = self.config.browser
        proxy_config = self.config.proxy
        
        try:
            if mode == FetchMode.HTTP:
                fetcher = AsyncFetcher(
                    timeout=http_config.timeout,
                    follow_redirects=http_config.follow_redirects,
                    verify_ssl=http_config.verify_ssl,
                    impersonate=http_config.impersonate,
                )
            
            elif mode == FetchMode.SESSION:
                fetcher = AsyncFetcherSession(
                    timeout=http_config.timeout,
                    follow_redirects=http_config.follow_redirects,
                    verify_ssl=http_config.verify_ssl,
                    impersonate=http_config.impersonate,
                )
            
            elif mode == FetchMode.BROWSER:
                fetcher = AsyncDynamicSession(
                    headless=browser_config.headless,
                    browser_type=browser_config.browser_type.value,
                    timeout=browser_config.timeout,
                    disable_resources=browser_config.disable_resources,
                    network_idle=browser_config.network_idle,
                )
            
            elif mode == FetchMode.STEALTH:
                fetcher = AsyncStealthySession(
                    headless=browser_config.headless,
                    browser_type=browser_config.browser_type.value,
                    timeout=browser_config.timeout,
                    solve_cloudflare=browser_config.solve_cloudflare,
                )
            
            else:
                # Default to HTTP
                fetcher = AsyncFetcher()
            
            self._fetcher_cache[cache_key] = fetcher
            return fetcher
            
        except Exception as e:
            _logger.error(f"Failed to create {mode.value} fetcher: {e}")
            # Return a basic fetcher as fallback
            return AsyncFetcher()
    
    def get_available_modes(self) -> List[FetchMode]:
        """Get list of available fetch modes"""
        return list(FetchMode)
    
    def clear_cache(self):
        """Clear fetcher cache"""
        self._fetcher_cache.clear()


class EnhancedFetcher:
    """
    Enhanced fetcher with intelligent mode selection, caching, and error handling.
    """
    
    def __init__(self, config=None, factory: Optional[FetcherFactory] = None):
        """
        Initialize EnhancedFetcher.
        
        Args:
            config: Configuration object
            factory: FetcherFactory instance
        """
        self.config = config or get_settings().config
        self.factory = factory or FetcherFactory(self.config)
        self.mode_detector = ModeDetector()
        self.cache = MultiLevelCache(self.config.cache) if self.config.cache.enabled else None
        self.proxy_rotator = SmartProxyRotator(self.config.proxy) if self.config.proxy.enabled else None
        self.rate_limiter = RateLimiter(
            requests=self.config.performance.rate_limit_requests,
            period=self.config.performance.rate_limit_period
        ) if self.config.performance.rate_limit_enabled else None
        self.retry_strategy = ExponentialBackoff(
            max_retries=self.config.http.max_retries,
            base_delay=self.config.http.retry_backoff
        )
        
        # Statistics
        self.stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'cached_requests': 0,
            'retry_count': 0,
            'mode_usage': {mode.value: 0 for mode in FetchMode},
        }
        
        # Session management
        self._sessions: Dict[str, Any] = {}
        self._session_lock = asyncio.Lock()
    
    def _generate_cache_key(self, url: str, options: FetchOptions) -> str:
        """Generate cache key for a request"""
        key_data = f"{url}|{options.mode.value}|{options.method}"
        if options.body:
            key_data += f"|body:{hashlib.md5(options.body).hexdigest()}"
        if options.form_data:
            key_data += f"|form:{hashlib.md5(str(options.form_data).encode()).hexdigest()}"
        if options.json_data:
            key_data += f"|json:{hashlib.md5(str(options.json_data).encode()).hexdigest()}"
        
        return hashlib.sha256(key_data.encode()).hexdigest()
    
    def _get_fetcher_for_mode(self, mode: FetchMode) -> Any:
        """Get fetcher for specified mode"""
        return self.factory.create_fetcher(mode)
    
    async def _apply_proxy(self, fetcher: Any, options: FetchOptions) -> Any:
        """Apply proxy settings to fetcher"""
        if not options.use_proxy or not self.proxy_rotator:
            return fetcher
        
        proxy = options.proxy or self.proxy_rotator.get_proxy()
        if proxy:
            # Apply proxy to fetcher
            if hasattr(fetcher, 'proxy'):
                fetcher.proxy = proxy
            elif hasattr(fetcher, '_session'):
                # For session-based fetchers
                if hasattr(fetcher._session, 'proxy'):
                    fetcher._session.proxy = proxy
        
        return fetcher
    
    async def _apply_headers(self, fetcher: Any, options: FetchOptions) -> Any:
        """Apply headers to fetcher"""
        headers = options.headers.copy()
        
        # Add default headers
        default_headers = self.config.http.default_headers
        for key, value in default_headers.items():
            if key.upper() not in [h.upper() for h in headers.keys()]:
                headers[key] = value
        
        # Add user agent if specified
        if options.user_agent:
            headers['User-Agent'] = options.user_agent
        
        # Apply headers to fetcher
        if hasattr(fetcher, 'headers'):
            fetcher.headers.update(headers)
        elif hasattr(fetcher, '_session') and hasattr(fetcher._session, 'headers'):
            fetcher._session.headers.update(headers)
        
        return fetcher
    
    async def fetch(self, url: str, options: Optional[FetchOptions] = None) -> FetchResult:
        """
        Fetch a URL with enhanced capabilities.
        
        Args:
            url: URL to fetch
            options: Fetch options (defaults to FetchOptions)
            
        Returns:
            FetchResult with response data
            
        Raises:
            FetchError: If fetch fails after all retries
        """
        # Normalize URL
        url = self._normalize_url(url)
        
        # Use default options if not provided
        options = options or FetchOptions()
        
        # Check cache first
        if options.use_cache and self.cache:
            cache_key = self._generate_cache_key(url, options)
            cached_result = self.cache.get(cache_key)
            if cached_result:
                self.stats['cached_requests'] += 1
                return FetchResult(
                    url=url,
                    content=cached_result.get('content', b''),
                    text=cached_result.get('text', ''),
                    status_code=cached_result.get('status_code', 200),
                    headers=cached_result.get('headers', {}),
                    from_cache=True,
                    mode=options.mode.value if hasattr(options.mode, 'value') else str(options.mode),
                    timing={'cached': True},
                )
        
        # Update statistics
        self.stats['total_requests'] += 1
        
        # Determine mode
        mode = options.mode
        if mode == FetchMode.AUTO:
            mode = self.mode_detector.detect_required_mode(url)
        
        # Get prioritized modes to try
        modes_to_try = self.mode_detector.get_mode_priority(url)
        if mode != FetchMode.AUTO and mode in modes_to_try:
            # Move requested mode to front
            modes_to_try.remove(mode)
            modes_to_try.insert(0, mode)
        
        # Try each mode
        last_error = None
        for try_mode in modes_to_try:
            try:
                result = await self._fetch_with_mode(url, options, try_mode)
                
                # Cache successful result if caching is enabled
                if options.use_cache and self.cache and result.status_code < 400:
                    cache_key = self._generate_cache_key(url, options)
                    cache_data = {
                        'content': result.content,
                        'text': result.text,
                        'status_code': result.status_code,
                        'headers': result.headers,
                        'timestamp': time.time(),
                        'ttl': options.cache_ttl or self.config.cache.cache_ttl,
                    }
                    self.cache.set(cache_key, cache_data)
                
                # Update statistics
                self.stats['successful_requests'] += 1
                mode_key = try_mode.value if hasattr(try_mode, 'value') else str(try_mode)
                self.stats['mode_usage'][mode_key] = self.stats['mode_usage'].get(mode_key, 0) + 1
                
                return result
                
            except FetchError as e:
                last_error = e
                e.retryable = True  # Mark as retryable for mode switching
                _logger.debug(f"Mode {try_mode.value} failed for {url}: {e}")
                continue
            except Exception as e:
                last_error = FetchError(str(e), url=url, mode=try_mode.value)
                _logger.error(f"Unexpected error with mode {try_mode.value}: {e}")
                continue
        
        # All modes failed
        self.stats['failed_requests'] += 1
        error_msg = f"All fetch modes failed for {url}"
        if last_error:
            error_msg += f": {last_error}"
        
        raise FetchError(error_msg, url=url, mode="all", retryable=True)
    
    async def _fetch_with_mode(self, url: str, options: FetchOptions, mode: FetchMode) -> FetchResult:
        """
        Fetch URL with specific mode.
        
        Args:
            url: URL to fetch
            options: Fetch options
            mode: FetchMode to use
            
        Returns:
            FetchResult
            
        Raises:
            FetchError: If fetch fails
        """
        start_time = time.time()
        
        # Update mode in options
        options.mode = mode
        
        # Get fetcher for this mode
        fetcher = self._get_fetcher_for_mode(mode)
        
        # Apply proxy
        fetcher = await self._apply_proxy(fetcher, options)
        
        # Apply headers
        fetcher = await self._apply_headers(fetcher, options)
        
        # Apply rate limiting
        if self.rate_limiter and options.respect_rate_limit:
            await self.rate_limiter.acquire()
        
        # Prepare fetch arguments
        fetch_args = self._prepare_fetch_args(url, options, mode)
        
        try:
            # Execute fetch with retry
            result = await self._fetch_with_retry(fetcher, fetch_args, options, mode, start_time)
            return result
            
        except Exception as e:
            error_type = type(e).__name__
            if 'timeout' in error_type.lower():
                raise FetchTimeoutError(str(e), url=url, mode=mode.value, retryable=True)
            elif 'connection' in error_type.lower():
                raise FetchConnectionError(str(e), url=url, mode=mode.value, retryable=True)
            elif 'rate' in error_type.lower() or '429' in str(e):
                raise FetchRateLimitError(str(e), url=url, mode=mode.value, retryable=True)
            elif 'proxy' in error_type.lower():
                raise FetchProxyError(str(e), url=url, mode=mode.value, retryable=True)
            elif '401' in str(e) or '403' in str(e):
                raise FetchAuthenticationError(str(e), url=url, mode=mode.value, retryable=False)
            else:
                raise FetchError(str(e), url=url, mode=mode.value, retryable=True)
    
    async def _fetch_with_retry(self, fetcher: Any, fetch_args: Dict[str, Any], 
                               options: FetchOptions, mode: FetchMode, start_time: float) -> FetchResult:
        """
        Execute fetch with retry logic.
        """
        last_exception = None
        retry_count = 0
        
        for attempt in range(options.retries + 1):
            try:
                result = await self._execute_fetch(fetcher, fetch_args, options, mode, start_time)
                return result
                
            except Exception as e:
                last_exception = e
                retry_count += 1
                
                # Check if retryable
                if not (isinstance(e, FetchError) and e.retryable) and attempt < options.retries:
                    continue
                
                # Wait before retry with exponential backoff
                if attempt < options.retries:
                    delay = self.retry_strategy.get_delay(attempt, e)
                    _logger.debug(f"Retry {attempt + 1}/{options.retries} for {fetch_args.get('url', 'unknown')}, waiting {delay:.2f}s")
                    await asyncio.sleep(delay)
                    
                    # Update statistics
                    self.stats['retry_count'] += 1
        
        # All retries exhausted
        if isinstance(last_exception, FetchError):
            raise last_exception
        raise FetchError(str(last_exception), url=fetch_args.get('url', ''), 
                        mode=mode.value, retryable=False)
    
    async def _execute_fetch(self, fetcher: Any, fetch_args: Dict[str, Any], 
                           options: FetchOptions, mode: FetchMode, start_time: float) -> FetchResult:
        """
        Execute the actual fetch operation.
        """
        url = fetch_args.get('url', '')
        
        try:
            # Different fetch methods based on mode
            if mode in [FetchMode.HTTP, FetchMode.SESSION]:
                # Use HTTP-based fetchers
                if options.method.upper() == 'GET':
                    response = await fetcher.get(**fetch_args)
                elif options.method.upper() == 'POST':
                    response = await fetcher.post(**fetch_args)
                else:
                    # Use general fetch method
                    response = await fetcher.fetch(**fetch_args)
                    
            else:
                # Use browser-based fetchers
                if options.method.upper() == 'GET':
                    response = await fetcher.fetch(url, **fetch_args)
                else:
                    response = await fetcher.fetch(url, method=options.method, **fetch_args)
            
            # Process response
            return await self._process_response(response, url, mode, options, start_time)
            
        except Exception as e:
            # Handle Playwright-specific errors
            error_str = str(e).lower()
            if 'timeout' in error_str:
                raise FetchTimeoutError(str(e), url=url, mode=mode.value)
            elif 'page crashed' in error_str or 'browser closed' in error_str:
                raise FetchError(f"Browser error: {e}", url=url, mode=mode.value)
            else:
                raise
    
    def _prepare_fetch_args(self, url: str, options: FetchOptions, mode: FetchMode) -> Dict[str, Any]:
        """
        Prepare fetch arguments based on mode and options.
        """
        args = {
            'url': url,
            'timeout': options.timeout,
            'follow_redirects': options.follow_redirects,
            'verify_ssl': options.verify_ssl,
        }
        
        # Add mode-specific arguments
        if mode == FetchMode.HTTP:
            args.update({
                'impersonate': options.impersonate,
                'stealthy_headers': options.stealthy_headers,
            })
        
        elif mode == FetchMode.SESSION:
            args.update({
                'impersonate': options.impersonate,
                'stealthy_headers': options.stealthy_headers,
            })
        
        elif mode == FetchMode.BROWSER:
            args.update({
                'headless': options.headless,
                'disable_resources': options.disable_resources,
                'network_idle': options.network_idle,
            })
        
        elif mode == FetchMode.STEALTH:
            args.update({
                'headless': options.headless,
                'solve_cloudflare': options.solve_cloudflare,
                'disable_resources': options.disable_resources,
                'network_idle': options.network_idle,
            })
        
        # Add request-specific arguments
        if options.headers:
            args['headers'] = options.headers
        if options.cookies:
            args['cookies'] = options.cookies
        if options.method.upper() != 'GET':
            args['method'] = options.method.upper()
            if options.body:
                args['content'] = options.body
            elif options.form_data:
                args['data'] = options.form_data
            elif options.json_data:
                args['json'] = options.json_data
        
        return args
    
    async def _process_response(self, response: Any, url: str, mode: FetchMode, 
                              options: FetchOptions, start_time: float) -> FetchResult:
        """
        Process fetcher response into FetchResult.
        """
        end_time = time.time()
        
        # Extract response data
        if hasattr(response, 'content'):
            content = response.content
        elif hasattr(response, 'body'):
            content = response.body
        else:
            content = b''
        
        if hasattr(response, 'text'):
            text = response.text
        else:
            try:
                text = content.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    text = content.decode('latin-1')
                except UnicodeDecodeError:
                    text = str(content)
        
        # Extract status code
        if hasattr(response, 'status_code'):
            status_code = response.status_code
        elif hasattr(response, 'status'):
            status_code = response.status
        else:
            status_code = 200
        
        # Extract headers
        headers = {}
        if hasattr(response, 'headers'):
            if hasattr(response.headers, 'items'):
                headers = dict(response.headers.items())
            elif isinstance(response.headers, dict):
                headers = response.headers.copy()
        
        # Extract cookies
        cookies = {}
        if hasattr(response, 'cookies'):
            if hasattr(response.cookies, 'items'):
                cookies = dict(response.cookies.items())
            elif isinstance(response.cookies, dict):
                cookies = response.cookies.copy()
        
        # Calculate timing
        timing = {
            'total': end_time - start_time,
            'fetch': end_time - start_time,
        }
        
        # Create result
        result = FetchResult(
            url=url,
            content=content,
            text=text,
            status_code=status_code,
            headers=headers,
            cookies=cookies,
            timing=timing,
            mode=mode.value,
            retry_count=0,  # Will be updated by retry logic
        )
        
        return result
    
    def _normalize_url(self, url: str) -> str:
        """Normalize URL by removing fragments, etc."""
        parsed = urllib.parse.urlparse(url)
        
        # Remove fragment
        url = urllib.parse.urlunparse(parsed._replace(fragment=''))
        
        # Ensure scheme
        if not parsed.scheme:
            url = f'https://{url.lstrip("/")}'
        
        return url
    
    async def batch_fetch(self, urls: List[str], options: Optional[FetchOptions] = None, 
                        concurrency: Optional[int] = None) -> AsyncIterator[FetchResult]:
        """
        Fetch multiple URLs concurrently.
        
        Args:
            urls: List of URLs to fetch
            options: Fetch options (applied to all URLs)
            concurrency: Maximum concurrent requests (defaults to config)
            
        Yields:
            FetchResult for each URL
        """
        options = options or FetchOptions()
        concurrency = concurrency or self.config.performance.concurrent_requests
        
        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(concurrency)
        
        async def fetch_url(url: str) -> FetchResult:
            async with semaphore:
                try:
                    result = await self.fetch(url, options)
                    return result
                except Exception as e:
                    _logger.error(f"Error fetching {url}: {e}")
                    return FetchResult(
                        url=url,
                        content=b'',
                        text='',
                        status_code=0,
                        error=str(e),
                        mode=options.mode.value if hasattr(options.mode, 'value') else str(options.mode),
                    )
        
        # Create tasks
        tasks = [fetch_url(url) for url in urls]
        
        # Execute and yield results as they complete
        for future in asyncio.as_completed(tasks):
            try:
                result = await future
                yield result
            except Exception as e:
                _logger.error(f"Error in batch fetch: {e}")
                continue
    
    async def stream_fetch(self, urls: List[str], options: Optional[FetchOptions] = None,
                          concurrency: Optional[int] = None) -> AsyncIterator[FetchResult]:
        """
        Stream fetch results as they become available (same as batch_fetch).
        """
        async for result in self.batch_fetch(urls, options, concurrency):
            yield result
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics"""
        return self.stats.copy()
    
    def reset_stats(self) -> None:
        """Reset statistics"""
        self.stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'cached_requests': 0,
            'retry_count': 0,
            'mode_usage': {mode.value: 0 for mode in FetchMode},
        }
    
    async def close(self) -> None:
        """Clean up resources"""
        # Close all sessions
        async with self._session_lock:
            for session_name, session in self._sessions.items():
                try:
                    if hasattr(session, 'close'):
                        await session.close()
                    elif hasattr(session, 'aclose'):
                        await session.aclose()
                except Exception as e:
                    _logger.warning(f"Error closing session {session_name}: {e}")
            self._sessions.clear()
        
        # Clear caches
        if self.cache:
            await self.cache.clear()
        
        # Close fetcher factory
        if hasattr(self.factory, 'clear_cache'):
            self.factory.clear_cache()


class FetcherPool:
    """
    Pool of EnhancedFetcher instances for concurrent operations.
    """
    
    def __init__(self, size: int = 5, config=None):
        """
        Initialize FetcherPool.
        
        Args:
            size: Number of fetcher instances in pool
            config: Configuration object
        """
        self.size = size
        self.config = config or get_settings().config
        self._pool: List[EnhancedFetcher] = []
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=size)
        self._lock = asyncio.Lock()
        
        # Initialize pool
        for _ in range(size):
            fetcher = EnhancedFetcher(self.config)
            self._pool.append(fetcher)
            self._queue.put_nowait(fetcher)
    
    async def acquire(self) -> EnhancedFetcher:
        """Acquire a fetcher from the pool"""
        return await self._queue.get()
    
    async def release(self, fetcher: EnhancedFetcher) -> None:
        """Release a fetcher back to the pool"""
        await self._queue.put(fetcher)
    
    @asynccontextmanager
    async def get_fetcher(self) -> EnhancedFetcher:
        """Context manager for getting a fetcher"""
        fetcher = await self.acquire()
        try:
            yield fetcher
        finally:
            await self.release(fetcher)
    
    async def fetch(self, url: str, options: Optional[FetchOptions] = None) -> FetchResult:
        """
        Fetch a URL using a fetcher from the pool.
        
        Args:
            url: URL to fetch
            options: Fetch options
            
        Returns:
            FetchResult
        """
        async with self.get_fetcher() as fetcher:
            return await fetcher.fetch(url, options)
    
    async def close(self) -> None:
        """Close all fetchers in the pool"""
        async with self._lock:
            for fetcher in self._pool:
                try:
                    await fetcher.close()
                except Exception as e:
                    _logger.warning(f"Error closing fetcher: {e}")
            self._pool.clear()
            # Clear the queue
            while not self._queue.empty():
                try:
                    await self._queue.get()
                except:
                    break


# Global fetcher pool instance
_fetcher_pool: Optional[FetcherPool] = None


def get_fetcher_pool(size: Optional[int] = None) -> FetcherPool:
    """Get global fetcher pool instance"""
    global _fetcher_pool
    if _fetcher_pool is None:
        config = get_settings().config
        pool_size = size or config.performance.concurrent_requests
        _fetcher_pool = FetcherPool(size=pool_size, config=config)
    return _fetcher_pool


def reset_fetcher_pool() -> None:
    """Reset global fetcher pool"""
    global _fetcher_pool
    if _fetcher_pool:
        # Note: This doesn't actually close the pool in async context
        _fetcher_pool = None


# Convenience functions
def create_enhanced_fetcher(config=None) -> EnhancedFetcher:
    """Create a new EnhancedFetcher instance"""
    return EnhancedFetcher(config)


async def fetch_url(url: str, options: Optional[FetchOptions] = None) -> FetchResult:
    """Fetch a URL with default configuration"""
    fetcher = create_enhanced_fetcher()
    try:
        return await fetcher.fetch(url, options)
    finally:
        await fetcher.close()
