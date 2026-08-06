"""
Proxy Rotation Module
====================
Advanced proxy rotation with intelligent selection, health checking, and blacklisting.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from contextlib import asynccontextmanager, contextmanager
from collections import defaultdict

from ..config.schemas import ProxyRotationStrategy
from ..config.settings import get_settings

# Logger
_logger = logging.getLogger(__name__)


class ProxyStatus(Enum):
    """Proxy status"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    BLACKLISTED = "blacklisted"
    UNTESTED = "untested"


class ProxyProtocol(Enum):
    """Proxy protocols"""
    HTTP = "http"
    HTTPS = "https"
    SOCKS5 = "socks5"
    SOCKS4 = "socks4"
    ALL = "all"


class ProxyType(Enum):
    """Proxy types"""
    RESIDENTIAL = "residential"
    DATACENTER = "datacenter"
    MOBILE = "mobile"
    SOCKS = "socks"
    UNKNOWN = "unknown"


@dataclass
class ProxyInfo:
    """Information about a proxy"""
    url: str
    protocol: ProxyProtocol = ProxyProtocol.HTTP
    host: str = ""
    port: int = 0
    username: Optional[str] = None
    password: Optional[str] = None
    proxy_type: ProxyType = ProxyType.UNKNOWN
    country: Optional[str] = None
    city: Optional[str] = None
    isp: Optional[str] = None
    anonymity: str = "transparent"  # transparent, anonymous, elite
    
    # Performance metrics
    latency: float = 0.0  # in seconds
    success_rate: float = 1.0  # 0.0 to 1.0
    failure_count: int = 0
    success_count: int = 0
    total_requests: int = 0
    last_used: float = 0.0
    last_tested: float = 0.0
    first_failure: float = 0.0
    
    # Status
    status: ProxyStatus = ProxyStatus.UNTESTED
    last_error: Optional[str] = None
    
    # Metadata
    source: str = "manual"
    tags: List[str] = field(default_factory=list)
    added_at: float = field(default_factory=time.time)
    notes: str = ""
    
    def __post_init__(self):
        """Parse URL and extract components"""
        if self.host == "" or self.port == 0:
            self._parse_url()
        if self.last_used == 0.0:
            self.last_used = time.time()
        if self.last_tested == 0.0:
            self.last_tested = time.time()
    
    def _parse_url(self) -> None:
        """Parse proxy URL to extract components"""
        try:
            parsed = urllib.parse.urlparse(self.url)
            self.protocol = ProxyProtocol(parsed.scheme.lower())
            self.host = parsed.hostname or self.host
            self.port = parsed.port or self.port
            
            if parsed.username:
                self.username = parsed.username
            if parsed.password:
                self.password = parsed.password
                
        except Exception as e:
            _logger.warning(f"Failed to parse proxy URL {self.url}: {e}")
    
    def get_auth_url(self) -> str:
        """Get URL with authentication"""
        if self.username and self.password:
            return f"{self.protocol.value}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.protocol.value}://{self.host}:{self.port}"
    
    def get_clean_url(self) -> str:
        """Get URL without authentication"""
        return f"{self.protocol.value}://{self.host}:{self.port}"
    
    def get_dict(self) -> Dict[str, str]:
        """Get proxy as dict (for HTTP requests)"""
        if self.username and self.password:
            return {
                'http': self.get_auth_url(),
                'https': self.get_auth_url()
            }
        return {
            'http': self.get_clean_url(),
            'https': self.get_clean_url()
        }
    
    def is_healthy(self) -> bool:
        """Check if proxy is healthy"""
        return self.status in [ProxyStatus.ACTIVE, ProxyStatus.UNTESTED]
    
    def is_available(self) -> bool:
        """Check if proxy is available for use"""
        return self.status == ProxyStatus.ACTIVE
    
    def get_latency_class(self) -> str:
        """Get latency classification"""
        if self.latency == 0.0:
            return "unknown"
        elif self.latency < 0.5:
            return "excellent"
        elif self.latency < 1.0:
            return "good"
        elif self.latency < 2.0:
            return "fair"
        elif self.latency < 5.0:
            return "slow"
        else:
            return "very_slow"
    
    def get_success_rate_percentage(self) -> float:
        """Get success rate as percentage"""
        if self.total_requests == 0:
            return 100.0
        return (self.success_count / self.total_requests) * 100
    
    def mark_success(self, response_time: float) -> None:
        """Mark a successful request"""
        self.success_count += 1
        self.total_requests += 1
        self.last_used = time.time()
        self.last_error = None
        
        # Update latency (exponential moving average)
        if self.latency == 0.0:
            self.latency = response_time
        else:
            alpha = 0.3
            self.latency = alpha * response_time + (1 - alpha) * self.latency
        
        # Update success rate
        self.success_rate = self.success_count / self.total_requests
        
        # If was failed/untested, try to activate
        if self.status in [ProxyStatus.FAILED, ProxyStatus.UNTESTED]:
            if self.success_count >= 2:  # Require 2 successes to activate
                self.status = ProxyStatus.ACTIVE
        elif self.status == ProxyStatus.BLACKLISTED:
            # Don't automatically unblacklist
            pass
    
    def mark_failure(self, error: str) -> None:
        """Mark a failed request"""
        self.failure_count += 1
        self.total_requests += 1
        self.last_used = time.time()
        self.last_error = error
        
        # Update success rate
        if self.total_requests > 0:
            self.success_rate = self.success_count / self.total_requests
        
        # Update failure tracking
        if self.first_failure == 0.0:
            self.first_failure = time.time()
        
        # Update status based on failures
        if self.failure_count >= 3:  # 3 consecutive failures
            self.status = ProxyStatus.FAILED
    
    def blacklist(self, reason: str = "manual") -> None:
        """Blacklist this proxy"""
        self.status = ProxyStatus.BLACKLISTED
        self.last_error = f"Blacklisted: {reason}"
    
    def unblacklist(self) -> None:
        """Remove from blacklist"""
        if self.status == ProxyStatus.BLACKLISTED:
            self.status = ProxyStatus.UNTESTED
        self.last_error = None
        self.failure_count = 0
    
    def test(self, test_url: str = "https://httpbin.org/ip") -> float:
        """Test proxy latency and functionality (sync)"""
        import requests
        start_time = time.time()
        
        try:
            response = requests.get(
                test_url,
                proxies=self.get_dict(),
                timeout=10
            )
            latency = time.time() - start_time
            self.mark_success(latency)
            self.last_tested = time.time()
            return latency
            
        except Exception as e:
            self.mark_failure(str(e))
            self.last_tested = time.time()
            return -1.0
    
    async def test_async(self, test_url: str = "https://httpbin.org/ip") -> float:
        """Test proxy latency and functionality (async)"""
        start_time = time.time()
        
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    test_url,
                    proxy=self.get_clean_url(),
                    proxy_auth=None if not self.username else aiohttp.BasicAuth(self.username, self.password),
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    latency = time.time() - start_time
                    self.mark_success(latency)
                    self.last_tested = time.time()
                    return latency
                    
        except Exception as e:
            self.mark_failure(str(e))
            self.last_tested = time.time()
            return -1.0
    
    def __str__(self) -> str:
        return f"Proxy({self.protocol.value}://{self.host}:{self.port}, status={self.status.value})"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'url': self.get_clean_url(),
            'protocol': self.protocol.value,
            'host': self.host,
            'port': self.port,
            'country': self.country,
            'type': self.proxy_type.value,
            'status': self.status.value,
            'latency': self.latency,
            'success_rate': self.get_success_rate_percentage(),
            'total_requests': self.total_requests,
            'last_used': self.last_used,
            'last_error': self.last_error,
            'tags': self.tags,
            'source': self.source
        }


