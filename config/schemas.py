"""
Configuration Schemas
====================
Pydantic models for configuration validation and management.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import os
import re

from pydantic import BaseModel, Field, validator, root_validator
from typing_extensions import Literal


class ConfigSource(str, Enum):
    """Configuration source priority"""
    DEFAULT = "default"
    FILE = "file"  
    ENVIRONMENT = "environment"
    CLI = "cli"


class LogLevel(str, Enum):
    """Logging levels"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class FetchMode(str, Enum):
    """Fetching modes"""
    AUTO = "auto"
    HTTP = "http"
    SESSION = "session" 
    BROWSER = "browser"
    STEALTH = "stealth"


class ProxyRotationStrategy(str, Enum):
    """Proxy rotation strategies"""
    ROUND_ROBIN = "round-robin"
    RANDOM = "random"
    PERFORMANCE = "performance"
    GEO = "geo-based"


class CacheBackend(str, Enum):
    """Cache backend options"""
    MEMORY = "memory"
    DISK = "disk"
    DATABASE = "database"
    MULTI = "multi-level"


class AIProvider(str, Enum):
    """AI provider options"""
    NONE = "none"
    LOCAL = "local"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    CUSTOM = "custom"


class BrowserType(str, Enum):
    """Browser types for Playwright"""
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"
    CHROME = "chrome"
    EDGE = "edge"


# =============================================================================
# Configuration Dataclasses (for internal use)
# =============================================================================

@dataclass
class GeneralConfig:
    """General application configuration"""
    # Application settings
    app_name: str = "Scrapper Magic"
    app_version: str = "2.0.0"
    app_description: str = "Enhanced Web Scraping Suite"
    
    # Paths
    base_dir: Path = field(default_factory=lambda: Path.cwd())
    data_dir: Path = field(default_factory=lambda: Path("./data"))
    cache_dir: Path = field(default_factory=lambda: Path("./.cache"))
    logs_dir: Path = field(default_factory=lambda: Path("./logs"))
    output_dir: Path = field(default_factory=lambda: Path("./output"))
    temp_dir: Path = field(default_factory=lambda: Path("./temp"))
    
    # Behavior
    debug: bool = False
    verbose: bool = False
    quiet: bool = False
    
    # Environment
    environment: str = "production"  # development, staging, production
    
    # Security
    allow_private_networks: bool = False
    allow_local_files: bool = True
    
    def __post_init__(self):
        # Ensure paths are absolute
        self.base_dir = self.base_dir.resolve()
        self.data_dir = (self.base_dir / self.data_dir).resolve()
        self.cache_dir = (self.base_dir / self.cache_dir).resolve()
        self.logs_dir = (self.base_dir / self.logs_dir).resolve()
        self.output_dir = (self.base_dir / self.output_dir).resolve()
        self.temp_dir = (self.base_dir / self.temp_dir).resolve()


@dataclass
class HTTPConfig:
    """HTTP client configuration"""
    # Connection settings
    timeout: int = 30  # seconds
    connect_timeout: int = 10
    read_timeout: int = 30
    write_timeout: int = 30
    
    # SSL/TLS
    verify_ssl: bool = True
    ssl_cert: Optional[Path] = None
    ssl_key: Optional[Path] = None
    
    # Redirects
    follow_redirects: bool = True
    max_redirects: int = 10
    
    # Headers
    default_headers: Dict[str, str] = field(default_factory=lambda: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    })
    
    # Impersonation
    impersonate: str = "chrome"  # browser to impersonate
    stealthy_headers: bool = True
    
    # HTTP/2 and HTTP/3
    http2: bool = True
    http3: bool = False
    
    # Retries
    max_retries: int = 3
    retry_backoff: float = 1.5  # exponential backoff factor
    retryable_statuses: List[int] = field(default_factory=lambda: [
        408, 429, 500, 502, 503, 504
    ])
    
    # Compression
    support_compression: bool = True


