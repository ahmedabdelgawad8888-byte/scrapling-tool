"""
Session Management Module
=======================
Advanced session management for HTTP and browser sessions with pooling and reuse.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
import weakref
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from contextlib import asynccontextmanager, contextmanager
from collections import defaultdict

from ..config.schemas import BrowserType, FetchMode
from ..config.settings import get_settings

# Logger
_logger = logging.getLogger(__name__)


class SessionType(Enum):
    """Types of sessions"""
    HTTP = "http"
    BROWSER = "browser"
    STEALTH = "stealth"
    DYNAMIC = "dynamic"


class SessionState(Enum):
    """Session states"""
    IDLE = "idle"
    BUSY = "busy"
    RESERVED = "reserved"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass
class SessionConfig:
    """Configuration for a session"""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    session_type: SessionType = SessionType.HTTP
    timeout: int = 30
    max_age: int = 3600  # Maximum session age in seconds
    reusable: bool = True
    
    # Browser-specific settings
    browser_type: Optional[BrowserType] = None
    headless: bool = True
    user_agent: Optional[str] = None
    viewport_width: int = 1280
    viewport_height: int = 720
    
    # HTTP-specific settings
    default_headers: Dict[str, str] = field(default_factory=dict)
    verify_ssl: bool = True
    follow_redirects: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        result = {}
        for key, value in self.__dict__.items():
            if hasattr(value, 'to_dict'):
                result[key] = value.to_dict()
            elif isinstance(value, (dict, list, str, int, float, bool)):
                result[key] = value
            else:
                result[key] = str(value)
        return result


@dataclass
class SessionInfo:
    """Information about a session"""
    session_id: str
    session_type: SessionType
    state: SessionState = SessionState.IDLE
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    usage_count: int = 0
    error_count: int = 0
    success_count: int = 0
    average_response_time: float = 0.0
    
    def __post_init__(self):
        if isinstance(self.created_at, float) and self.created_at == 0.0:
            self.created_at = time.time()
        if isinstance(self.last_used, float) and self.last_used == 0.0:
            self.last_used = time.time()
    
    def update_stats(self, success: bool, response_time: float) -> None:
        """Update session statistics"""
        self.last_used = time.time()
        self.usage_count += 1
        
        if success:
            self.success_count += 1
        else:
            self.error_count += 1
        
        # Update average response time using exponential moving average
        if self.average_response_time == 0.0:
            self.average_response_time = response_time
        else:
            alpha = 0.2  # Smoothing factor
            self.average_response_time = (alpha * response_time + 
                                         (1 - alpha) * self.average_response_time)
    
    def get_age(self) -> float:
        """Get session age in seconds"""
        return time.time() - self.created_at
    
    def get_idle_time(self) -> float:
        """Get idle time in seconds"""
        return time.time() - self.last_used
    
    def is_expired(self, max_age: int = 3600) -> bool:
        """Check if session has expired"""
        return self.get_age() > max_age
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'session_id': self.session_id,
            'session_type': self.session_type.value,
            'state': self.state.value,
            'created_at': self.created_at,
            'last_used': self.last_used,
            'usage_count': self.usage_count,
            'error_count': self.error_count,
            'success_count': self.success_count,
            'average_response_time': self.average_response_time,
            'age': self.get_age(),
            'idle_time': self.get_idle_time()
        }


class SessionError(Exception):
    """Base session exception"""
    pass


class SessionPoolExhausted(SessionError):
    """Raised when all sessions in pool are busy"""
    pass


class SessionExpired(SessionError):
    """Raised when a session has expired"""
    pass


class SessionNotAvailable(SessionError):
    """Raised when requested session type is not available"""
    pass


class BaseSession:
    """Base session class providing common functionality"""
    
    def __init__(self, config: Optional[SessionConfig] = None):
        """
        Initialize base session.
        
        Args:
            config: Session configuration
        """
        self.config = config or SessionConfig()
        self.info = SessionInfo(
            session_id=self.config.session_id,
            session_type=self.config.session_type
        )
        self._lock = asyncio.Lock()
        self._closed = False
    
    @property
    def session_id(self) -> str:
        """Get session ID"""
        return self.info.session_id
    
    @property
    def session_type(self) -> SessionType:
        """Get session type"""
        return self.info.session_type
    
    @property
    def is_closed(self) -> bool:
        """Check if session is closed"""
        return self._closed or self.info.state == SessionState.CLOSED
    
    @property
    def is_busy(self) -> bool:
        """Check if session is busy"""
        return self.info.state == SessionState.BUSY
    
    async def acquire(self) -> None:
        """Acquire the session for use"""
        async with self._lock:
            if self.is_closed:
                raise SessionError(f"Session {self.session_id} is closed")
            self.info.state = SessionState.BUSY
            self.info.last_used = time.time()
    
    async def release(self, success: bool = True, response_time: float = 0.0) -> None:
        """Release the session after use"""
        async with self._lock:
            self.info.state = SessionState.IDLE
            self.info.update_stats(success, response_time)
    
    async def reserve(self) -> None:
        """Reserve the session (prevent it from being used by others)"""
        async with self._lock:
            if self.is_closed:
                raise SessionError(f"Session {self.session_id} is closed")
            self.info.state = SessionState.RESERVED
    
    async def unreserve(self) -> None:
        """Unreserve the session"""
        async with self._lock:
            self.info.state = SessionState.IDLE
    
    async def close(self) -> None:
        """Close the session"""
        async with self._lock:
            if self.is_closed:
                return
            
            self.info.state = SessionState.CLOSING
            self._closed = True
            self.info.state = SessionState.CLOSED
            _logger.info(f"Session {self.session_id} closed")
    
    def __enter__(self) -> "BaseSession":
        """Context manager entry"""
        # For sync usage, we'll use the async version
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.acquire())
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit"""
        import asyncio
        loop = asyncio.get_event_loop()
        success = exc_type is None
        loop.run_until_complete(self.release(success))
    
    async def __aenter__(self) -> "BaseSession":
        """Async context manager entry"""
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit"""
        success = exc_type is None
        await self.release(success)
    
    def get_info(self) -> SessionInfo:
        """Get session information"""
        return self.info
    
    def __str__(self) -> str:
        return f"{self.session_type.value}:{self.session_id} ({self.info.state.value})"


class HTTPSession(BaseSession):
    """HTTP session for regular HTTP requests"""
    
    def __init__(self, config: Optional[SessionConfig] = None):
        """Initialize HTTP session"""
        if config is None:
            config = SessionConfig(
                session_type=SessionType.HTTP,
                session_id=f"http_{str(uuid.uuid4())[:8]}"
            )
        super().__init__(config)
        
        # Initialize HTTP client
        self._client = None
        self._init_client()
    
    def _init_client(self) -> None:
        """Initialize the HTTP client"""
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            self._client = aiohttp.ClientSession(
                timeout=timeout,
                headers=self.config.default_headers,
                connector=aiohttp.TCPConnector(ssl=self.config.verify_ssl)
            )
        except ImportError:
            # Fallback to requests if aiohttp not available
            import requests
            self._client = requests.Session()
            self._client.headers.update(self.config.default_headers)
            self._client.verify = self.config.verify_ssl
    
    async def fetch(self, url: str, method: str = "GET", **kwargs) -> Any:
        """Fetch a URL using this session"""
        start_time = time.time()
        
        try:
            await self.acquire()
            
            if hasattr(self._client, 'request'):  # aiohttp
                async with self._client.request(method, url, **kwargs) as response:
                    data = await response.text()
                    success = response.status < 400
                    return data, response.status, success
            else:  # requests
                import requests
                response = self._client.request(method, url, **kwargs)
                success = response.status_code < 400
                return response.text, response.status_code, success
                
        except Exception as e:
            await self.release(success=False)
            raise
        finally:
            await self.release(success=True, response_time=time.time() - start_time)
    
    async def close(self) -> None:
        """Close HTTP session and underlying client"""
        try:
            if hasattr(self._client, 'close'):
                if asyncio.iscoroutinefunction(self._client.close):
                    await self._client.close()
                else:
                    self._client.close()
        except Exception as e:
            _logger.warning(f"Error closing HTTP client: {e}")
        finally:
            await super().close()


class BrowserSession(BaseSession):
    """Browser session using Playwright"""
    
    def __init__(self, config: Optional[SessionConfig] = None):
        """Initialize browser session"""
        if config is None:
            config = SessionConfig(
                session_type=SessionType.BROWSER,
                session_id=f"browser_{str(uuid.uuid4())[:8]}",
                browser_type=BrowserType.CHROMIUM,
                headless=True
            )
        super().__init__(config)
        
        # Browser instance
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
    
    async def _init_browser(self) -> None:
        """Initialize browser"""
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            
            browser_type_map = {
                BrowserType.CHROMIUM: self._playwright.chromium,
                BrowserType.CHROME: self._playwright.chromium,
                BrowserType.FIREFOX: self._playwright.firefox,
                BrowserType.WEBKIT: self._playwright.webkit,
                BrowserType.EDGE: self._playwright.chromium,
            }
            
            browser_launcher = browser_type_map.get(self.config.browser_type, self._playwright.chromium)
            self._browser = await browser_launcher.launch(
                headless=self.config.headless,
                timeout=self.config.timeout * 1000
            )
            
            self._context = await self._browser.new_context(
                viewport={'width': self.config.viewport_width, 'height': self.config.viewport_height}
            )
            
            if self.config.user_agent:
                await self._context.set_user_agent(self.config.user_agent)
            
            self._page = await self._context.new_page()
            
        except ImportError:
            raise SessionError("Playwright is required for browser sessions. Install with: pip install playwright")
    
    async def get_page(self) -> Any:
        """Get the browser page, initializing if necessary"""
        if self._page is None:
            await self._init_browser()
        return self._page
    
    async def fetch(self, url: str, **kwargs) -> Any:
        """Fetch a URL using browser automation"""
        start_time = time.time()
        
        try:
            await self.acquire()
            page = await self.get_page()
            
            await page.goto(url, timeout=self.config.timeout * 1000)
            content = await page.content()
            title = await page.title()
            
            success = page.url == url or "error" not in page.url.lower()
            return content, 200, success, {"title": title, "url": page.url}
            
        except Exception as e:
            await self.release(success=False)
            raise
        finally:
            await self.release(success=True, response_time=time.time() - start_time)
    
    async def close(self) -> None:
        """Close browser session"""
        try:
            if self._page:
                await self._page.close()
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            _logger.warning(f"Error closing browser session: {e}")
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
            await super().close()


class StealthySession(BrowserSession):
    """Stealthy browser session with enhanced anti-detection"""
    
    def __init__(self, config: Optional[SessionConfig] = None):
        """Initialize stealthy session"""
        if config is None:
            config = SessionConfig(
                session_type=SessionType.STEALTH,
                session_id=f"stealth_{str(uuid.uuid4())[:8]}",
                browser_type=BrowserType.CHROMIUM,
                headless=True
            )
        super().__init__(config)
    
    async def _init_browser(self) -> None:
        """Initialize browser with stealth settings"""
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            
            browser_type_map = {
                BrowserType.CHROMIUM: self._playwright.chromium,
                BrowserType.CHROME: self._playwright.chromium,
                BrowserType.FIREFOX: self._playwright.firefox,
                BrowserType.WEBKIT: self._playwright.webkit,
                BrowserType.EDGE: self._playwright.chromium,
            }
            
            browser_launcher = browser_type_map.get(self.config.browser_type, self._playwright.chromium)
            
            # Use installed Chrome for better stealth
            if self.config.use_installed_chrome:
                self._browser = await browser_launcher.launch_persistent_context(
                    user_data_dir=str(Path.home() / ".scrapper_browser_data"),
                    headless=self.config.headless,
                    timeout=self.config.timeout * 1000,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-infobars',
                        '--disable-extensions',
                        '--disable-gpu',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                    ]
                )
                self._context = self._browser
            else:
                self._browser = await browser_launcher.launch(
                    headless=self.config.headless,
                    timeout=self.config.timeout * 1000,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-infobars',
                        '--disable-extensions',
                        '--disable-gpu',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                    ]
                )
                self._context = await self._browser.new_context()
            
            # Set stealthy user agent and viewport
            await self._context.set_user_agent(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            await self._context.set_viewport_size({
                'width': self.config.viewport_width,
                'height': self.config.viewport_height
            })
            
            self._page = await self._context.new_page()
            
            # Additional stealth measures
            await self._page.evaluate("""
                () => {
                    Object.defineProperty(navigator, 'webdriver', {get: () => false});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
                    
                    // Remove web driver flags
                    if (navigator.webdriver) {
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined,
                            configurable: true
                        });
                    }
                    
                    // Overwrite permissions
                    if (navigator.permissions) {
                        navigator.permissions.query = () => Promise.resolve({state: 'granted'});
                    }
                }
            """)
            
        except ImportError:
            raise SessionError("Playwright is required for stealth sessions. Install with: pip install playwright")


class SessionPool:
    """Pool of reusable sessions"""
    
    def __init__(self, pool_size: int = 10, session_type: SessionType = SessionType.HTTP,
                 config: Optional[SessionConfig] = None):
        """
        Initialize session pool.
        
        Args:
            pool_size: Maximum number of sessions in pool
            session_type: Type of sessions to create
            config: Base configuration for sessions
        """
        self.pool_size = pool_size
        self.session_type = session_type
        self.base_config = config or SessionConfig(session_type=session_type)
        self._sessions: List[BaseSession] = []
        self._busy_sessions: Dict[str, BaseSession] = {}
        self._idle_sessions: List[BaseSession] = []
        self._lock = asyncio.Lock()
        self._created = 0
        self._destroyed = 0
        
        self._session_creation_lock = asyncio.Lock()
    
    async def _create_session(self) -> BaseSession:
        """Create a new session"""
        async with self._session_creation_lock:
            config = SessionConfig(
                session_id=f"{self.session_type.value}_{str(uuid.uuid4())[:8]}",
                session_type=self.session_type,
                timeout=self.base_config.timeout,
                max_age=self.base_config.max_age,
                browser_type=self.base_config.browser_type,
                headless=self.base_config.headless
            )
            
            session_classes = {
                SessionType.HTTP: HTTPSession,
                SessionType.BROWSER: BrowserSession,
                SessionType.STEALTH: StealthySession,
                SessionType.DYNAMIC: HTTPSession,  # Default to HTTP for dynamic
            }
            
            session_class = session_classes.get(self.session_type, HTTPSession)
            session = session_class(config)
            
            self._sessions.append(session)
            self._idle_sessions.append(session)
            self._created += 1
            
            _logger.debug(f"Created new session: {session}")
            return session
    
    async def acquire_session(self, timeout: Optional[float] = None) -> BaseSession:
        """
        Acquire a session from the pool.
        
        Args:
            timeout: Maximum time to wait for a session
            
        Returns:
            Available session
            
        Raises:
            SessionPoolExhausted: If no sessions available and pool is full
        """
        start_time = time.time()
        
        async with self._lock:
            # Try to get an idle session
            while self._idle_sessions:
                session = self._idle_sessions.pop(0)
                if not session.is_closed and not session.is_busy:
                    self._busy_sessions[session.session_id] = session
                    await session.acquire()
                    return session
            
            # If we can create more sessions
            if len(self._sessions) < self.pool_size:
                session = await self._create_session()
                self._busy_sessions[session.session_id] = session
                await session.acquire()
                return session
        
        # If timeout is specified, wait for a session to become available
        if timeout is not None:
            remaining_time = timeout - (time.time() - start_time)
            if remaining_time > 0:
                try:
                    # Wait for a session to be released
                    await asyncio.wait_for(
                        self._wait_for_available_session(remaining_time),
                        timeout=remaining_time
                    )
                    
                    # Try again
                    async with self._lock:
                        if self._idle_sessions:
                            session = self._idle_sessions.pop(0)
                            self._busy_sessions[session.session_id] = session
                            await session.acquire()
                            return session
                except asyncio.TimeoutError:
                    pass
        
        raise SessionPoolExhausted(f"No sessions available after {timeout or 'no'} timeout")
    
    async def _wait_for_available_session(self, timeout: float) -> None:
        """Wait for a session to become available"""
        # This would normally be implemented with condition variables
        # For simplicity, we'll use a basic approach
        await asyncio.sleep(min(0.1, timeout))
    
    async def release_session(self, session: BaseSession) -> None:
        """Release a session back to the pool"""
        async with self._lock:
            if session.session_id in self._busy_sessions:
                del self._busy_sessions[session.session_id]
                
                # Check if session should be destroyed
                if session.info.is_expired(self.base_config.max_age):
                    await session.close()
                    self._sessions.remove(session)
                    self._destroyed += 1
                    _logger.debug(f"Destroyed expired session: {session}")
                else:
                    self._idle_sessions.append(session)
                    await session.release(success=True)
                    _logger.debug(f"Released session: {session}")
    
    async def close_all(self) -> None:
        """Close all sessions in the pool"""
        async with self._lock:
            for session in self._sessions:
                try:
                    await session.close()
                except Exception as e:
                    _logger.warning(f"Error closing session {session.session_id}: {e}")
            
            self._sessions.clear()
            self._idle_sessions.clear()
            self._busy_sessions.clear()
            _logger.info(f"Closed all sessions in pool ({self._destroyed} sessions destroyed)")
    
    async def cleanup_expired(self) -> int:
        """Clean up expired sessions"""
        cleaned = 0
        async with self._lock:
            expired_sessions = [
                s for s in self._sessions 
                if s.info.is_expired(self.base_config.max_age)
            ]
            
            for session in expired_sessions:
                try:
                    await session.close()
                    self._sessions.remove(session)
                    if session.session_id in self._busy_sessions:
                        del self._busy_sessions[session.session_id]
                    if session in self._idle_sessions:
                        self._idle_sessions.remove(session)
                    cleaned += 1
                except Exception as e:
                    _logger.warning(f"Error cleaning up session {session.session_id}: {e}")
        
        return cleaned
    
    def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics"""
        return {
            'pool_size': self.pool_size,
            'total_created': self._created,
            'total_destroyed': self._destroyed,
            'total_sessions': len(self._sessions),
            'busy_sessions': len(self._busy_sessions),
            'idle_sessions': len(self._idle_sessions),
            'session_type': self.session_type.value,
            'available': len(self._idle_sessions) + (self.pool_size - len(self._sessions))
        }
    
    def __str__(self) -> str:
        stats = self.get_stats()
        return f"SessionPool(type={stats['session_type']}, size={stats['total_sessions']}, busy={stats['busy_sessions']}, idle={stats['idle_sessions']})"