@dataclass
class ProxyStats:
    """Statistics for proxy pool"""
    total_proxies: int = 0
    active_proxies: int = 0
    failed_proxies: int = 0
    blacklisted_proxies: int = 0
    untested_proxies: int = 0
    
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    
    average_latency: float = 0.0
    average_success_rate: float = 0.0
    
    def __str__(self) -> str:
        return (f"ProxyStats(total={self.total_proxies}, "
                f"active={self.active_proxies}, "
                f"failed={self.failed_proxies}, "
                f"blacklisted={self.blacklisted_proxies})")


class ProxyError(Exception):
    """Base proxy exception"""
    pass


class ProxyPoolExhausted(ProxyError):
    """Raised when all proxies are exhausted or blacklisted"""
    pass


class ProxyNotAvailable(ProxyError):
    """Raised when no proxies are available"""
    pass


class ProxyHealthChecker:
    """Health checker for proxy validation"""
    
    def __init__(self, test_url: str = "https://httpbin.org/ip",
                 test_timeout: int = 10, check_interval: int = 60):
        """
        Initialize proxy health checker.
        
        Args:
            test_url: URL to use for testing proxies
            test_timeout: Timeout for test requests
            check_interval: Interval between health checks (seconds)
        """
        self.test_url = test_url
        self.test_timeout = test_timeout
        self.check_interval = check_interval
        self._last_check: float = 0.0
    
    async def check_proxy(self, proxy: ProxyInfo) -> bool:
        """Check if a proxy is healthy"""
        latency = await proxy.test_async(self.test_url)
        return latency >= 0
    
    async def check_all(self, proxies: List[ProxyInfo]) -> Dict[str, bool]:
        """Check health of all proxies"""
        results = {}
        tasks = []
        
        for proxy in proxies:
            if proxy.status not in [ProxyStatus.ACTIVE, ProxyStatus.UNTESTED]:
                continue
            tasks.append(self._check_proxy_with_timeout(proxy))
        
        completed = await asyncio.gather(*tasks, return_exceptions=True)
        
        for proxy, result in zip(proxies, completed):
            if isinstance(result, Exception):
                results[proxy.get_clean_url()] = False
            else:
                results[proxy.get_clean_url()] = result
        
        return results
    
    async def _check_proxy_with_timeout(self, proxy: ProxyInfo) -> bool:
        """Check a single proxy with timeout"""
        try:
            return await asyncio.wait_for(
                self.check_proxy(proxy),
                timeout=self.test_timeout
            )
        except asyncio.TimeoutError:
            return False
    
    async def periodic_check(self, proxy_rotator: "SmartProxyRotator") -> int:
        """Perform periodic health check on all proxies"""
        current_time = time.time()
        if current_time - self._last_check < self.check_interval:
            return 0
        
        self._last_check = current_time
        proxies = proxy_rotator.get_all_proxies()
        results = await self.check_all(proxies)
        
        activated = 0
        failed = 0
        
        for proxy in proxies:
            url = proxy.get_clean_url()
            if url in results and results[url]:
                if proxy.status != ProxyStatus.ACTIVE:
                    proxy.status = ProxyStatus.ACTIVE
                    activated += 1
            elif proxy.status == ProxyStatus.ACTIVE:
                proxy.status = ProxyStatus.FAILED
                failed += 1
        
        _logger.info(f"Health check completed: {activated} activated, {failed} failed")
        return activated + failed