@dataclass
class ProxyConfig:
    """Proxy configuration"""
    # Proxy enable/disable
    enabled: bool = False
    
    # Proxy list
    proxies: List[str] = field(default_factory=list)
    proxy_files: List[Path] = field(default_factory=list)
    
    # Rotation
    rotation_strategy: ProxyRotationStrategy = ProxyRotationStrategy.ROUND_ROBIN
    rotation_interval: int = 1  # rotate every N requests
    
    # Authentication
    proxy_auth: Optional[Dict[str, str]] = None  # {"username": "user", "password": "pass"}
    
    # Blacklisting
    blacklist_on_failure: bool = True
    blacklist_threshold: int = 3  # blacklist after N consecutive failures
    blacklist_timeout: int = 300  # seconds to keep in blacklist
    
    # Protocol
    default_protocol: str = "http"  # http, https, socks5
    
    # DNS
    dns_over_https: bool = False
    dns_servers: List[str] = field(default_factory=lambda: ["1.1.1.1", "8.8.8.8"])
    
    # Geo targeting
    geo_targeting: bool = False
    preferred_countries: List[str] = field(default_factory=list)
    
    # Health checking
    health_check_interval: int = 60  # seconds
    health_check_url: str = "https://httpbin.org/ip"


@dataclass 
class BrowserConfig:
    """Browser automation configuration"""
    # Browser selection
    browser_type: BrowserType = BrowserType.CHROMIUM
    use_installed_chrome: bool = False
    chrome_executable: Optional[Path] = None
    
    # Browser settings
    headless: bool = True
    user_agent: Optional[str] = None
    viewport_width: int = 1280
    viewport_height: int = 720
    device_scale_factor: int = 1
    
    # Behavior
    disable_resources: bool = True  # Disable loading of images, stylesheets, etc.
    block_domains: List[str] = field(default_factory=lambda: [
        "google-analytics.com",
        "googlesyndication.com", 
        "doubleclick.net",
        "facebook.com",
        "twitter.com",
    ])
    
    # Performance
    timeout: int = 60  # seconds
    network_idle_timeout: int = 5000  # milliseconds
    
    # Stealth
    stealth_mode: bool = False
    bypass_cf: bool = True
    bypass_cf_challenge: bool = True
    solve_cloudflare: bool = True
    google_search: bool = False
    
    # Session management
    persistent_context: bool = False
    user_data_dir: Optional[Path] = None
    
    # Pooling
    max_pages: int = 10  # Maximum concurrent browser pages
    page_reuse: bool = True  # Reuse pages when possible
    
    # Cookies
    accept_cookies: bool = True
    cookie_storage: bool = True
    cookie_file: Optional[Path] = None


@dataclass
class CacheConfig:
    """Caching configuration"""
    # Global enable/disable
    enabled: bool = True
    
    # Backend selection
    backend: CacheBackend = CacheBackend.MULTI
    
    # Memory cache
    memory_cache_enabled: bool = True
    memory_cache_size: int = 1000  # Maximum entries
    memory_cache_ttl: int = 300  # seconds
    
    # Disk cache
    disk_cache_enabled: bool = True
    disk_cache_dir: Path = field(default_factory=lambda: Path("./.cache/disk"))
    disk_cache_max_size: int = 1024 * 1024 * 100  # 100 MB
    disk_cache_ttl: int = 86400  # 24 hours
    
    # Database cache
    database_cache_enabled: bool = False
    database_url: str = "sqlite:///.cache/cache.db"
    database_cache_ttl: int = 604800  # 7 days
    
    # Request caching
    cache_requests: bool = True
    cache_responses: bool = True
    cache_selectors: bool = True  # Cache compiled CSS/XPath selectors
    
    # Invalidation
    auto_invalidate: bool = True
    invalidation_patterns: List[str] = field(default_factory=list)
    
    # Compression
    compress_cache: bool = False