class SessionManager:
    """High-level session manager with multiple pools"""
    
    def __init__(self):
        """Initialize session manager"""
        self._pools: Dict[SessionType, SessionPool] = {}
        self._default_pool_size: int = 10
        self._settings = get_settings()
        
        # Initialize default pools
        self._init_default_pools()
    
    def _init_default_pools(self) -> None:
        """Initialize default session pools"""
        pool_configs = [
            (SessionType.HTTP, self._settings.performance.concurrent_requests),
            (SessionType.BROWSER, self._settings.performance.concurrent_browsers),
            (SessionType.STEALTH, max(1, self._settings.performance.concurrent_browsers // 2)),
        ]
        
        for session_type, pool_size in pool_configs:
            self._pools[session_type] = SessionPool(
                pool_size=pool_size,
                session_type=session_type
            )
    
    async def get_session(self, session_type: Optional[SessionType] = None, 
                          fetch_mode: Optional[FetchMode] = None) -> BaseSession:
        """
        Get a session of the specified type.
        
        Args:
            session_type: Specific session type to get
            fetch_mode: Fetch mode to determine session type
            
        Returns:
            Session instance
        """
        # Determine session type from fetch mode
        if session_type is None and fetch_mode is not None:
            session_type_map = {
                FetchMode.HTTP: SessionType.HTTP,
                FetchMode.SESSION: SessionType.HTTP,
                FetchMode.BROWSER: SessionType.BROWSER,
                FetchMode.STEALTH: SessionType.STEALTH,
                FetchMode.AUTO: SessionType.HTTP,  # Default to HTTP for auto
            }
            session_type = session_type_map.get(fetch_mode, SessionType.HTTP)
        elif session_type is None:
            session_type = SessionType.HTTP
        
        # Get or create pool for this session type
        if session_type not in self._pools:
            self._pools[session_type] = SessionPool(
                pool_size=self._default_pool_size,
                session_type=session_type
            )
        
        pool = self._pools[session_type]
        return await pool.acquire_session()
    
    async def release_session(self, session: BaseSession) -> None:
        """Release a session back to its pool"""
        if session.session_type in self._pools:
            await self._pools[session.session_type].release_session(session)
    
    async def cleanup_all(self) -> Dict[str, int]:
        """Clean up all session pools"""
        results = {}
        for pool_type, pool in self._pools.items():
            cleaned = await pool.cleanup_expired()
            results[pool_type.value] = cleaned
        
        return results
    
    async def close_all(self) -> None:
        """Close all session pools"""
        for pool in self._pools.values():
            await pool.close_all()
        self._pools.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get overall session manager statistics"""
        stats = {
            'pools': {},
            'total_created': 0,
            'total_destroyed': 0,
            'total_busy': 0,
            'total_idle': 0
        }
        
        for pool_type, pool in self._pools.items():
            pool_stats = pool.get_stats()
            stats['pools'][pool_type.value] = pool_stats
            stats['total_created'] += pool_stats['total_created']
            stats['total_destroyed'] += pool_stats['total_destroyed']
            stats['total_busy'] += pool_stats['busy_sessions']
            stats['total_idle'] += pool_stats['idle_sessions']
        
        return stats
    
    @contextmanager
    def session_context(self, session_type: Optional[SessionType] = None, 
                        fetch_mode: Optional[FetchMode] = None):
        """
        Context manager for session usage.
        
        Usage:
            with session_manager.session_context(FetchMode.BROWSER) as session:
                result = session.fetch(url)
        """
        import asyncio
        
        session = None
        try:
            # Create a new event loop for sync usage
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Get session asynchronously
            session = loop.run_until_complete(
                self.get_session(session_type, fetch_mode)
            )
            yield session
            
            # Release session
            loop.run_until_complete(self.release_session(session))
            
        except Exception as e:
            if session:
                loop.run_until_complete(self.release_session(session))
            raise
        finally:
            if loop:
                loop.close()
    
    @asynccontextmanager
    async def async_session_context(self, session_type: Optional[SessionType] = None,
                                     fetch_mode: Optional[FetchMode] = None):
        """
        Async context manager for session usage.
        
        Usage:
            async with session_manager.async_session_context(FetchMode.BROWSER) as session:
                result = await session.fetch(url)
        """
        session = await self.get_session(session_type, fetch_mode)
        try:
            yield session
        finally:
            await self.release_session(session)


# Global session manager instance
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """Get the global session manager instance"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager


def reset_session_manager() -> None:
    """Reset the global session manager"""
    global _session_manager
    _session_manager = None


# Module exports
__all__ = [
    # Session classes
    'BaseSession',
    'HTTPSession',
    'BrowserSession', 
    'StealthySession',
    
    # Pool and manager classes
    'SessionPool',
    'SessionManager',
    
    # Configuration and info classes
    'SessionConfig',
    'SessionInfo',
    
    # Enums
    'SessionType',
    'SessionState',
    
    # Exceptions
    'SessionError',
    'SessionPoolExhausted',
    'SessionExpired',
    'SessionNotAvailable',
    
    # Utility functions
    'get_session_manager',
    'reset_session_manager',
]