class SmartProxyRotator:
    """
    Intelligent proxy rotator with multiple selection strategies.
    """
    
    def __init__(self, proxies: Optional[List[Union[str, ProxyInfo]]] = None,
                 strategy: ProxyRotationStrategy = ProxyRotationStrategy.ROUND_ROBIN):
        """
        Initialize proxy rotator.
        
        Args:
            proxies: Initial list of proxies (URLs or ProxyInfo objects)
            strategy: Rotation strategy to use
        """
        self.proxies: List[ProxyInfo] = []
        self.strategy = strategy
        self._current_index = 0
        self._last_used_proxy: Optional[ProxyInfo] = None
        self._usage_counts: Dict[str, int] = defaultdict(int)
        self._last_rotation: float = 0.0
        self._rotation_lock = asyncio.Lock()
        
        # Health checker
        self.health_checker = ProxyHealthChecker()
        
        # Add initial proxies
        if proxies:
            for proxy in proxies:
                self.add_proxy(proxy)
        
        # Load proxies from configuration
        self._load_proxies_from_config()
    
    def _load_proxies_from_config(self) -> None:
        """Load proxies from configuration"""
        try:
            settings = get_settings()
            for proxy_url in settings.proxy.proxies:
                self.add_proxy(proxy_url)
        except Exception:
            pass
    
    def add_proxy(self, proxy: Union[str, ProxyInfo]) -> ProxyInfo:
        """Add a proxy to the rotator"""
        if isinstance(proxy, str):
            proxy_info = ProxyInfo(url=proxy)
        else:
            proxy_info = proxy
        
        # Avoid duplicates
        existing_urls = {p.get_clean_url() for p in self.proxies}
        if proxy_info.get_clean_url() not in existing_urls:
            self.proxies.append(proxy_info)
            _logger.info(f"Added proxy: {proxy_info}")
        else:
            _logger.debug(f"Proxy already exists: {proxy_info}")
        
        return proxy_info
    
    def add_proxies(self, proxies: List[Union[str, ProxyInfo]]) -> List[ProxyInfo]:
        """Add multiple proxies"""
        return [self.add_proxy(p) for p in proxies]
    
    def remove_proxy(self, proxy: Union[str, ProxyInfo]) -> bool:
        """Remove a proxy from the rotator"""
        if isinstance(proxy, str):
            target_url = proxy
        else:
            target_url = proxy.get_clean_url()
        
        for i, p in enumerate(self.proxies):
            if p.get_clean_url() == target_url:
                del self.proxies[i]
                _logger.info(f"Removed proxy: {target_url}")
                return True
        
        return False
    
    def remove_failed_proxies(self) -> int:
        """Remove all failed proxies"""
        failed_count = 0
        self.proxies = [
            p for p in self.proxies 
            if p.status != ProxyStatus.FAILED or p.failure_count < 5
        ]
        
        for p in self.proxies:
            if p.status == ProxyStatus.FAILED:
                failed_count += 1
        
        if failed_count > 0:
            _logger.info(f"Removed {failed_count} failed proxies")
        
        return failed_count
    
    def get_proxy(self, target_url: Optional[str] = None) -> Optional[ProxyInfo]:
        """Get the next proxy based on rotation strategy"""
        if not self.proxies:
            return None
        
        available_proxies = [p for p in self.proxies if p.is_available()]
        
        if not available_proxies:
            # Try to use untested or failed proxies as last resort
            available_proxies = [p for p in self.proxies if p.status != ProxyStatus.BLACKLISTED]
            if not available_proxies:
                _logger.warning("No available proxies")
                return None
        
        proxy = self._select_proxy(available_proxies, target_url)
        return proxy
    
    def _select_proxy(self, available_proxies: List[ProxyInfo], 
                     target_url: Optional[str] = None) -> ProxyInfo:
        """Select proxy based on strategy"""
        if self.strategy == ProxyRotationStrategy.ROUND_ROBIN:
            return self._select_round_robin(available_proxies)
        elif self.strategy == ProxyRotationStrategy.RANDOM:
            return self._select_random(available_proxies)
        elif self.strategy == ProxyRotationStrategy.PERFORMANCE:
            return self._select_performance(available_proxies)
        elif self.strategy == ProxyRotationStrategy.GEO:
            return self._select_geo(available_proxies, target_url)
        else:
            return self._select_round_robin(available_proxies)
    
    def _select_round_robin(self, available_proxies: List[ProxyInfo]) -> ProxyInfo:
        """Round-robin selection"""
        proxy = available_proxies[self._current_index % len(available_proxies)]
        self._current_index += 1
        return proxy
    
    def _select_random(self, available_proxies: List[ProxyInfo]) -> ProxyInfo:
        """Random selection"""
        return random.choice(available_proxies)
    
    def _select_performance(self, available_proxies: List[ProxyInfo]) -> ProxyInfo:
        """Performance-based selection (lowest latency, highest success rate)"""
        # Sort by success rate (descending), then by latency (ascending)
        sorted_proxies = sorted(
            available_proxies,
            key=lambda p: (-p.get_success_rate_percentage(), p.latency)
        )
        return sorted_proxies[0] if sorted_proxies else None
    
    def _select_geo(self, available_proxies: List[ProxyInfo], 
                   target_url: Optional[str] = None) -> ProxyInfo:
        """Geo-based selection (closest to target)"""
        if target_url:
            # Try to match by country if target domain is known
            target_country = self._extract_country_from_url(target_url)
            if target_country:
                country_proxies = [p for p in available_proxies if p.country == target_country]
                if country_proxies:
                    return random.choice(country_proxies)
        
        # Fall back to performance-based selection
        return self._select_performance(available_proxies)
    
    def _extract_country_from_url(self, url: str) -> Optional[str]:
        """Extract country code from URL (simplified)"""
        try:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.hostname or ""
            
            # Simple country code extraction from TLD
            parts = domain.split('.')
            if len(parts) >= 2:
                # Check if last part is a country code (2 letters)
                tld = parts[-1].lower()
                if len(tld) == 2 and tld.isalpha():
                    return tld
        except Exception:
            pass
        return None
    
    async def get_proxy_async(self, target_url: Optional[str] = None) -> Optional[ProxyInfo]:
        """Async version of get_proxy"""
        return self.get_proxy(target_url)
    
    def mark_success(self, proxy: ProxyInfo, response_time: float) -> None:
        """Mark a successful request for a proxy"""
        proxy.mark_success(response_time)
        self._usage_counts[proxy.get_clean_url()] += 1
        self._last_used_proxy = proxy
        self._last_rotation = time.time()
    
    def mark_failure(self, proxy: ProxyInfo, error: str) -> None:
        """Mark a failed request for a proxy"""
        proxy.mark_failure(error)
        
        # Check if should be blacklisted
        if proxy.failure_count >= 5:  # 5 consecutive failures
            proxy.blacklist("too many failures")
            _logger.warning(f"Blacklisted proxy: {proxy} (reason: too many failures)")
    
    def blacklist_proxy(self, proxy: Union[str, ProxyInfo], reason: str = "manual") -> bool:
        """Manually blacklist a proxy"""
        if isinstance(proxy, str):
            target_url = proxy
        else:
            target_url = proxy.get_clean_url()
        
        for p in self.proxies:
            if p.get_clean_url() == target_url:
                p.blacklist(reason)
                _logger.info(f"Blacklisted proxy: {target_url} (reason: {reason})")
                return True
        
        return False
    
    def unblacklist_proxy(self, proxy: Union[str, ProxyInfo]) -> bool:
        """Manually unblacklist a proxy"""
        if isinstance(proxy, str):
            target_url = proxy
        else:
            target_url = proxy.get_clean_url()
        
        for p in self.proxies:
            if p.get_clean_url() == target_url:
                p.unblacklist()
                _logger.info(f"Unblacklisted proxy: {target_url}")
                return True
        
        return False
    
    def get_all_proxies(self) -> List[ProxyInfo]:
        """Get all proxies"""
        return self.proxies.copy()
    
    def get_available_proxies(self) -> List[ProxyInfo]:
        """Get all available (active) proxies"""
        return [p for p in self.proxies if p.is_available()]
    
    def get_blacklisted_proxies(self) -> List[ProxyInfo]:
        """Get all blacklisted proxies"""
        return [p for p in self.proxies if p.status == ProxyStatus.BLACKLISTED]
    
    def get_stats(self) -> ProxyStats:
        """Get proxy pool statistics"""
        stats = ProxyStats()
        
        for proxy in self.proxies:
            stats.total_proxies += 1
            
            if proxy.status == ProxyStatus.ACTIVE:
                stats.active_proxies += 1
            elif proxy.status == ProxyStatus.FAILED:
                stats.failed_proxies += 1
            elif proxy.status == ProxyStatus.BLACKLISTED:
                stats.blacklisted_proxies += 1
            else:
                stats.untested_proxies += 1
            
            stats.total_requests += proxy.total_requests
            stats.successful_requests += proxy.success_count
            stats.failed_requests += proxy.failure_count
            stats.average_latency += proxy.latency
        
        if stats.total_proxies > 0:
            stats.average_latency /= stats.total_proxies
        
        if stats.total_requests > 0:
            stats.average_success_rate = (stats.successful_requests / stats.total_requests) * 100
        
        return stats
    
    def rotate_strategy(self) -> None:
        """Rotate to next selection strategy"""
        strategies = list(ProxyRotationStrategy)
        current_index = strategies.index(self.strategy)
        self.strategy = strategies[(current_index + 1) % len(strategies)]
        _logger.info(f"Rotated to strategy: {self.strategy.value}")
    
    def set_strategy(self, strategy: Union[str, ProxyRotationStrategy]) -> None:
        """Set the rotation strategy"""
        if isinstance(strategy, str):
            self.strategy = ProxyRotationStrategy(strategy)
        else:
            self.strategy = strategy
        _logger.info(f"Set rotation strategy: {self.strategy.value}")
    
    async def refresh_proxies(self, proxy_list: Optional[List[str]] = None) -> int:
        """Refresh proxy list from external source"""
        added = 0
        
        if proxy_list:
            for proxy_url in proxy_list:
                if not any(p.get_clean_url() == proxy_url for p in self.proxies):
                    self.add_proxy(proxy_url)
                    added += 1
        
        # Test new proxies
        new_proxies = [p for p in self.proxies if p.status == ProxyStatus.UNTESTED]
        if new_proxies:
            await self.test_new_proxies(new_proxies)
        
        return added
    
    async def test_new_proxies(self, proxies: List[ProxyInfo]) -> int:
        """Test newly added proxies"""
        tested = 0
        for proxy in proxies:
            if proxy.status == ProxyStatus.UNTESTED:
                latency = await proxy.test_async()
                if latency >= 0:
                    proxy.status = ProxyStatus.ACTIVE
                else:
                    proxy.status = ProxyStatus.FAILED
                tested += 1
        return tested
    
    def get_best_proxy(self) -> Optional[ProxyInfo]:
        """Get the best performing proxy"""
        available = self.get_available_proxies()
        if not available:
            return None
        
        # Sort by success rate and latency
        sorted_proxies = sorted(
            available,
            key=lambda p: (-p.get_success_rate_percentage(), p.latency)
        )
        
        return sorted_proxies[0] if sorted_proxies else None
    
    def get_proxy_by_type(self, proxy_type: ProxyType) -> Optional[ProxyInfo]:
        """Get a proxy of specific type"""
        available = [p for p in self.get_available_proxies() if p.proxy_type == proxy_type]
        return random.choice(available) if available else None
    
    def get_proxy_by_country(self, country: str) -> Optional[ProxyInfo]:
        """Get a proxy from specific country"""
        available = [p for p in self.get_available_proxies() if p.country and p.country.lower() == country.lower()]
        return random.choice(available) if available else None
    
    def clear(self) -> None:
        """Clear all proxies"""
        self.proxies.clear()
        self._current_index = 0
        self._usage_counts.clear()
        self._last_used_proxy = None
        _logger.info("Cleared all proxies")
    
    def __len__(self) -> int:
        return len(self.proxies)
    
    def __str__(self) -> str:
        stats = self.get_stats()
        return (f"SmartProxyRotator(strategy={self.strategy.value}, "
                f"total={stats.total_proxies}, "
                f"active={stats.active_proxies}, "
                f"failed={stats.failed_proxies}, "
                f"blacklisted={stats.blacklisted_proxies})")