@dataclass
class AIConfig:
    """AI integration configuration"""
    # Global enable/disable
    enabled: bool = False
    
    # Provider
    provider: AIProvider = AIProvider.NONE
    
    # API keys (sensitive - should be loaded from env or secure storage)
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None
    
    # Model selection
    model: str = "gpt-4o-mini"  # Default model
    temperature: float = 0.7
    max_tokens: int = 4096
    
    # Content extraction
    ai_extraction_enabled: bool = True
    extraction_prompt: str = "Extract the main content and key information from this webpage."
    
    # Content analysis
    sentiment_analysis: bool = False
    keyword_extraction: bool = False
    entity_recognition: bool = False
    
    # Sanitization
    ai_sanitization: bool = False
    sanitization_prompt: str = "Clean this text for AI processing, remove ads, navigation, and boilerplate."
    
    # Rate limiting
    rate_limit_requests: int = 10
    rate_limit_period: int = 60  # seconds
    
    # Caching
    cache_ai_responses: bool = True
    
    # Local LLM (for LOCAL provider)
    local_model_path: Optional[Path] = None
    local_model_config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LoggingConfig:
    """Logging configuration"""
    # Global settings
    enabled: bool = True
    level: LogLevel = LogLevel.INFO
    
    # Console logging
    console_enabled: bool = True
    console_level: LogLevel = LogLevel.INFO
    console_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    console_date_format: str = "%Y-%m-%d %H:%M:%S"
    
    # File logging
    file_enabled: bool = True
    log_file: Path = field(default_factory=lambda: Path("logs/scrapper.log"))
    file_level: LogLevel = LogLevel.DEBUG
    file_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s"
    file_date_format: str = "%Y-%m-%d %H:%M:%S"
    
    # Rotation
    max_file_size: int = 10 * 1024 * 1024  # 10 MB
    backup_count: int = 5
    
    # JSON logging
    json_enabled: bool = False
    json_log_file: Path = field(default_factory=lambda: Path("logs/scrapper.json.log"))
    
    # Performance logging
    performance_logging: bool = False
    slow_request_threshold: float = 5.0  # seconds
    
    # Sensitive data
    mask_sensitive_data: bool = True
    sensitive_patterns: List[str] = field(default_factory=lambda: [
        r"password[\s=:]?[\s\"']*[^\s\"'\n]+",
        r"api[_-]?key[\s=:]?[\s\"']*[^\s\"'\n]+",
        r"token[\s=:]?[\s\"']*[^\s\"'\n]+",
        r"secret[\s=:]?[\s\"']*[^\s\"'\n]+",
    ])


@dataclass
class PerformanceConfig:
    """Performance configuration"""
    # Concurrency
    max_concurrency: int = 10
    concurrent_requests: int = 5
    concurrent_browsers: int = 3
    
    # Rate limiting
    rate_limit_enabled: bool = False
    rate_limit_requests: int = 100
    rate_limit_period: int = 60  # seconds
    rate_limit_per_domain: bool = True
    
    # Throttling
    request_delay: float = 0.0  # seconds between requests
    domain_delay: Dict[str, float] = field(default_factory=dict)  # per-domain delays
    
    # Timeouts
    task_timeout: int = 300  # seconds
    shutdown_timeout: int = 30  # seconds
    
    # Resource limits
    max_memory_usage: Optional[int] = None  # MB
    max_cpu_usage: Optional[float] = None  # percentage
    
    # Cleanup
    cleanup_interval: int = 300  # seconds
    temp_file_cleanup: bool = True
    cache_cleanup: bool = True
    
    # Monitoring
    metrics_enabled: bool = False
    metrics_port: int = 9090
    
    # Adaptive performance
    adaptive_concurrency: bool = True
    auto_tune: bool = False


@dataclass
class PlatformConfig:
    """Platform-specific configuration"""
    # Platform detection
    auto_detect: bool = True
    
    # Platform-specific settings
    platform_settings: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # User agents per platform
    platform_user_agents: Dict[str, str] = field(default_factory=dict)
    
    # Custom selectors
    custom_selectors: Dict[str, Dict[str, str]] = field(default_factory=dict)
    
    # Platform timeouts
    platform_timeouts: Dict[str, int] = field(default_factory=lambda: {
        "tiktok": 45,
        "instagram": 45, 
        "snapchat": 60,
        "youtube": 30,
        "twitter": 30,
    })
    
    # Rate limits per platform
    platform_rate_limits: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {
        "tiktok": {"requests": 20, "period": 60},
        "instagram": {"requests": 30, "period": 60},
        "snapchat": {"requests": 10, "period": 60},
        "youtube": {"requests": 50, "period": 60},
        "twitter": {"requests": 90, "period": 15 * 60},  # 90 requests per 15 minutes
    })


@dataclass
class NotificationConfig:
    """Notification configuration"""
    # Global settings
    enabled: bool = False
    
    # Email notifications
    email_enabled: bool = False
    email_smtp_server: str = ""
    email_smtp_port: int = 587
    email_username: str = ""
    email_password: str = ""
    email_from: str = ""
    email_to: List[str] = field(default_factory=list)
    email_on_error: bool = True
    email_on_completion: bool = False
    
    # Webhook notifications
    webhook_enabled: bool = False
    webhook_urls: List[str] = field(default_factory=list)
    webhook_events: List[str] = field(default_factory=lambda: [
        "error", "completion", "start", "progress"
    ])
    
    # Slack notifications
    slack_enabled: bool = False
    slack_webhook_url: str = ""
    slack_channel: str = "#scraper"
    
    # Discord notifications
    discord_enabled: bool = False
    discord_webhook_url: str = ""
    
    # Desktop notifications
    desktop_enabled: bool = False