class ProxyManager:
    """
    High-level proxy manager with multiple rotators and advanced features.
    """
    
    def __init__(self):
        """Initialize proxy manager"""
        self._rotators: Dict[str, SmartProxyRotator] = {}
        self._default_rotator: Optional[SmartProxyRotator] = None
        self._settings = get_settings()
        
        # Initialize from settings
        self._init_from_settings()
    
    def _init_from_settings(self) -> None:
        """Initialize from configuration"""
        try:
            settings = self._settings
            if settings.proxy.enabled and settings.proxy.proxies:
                rotator = SmartProxyRotator(
                    proxies=settings.proxy.proxies,
                    strategy=settings.proxy.rotation_strategy
                )
                self._default_rotator = rotator
                self._rotators["default"] = rotator
        except Exception:
            pass
    
    def add_rotator(self, name: str, rotator: SmartProxyRotator) -> None:
        """Add a named proxy rotator"""
        self._rotators[name] = rotator
        if self._default_rotator is None:
            self._default_rotator = rotator
    
    def get_rotator(self, name: str = "default") -> SmartProxyRotator:
        """Get a named proxy rotator"""
        if name not in self._rotators:
            if self._default_rotator is None:
                self._default_rotator = SmartProxyRotator()
                self._rotators["default"] = self._default_rotator
            return self._default_rotator
        return self._rotators[name]
    
    def get_proxy(self, target_url: Optional[str] = None, 
                  rotator_name: str = "default") -> Optional[ProxyInfo]:
        """Get a proxy from the specified rotator"""
        rotator = self.get_rotator(rotator_name)
        return rotator.get_proxy(target_url)
    
    async def get_proxy_async(self, target_url: Optional[str] = None,
                            rotator_name: str = "default") -> Optional[ProxyInfo]:
        """Async version of get_proxy"""
        return self.get_proxy(target_url, rotator_name)
    
    def mark_success(self, proxy: ProxyInfo, response_time: float,
                     rotator_name: str = "default") -> None:
        """Mark a successful request"""
        rotator = self.get_rotator(rotator_name)
        rotator.mark_success(proxy, response_time)
    
    def mark_failure(self, proxy: ProxyInfo, error: str,
                     rotator_name: str = "default") -> None:
        """Mark a failed request"""
        rotator = self.get_rotator(rotator_name)
        rotator.mark_failure(proxy, error)
    
    async def health_check(self, rotator_name: str = "default") -> int:
        """Perform health check on all proxies in a rotator"""
        rotator = self.get_rotator(rotator_name)
        return await rotator.health_checker.periodic_check(rotator)
    
    def get_stats(self, rotator_name: str = "default") -> ProxyStats:
        """Get statistics for a rotator"""
        rotator = self.get_rotator(rotator_name)
        return rotator.get_stats()
    
    def get_all_stats(self) -> Dict[str, ProxyStats]:
        """Get statistics for all rotators"""
        return {name: rotator.get_stats() for name, rotator in self._rotators.items()}
    
    def add_proxy(self, proxy: Union[str, ProxyInfo], 
                  rotator_name: str = "default") -> ProxyInfo:
        """Add a proxy to a rotator"""
        rotator = self.get_rotator(rotator_name)
        return rotator.add_proxy(proxy)
    
    def remove_proxy(self, proxy: Union[str, ProxyInfo],
                     rotator_name: str = "default") -> bool:
        """Remove a proxy from a rotator"""
        rotator = self.get_rotator(rotator_name)
        return rotator.remove_proxy(proxy)
    
    def blacklist_proxy(self, proxy: Union[str, ProxyInfo], reason: str = "manual",
                        rotator_name: str = "default") -> bool:
        """Blacklist a proxy"""
        rotator = self.get_rotator(rotator_name)
        return rotator.blacklist_proxy(proxy, reason)
    
    def clear(self, rotator_name: str = "default") -> None:
        """Clear all proxies from a rotator"""
        rotator = self.get_rotator(rotator_name)
        rotator.clear()
    
    def __str__(self) -> str:
        return f"ProxyManager(rotators={list(self._rotators.keys())})"


# Context managers for proxy usage
@contextmanager
def proxy_context(manager: Optional[ProxyManager] = None, target_url: Optional[str] = None,
                 rotator_name: str = "default"):
    """
    Context manager for proxy usage.
    
    Usage:
        with proxy_context() as proxy:
            # Use proxy for requests
            requests.get(url, proxies=proxy.get_dict())
    """
    if manager is None:
        manager = ProxyManager()
    
    proxy_info = manager.get_proxy(target_url, rotator_name)
    
    if proxy_info is None:
        raise ProxyNotAvailable("No proxies available")
    
    try:
        yield proxy_info
    except Exception as e:
        manager.mark_failure(proxy_info, str(e), rotator_name)
        raise


@asynccontextmanager
async def async_proxy_context(manager: Optional[ProxyManager] = None, target_url: Optional[str] = None,
                              rotator_name: str = "default"):
    """
    Async context manager for proxy usage.
    
    Usage:
        async with async_proxy_context() as proxy:
            # Use proxy for async requests
            async with session.get(url, proxy=proxy.get_clean_url()) as response:
                data = await response.text()
    """
    if manager is None:
        manager = ProxyManager()
    
    proxy_info = await manager.get_proxy_async(target_url, rotator_name)
    
    if proxy_info is None:
        raise ProxyNotAvailable("No proxies available")
    
    try:
        yield proxy_info
    except Exception as e:
        manager.mark_failure(proxy_info, str(e), rotator_name)
        raise


# Global proxy manager instance
_proxy_manager: Optional[ProxyManager] = None


def get_proxy_manager() -> ProxyManager:
    """Get the global proxy manager instance"""
    global _proxy_manager
    if _proxy_manager is None:
        _proxy_manager = ProxyManager()
    return _proxy_manager


def reset_proxy_manager() -> None:
    """Reset the global proxy manager"""
    global _proxy_manager
    _proxy_manager = None


# Module exports
__all__ = [
    # Proxy classes
    'ProxyInfo',
    'ProxyStats',
    'SmartProxyRotator',
    'ProxyManager',
    'ProxyHealthChecker',
    
    # Configuration classes
    'ProxyConfig',
    
    # Enums
    'ProxyStatus',
    'ProxyProtocol',
    'ProxyType',
    
    # Exceptions
    'ProxyError',
    'ProxyPoolExhausted',
    'ProxyNotAvailable',
    
    # Context managers
    'proxy_context',
    'async_proxy_context',
    
    # Utility functions
    'get_proxy_manager',
    'reset_proxy_manager',
]