@dataclass
class SecurityConfig:
    """Security configuration"""
    # Request validation
    validate_urls: bool = True
    allowed_schemes: List[str] = field(default_factory=lambda: ["http", "https"])
    blocked_domains: List[str] = field(default_factory=list)
    allowed_domains: List[str] = field(default_factory=list)
    
    # Rate limiting protection
    enable_rate_limit_protection: bool = True
    rate_limit_ban_time: int = 300  # seconds
    
    # IP restrictions
    restrict_to_ip: Optional[str] = None
    restrict_to_network: Optional[str] = None
    
    # Sandboxing
    sandbox_enabled: bool = False
    sandbox_timeout: int = 10  # seconds
    
    # Content security
    scan_for_malware: bool = False
    block_suspicious_content: bool = True
    
    # API keys and secrets
    api_key_required: bool = False
    api_keys: Dict[str, str] = field(default_factory=dict)


@dataclass
class RetryConfig:
    """Retry configuration for failed requests"""
    # Global settings
    enabled: bool = True
    max_retries: int = 3
    
    # Backoff strategy
    strategy: str = "exponential_backoff"  # exponential_backoff, linear_backoff, constant_backoff, adaptive
    
    # Base delays
    base_delay: float = 1.0  # Base delay in seconds
    max_delay: float = 60.0  # Maximum delay in seconds
    min_delay: float = 0.1  # Minimum delay in seconds
    
    # Exponential backoff settings
    exponential_base: float = 2.0  # Multiplier for exponential backoff
    
    # Linear backoff settings
    linear_increment: float = 1.0  # Increment for linear backoff
    
    # Jitter settings
    use_jitter: bool = True
    jitter_factor: float = 0.1  # Jitter factor (0.0 to 1.0)
    
    # Retry conditions
    retry_on_network_error: bool = True
    retry_on_timeout: bool = True
    retry_on_http_429: bool = True  # Too Many Requests
    retry_on_http_5xx: bool = True  # Server errors
    retry_on_connection_error: bool = True
    retry_on_ssl_error: bool = True
    retry_on_proxy_error: bool = True
    
    # HTTP status codes to retry
    retryable_status_codes: List[int] = field(default_factory=lambda: [
        408,  # Request Timeout
        429,  # Too Many Requests
        500,  # Internal Server Error
        502,  # Bad Gateway
        503,  # Service Unavailable
        504,  # Gateway Timeout
    ])
    
    # Error types to retry
    retryable_exceptions: List[str] = field(default_factory=lambda: [
        "ConnectionError",
        "TimeoutError", 
        "OSError",
        "asyncio.TimeoutError",
        "aiohttp.ClientError",
        "requests.exceptions.ConnectionError",
        "requests.exceptions.Timeout",
    ])
    
    # Retry-After header
    respect_retry_after: bool = True
    max_retry_after: float = 300.0  # Maximum Retry-After value to respect
    
    # Per-domain retry settings
    domain_specific_retries: Dict[str, Dict[str, Any]] = field(default_factory=dict)


# =============================================================================
# Main Configuration Class
# =============================================================================

@dataclass
class Configuration:
    """Main configuration class containing all settings"""
    
    general: GeneralConfig = field(default_factory=GeneralConfig)
    http: HTTPConfig = field(default_factory=HTTPConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    platform: PlatformConfig = field(default_factory=PlatformConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    
    # Source tracking
    source: ConfigSource = ConfigSource.DEFAULT
    sources: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        """Post-initialization processing"""
        # Ensure all paths are resolved
        self.general.base_dir = self.general.base_dir.resolve()
        
        # Validate configuration
        validate_config(self)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary"""
        result = {}
        for field_name, field_value in self.__dict__.items():
            if hasattr(field_value, 'to_dict'):
                result[field_name] = field_value.to_dict()
            elif hasattr(field_value, '__dict__'):
                result[field_name] = field_value.__dict__
            else:
                result[field_name] = field_value
        return result
    
    def to_env_dict(self) -> Dict[str, str]:
        """Convert configuration to environment variable format"""
        result = {}
        
        def flatten_config(obj: Any, prefix: str = ""):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    new_prefix = f"{prefix}_{key}" if prefix else key
                    flatten_config(value, new_prefix.upper())
            elif hasattr(obj, '__dict__'):
                for key, value in obj.__dict__.items():
                    if not key.startswith('_'):
                        new_prefix = f"{prefix}_{key}" if prefix else key
                        flatten_config(value, new_prefix.upper())
            else:
                if prefix:
                    result[prefix] = str(obj)
        
        flatten_config(self)
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Configuration":
        """Create configuration from dictionary"""
        config = cls()
        
        for section_name, section_data in data.items():
            if hasattr(config, section_name):
                section_obj = getattr(config, section_name)
                if hasattr(section_obj, 'from_dict'):
                    setattr(config, section_name, section_obj.from_dict(section_data))
                else:
                    for key, value in section_data.items():
                        if hasattr(section_obj, key):
                            setattr(section_obj, key, value)
        
        return config
    
    def merge(self, other: "Configuration") -> "Configuration":
        """Merge another configuration into this one (other takes precedence)"""
        result = Configuration()
        
        for field_name in self.__dataclass_fields__:
            self_value = getattr(self, field_name)
            other_value = getattr(other, field_name)
            
            if hasattr(self_value, '__dict__') and hasattr(other_value, '__dict__'):
                # Merge nested dataclasses
                merged_value = self_value
                for key, value in other_value.__dict__.items():
                    if hasattr(merged_value, key):
                        setattr(merged_value, key, value)
                setattr(result, field_name, merged_value)
            else:
                # Use other value if it's not default
                if other_value != getattr(Configuration(), field_name):
                    setattr(result, field_name, other_value)
                else:
                    setattr(result, field_name, self_value)
        
        return result


# =============================================================================
# Pydantic Models for API/CLI Input Validation
# =============================================================================

class ProxyModel(BaseModel):
    """Proxy configuration model for API input"""
    url: str = Field(..., description="Proxy URL (e.g., http://user:pass@host:port)")
    protocol: str = Field(default="http", description="Proxy protocol")
    username: Optional[str] = Field(default=None, description="Proxy username")
    password: Optional[str] = Field(default=None, description="Proxy password")
    
    @validator('url')
    def validate_url(cls, v):
        if not v:
            raise ValueError("Proxy URL cannot be empty")
        # Basic URL validation
        if not re.match(r'^[a-zA-Z]+://', v):
            raise ValueError("Proxy URL must include scheme (e.g., http://)")
        return v


class BrowserSettingsModel(BaseModel):
    """Browser settings model"""
    browser_type: BrowserType = Field(default=BrowserType.CHROMIUM, description="Browser type")
    headless: bool = Field(default=True, description="Run browser in headless mode")
    timeout: int = Field(default=60, ge=5, le=300, description="Browser timeout in seconds")
    viewport_width: int = Field(default=1280, ge=320, le=3840, description="Browser viewport width")
    viewport_height: int = Field(default=720, ge=240, le=2160, description="Browser viewport height")
    user_agent: Optional[str] = Field(default=None, description="Custom user agent")
    
    class Config:
        use_enum_values = True


class AISettingsModel(BaseModel):
    """AI settings model"""
    enabled: bool = Field(default=False, description="Enable AI features")
    provider: AIProvider = Field(default=AIProvider.NONE, description="AI provider")
    model: str = Field(default="gpt-4o-mini", description="AI model to use")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="AI temperature")
    max_tokens: int = Field(default=4096, ge=1, le=32768, description="Maximum tokens")
    
    class Config:
        use_enum_values = True


class ScrapeRequestModel(BaseModel):
    """Model for scrape request"""
    url: str = Field(..., description="URL to scrape")
    mode: FetchMode = Field(default=FetchMode.AUTO, description="Fetching mode")
    selector: Optional[str] = Field(default=None, description="CSS or XPath selector")
    format: str = Field(default="json", description="Output format (json, html, text, markdown)")
    
    # Browser settings
    browser: Optional[BrowserSettingsModel] = Field(default=None, description="Browser settings")
    
    # AI settings
    ai: Optional[AISettingsModel] = Field(default=None, description="AI settings")
    
    # Proxy settings
    proxy: Optional[ProxyModel] = Field(default=None, description="Proxy settings")
    
    class Config:
        use_enum_values = True


class BatchScrapeRequestModel(BaseModel):
    """Model for batch scrape request"""
    urls: List[str] = Field(..., min_items=1, description="List of URLs to scrape")
    concurrency: int = Field(default=5, ge=1, le=50, description="Number of concurrent requests")
    mode: FetchMode = Field(default=FetchMode.AUTO, description="Fetching mode")
    
    class Config:
        use_enum_values = True