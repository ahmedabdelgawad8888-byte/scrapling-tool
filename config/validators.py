"""
Configuration Validators
=======================
Validation functions and utilities for configuration values.
"""

from __future__ import annotations

import re
import socket
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from dataclasses import fields

from .schemas import (
    Configuration,
    GeneralConfig,
    HTTPConfig,
    ProxyConfig,
    BrowserConfig,
    CacheConfig,
    AIConfig,
    LoggingConfig,
    PerformanceConfig,
    PlatformConfig,
    NotificationConfig,
    SecurityConfig,
    FetchMode,
    ProxyRotationStrategy,
    CacheBackend,
    AIProvider,
    BrowserType,
    LogLevel,
)


class ConfigValidationError(Exception):
    """Configuration validation error"""
    
    def __init__(self, message: str, errors: Optional[List[str]] = None):
        super().__init__(message)
        self.errors = errors or []
    
    def __str__(self) -> str:
        if self.errors:
            error_list = "\n  - ".join(self.errors)
            return f"{super().__str__()}\n  - {error_list}"
        return super().__str__()


class ValidationResult:
    """Result of configuration validation"""
    
    def __init__(self, is_valid: bool = True, errors: Optional[List[str]] = None):
        self.is_valid = is_valid
        self.errors = errors or []
    
    def add_error(self, error: str) -> None:
        """Add an error message"""
        self.is_valid = False
        self.errors.append(error)
    
    def merge(self, other: ValidationResult) -> ValidationResult:
        """Merge another validation result into this one"""
        self.is_valid = self.is_valid and other.is_valid
        self.errors.extend(other.errors)
        return self
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "error_count": len(self.errors)
        }
    
    def __bool__(self) -> bool:
        return self.is_valid
    
    def __str__(self) -> str:
        if self.is_valid:
            return "Configuration is valid"
        return f"Configuration is invalid: {len(self.errors)} errors"


# =============================================================================
# Individual Field Validators
# =============================================================================

def validate_path(value: Any, field_name: str, must_exist: bool = False) -> Optional[str]:
    """
    Validate a path value.
    
    Args:
        value: Value to validate
        field_name: Name of the field being validated
        must_exist: Whether the path must exist
        
    Returns:
        Error message if invalid, None otherwise
    """
    if value is None:
        return None
    
    try:
        path = Path(value)
    except (TypeError, ValueError):
        return f"{field_name} must be a valid path string or Path object"
    
    if must_exist and not path.exists():
        return f"{field_name} path does not exist: {path}"
    
    return None


def validate_url(value: Any, field_name: str) -> Optional[str]:
    """
    Validate a URL value.
    
    Args:
        value: Value to validate
        field_name: Name of the field being validated
        
    Returns:
        Error message if invalid, None otherwise
    """
    if value is None:
        return None
    
    try:
        parsed = urllib.parse.urlparse(str(value))
        if not parsed.scheme:
            return f"{field_name} must have a valid scheme (http, https, etc.)"
        if not parsed.netloc:
            return f"{field_name} must have a valid network location"
    except (ValueError, AttributeError):
        return f"{field_name} must be a valid URL"
    
    return None


def validate_url_list(value: Any, field_name: str) -> Optional[str]:
    """
    Validate a list of URLs.
    
    Args:
        value: Value to validate
        field_name: Name of the field being validated
        
    Returns:
        Error message if invalid, None otherwise
    """
    if value is None:
        return None
    
    if not isinstance(value, list):
        return f"{field_name} must be a list"
    
    for i, url in enumerate(value):
        error = validate_url(url, f"{field_name}[{i}]")
        if error:
            return error
    
    return None


def validate_positive_int(value: Any, field_name: str, min_value: int = 1) -> Optional[str]:
    """
    Validate a positive integer value.
    
    Args:
        value: Value to validate
        field_name: Name of the field being validated
        min_value: Minimum allowed value
        
    Returns:
        Error message if invalid, None otherwise
    """
    if value is None:
        return None
    
    if not isinstance(value, int):
        return f"{field_name} must be an integer"
    
    if value < min_value:
        return f"{field_name} must be at least {min_value}"
    
    return None


def validate_non_negative_int(value: Any, field_name: str) -> Optional[str]:
    """
    Validate a non-negative integer value.
    
    Args:
        value: Value to validate
        field_name: Name of the field being validated
        
    Returns:
        Error message if invalid, None otherwise
    """
    return validate_positive_int(value, field_name, 0)


def validate_range(value: Any, field_name: str, min_val: float, max_val: float) -> Optional[str]:
    """
    Validate a value within a range.
    
    Args:
        value: Value to validate
        field_name: Name of the field being validated
        min_val: Minimum allowed value
        max_val: Maximum allowed value
        
    Returns:
        Error message if invalid, None otherwise
    """
    if value is None:
        return None
    
    if not isinstance(value, (int, float)):
        return f"{field_name} must be a number"
    
    if value < min_val or value > max_val:
        return f"{field_name} must be between {min_val} and {max_val}"
    
    return None


def validate_boolean(value: Any, field_name: str) -> Optional[str]:
    """
    Validate a boolean value.
    
    Args:
        value: Value to validate
        field_name: Name of the field being validated
        
    Returns:
        Error message if invalid, None otherwise
    """
    if value is None:
        return None
    
    if not isinstance(value, bool):
        return f"{field_name} must be a boolean"
    
    return None


def validate_string_list(value: Any, field_name: str, min_length: int = 0) -> Optional[str]:
    """
    Validate a list of strings.
    
    Args:
        value: Value to validate
        field_name: Name of the field being validated
        min_length: Minimum length of the list
        
    Returns:
        Error message if invalid, None otherwise
    """
    if value is None:
        return None
    
    if not isinstance(value, list):
        return f"{field_name} must be a list"
    
    if len(value) < min_length:
        return f"{field_name} must have at least {min_length} items"
    
    for i, item in enumerate(value):
        if not isinstance(item, str):
            return f"{field_name}[{i}] must be a string"
    
    return None


def validate_dict(value: Any, field_name: str) -> Optional[str]:
    """
    Validate a dictionary value.
    
    Args:
        value: Value to validate
        field_name: Name of the field being validated
        
    Returns:
        Error message if invalid, None otherwise
    """
    if value is None:
        return None
    
    if not isinstance(value, dict):
        return f"{field_name} must be a dictionary"
    
    return None


def validate_enum(value: Any, field_name: str, enum_type: type) -> Optional[str]:
    """
    Validate an enum value.
    
    Args:
        value: Value to validate
        field_name: Name of the field being validated
        enum_type: Enum type to validate against
        
    Returns:
        Error message if invalid, None otherwise
    """
    if value is None:
        return None
    
    if not isinstance(value, enum_type):
        try:
            # Try to convert string to enum
            if isinstance(value, str):
                enum_value = enum_type(value.upper())
                return None
        except (ValueError, KeyError):
            pass
        
        valid_values = [e.value for e in enum_type]
        return f"{field_name} must be one of: {', '.join(valid_values)}"
    
    return None


def validate_regex(value: Any, field_name: str, pattern: str, description: str = "") -> Optional[str]:
    """
    Validate a string against a regex pattern.
    
    Args:
        value: Value to validate
        field_name: Name of the field being validated
        pattern: Regex pattern to match against
        description: Description of the expected format
        
    Returns:
        Error message if invalid, None otherwise
    """
    if value is None:
        return None
    
    if not isinstance(value, str):
        return f"{field_name} must be a string"
    
    if not re.match(pattern, value):
        desc = f" matching {description}" if description else ""
        return f"{field_name} must be a valid{desc} format"
    
    return None


def validate_host_port(value: Any, field_name: str) -> Optional[str]:
    """
    Validate a host:port value.
    
    Args:
        value: Value to validate
        field_name: Name of the field being validated
        
    Returns:
        Error message if invalid, None otherwise
    """
    if value is None:
        return None
    
    try:
        # Handle both "host:port" and "host" formats
        parts = str(value).rsplit(":", 1)
        host = parts[0]
        
        if not host:
            return f"{field_name} must have a valid host"
        
        # Validate host
        try:
            socket.gethostbyname(host)
        except socket.gaierror:
            # Try as IP address
            try:
                socket.inet_aton(host)
            except socket.error:
                return f"{field_name} has invalid host: {host}"
        
        # Validate port if present
        if len(parts) == 2:
            port_str = parts[1]
            try:
                port = int(port_str)
                if not (0 < port <= 65535):
                    return f"{field_name} has invalid port: {port}"
            except ValueError:
                return f"{field_name} has invalid port: {port_str}"
    
    except Exception as e:
        return f"{field_name} is invalid: {e}"
    
    return None


def validate_proxy_url(value: Any, field_name: str) -> Optional[str]:
    """
    Validate a proxy URL.
    
    Args:
        value: Value to validate
        field_name: Name of the field being validated
        
    Returns:
        Error message if invalid, None otherwise
    """
    if value is None:
        return None
    
    try:
        parsed = urllib.parse.urlparse(str(value))
        
        if not parsed.scheme:
            return f"{field_name} must have a scheme (http, https, socks5)"
        
        if parsed.scheme.lower() not in ('http', 'https', 'socks5', 'socks4', 'ftp'):
            return f"{field_name} has unsupported scheme: {parsed.scheme}"
        
        if not parsed.netloc:
            return f"{field_name} must have a network location"
        
    except (ValueError, AttributeError):
        return f"{field_name} must be a valid proxy URL"
    
    return None


# =============================================================================
# Section-specific Validators
# =============================================================================

def validate_general_config(config: GeneralConfig) -> ValidationResult:
    """
    Validate GeneralConfig section.
    
    Args:
        config: GeneralConfig instance to validate
        
    Returns:
        ValidationResult with any errors found
    """
    result = ValidationResult()
    
    # Validate paths
    paths_to_check = [
        ('base_dir', False),
        ('data_dir', False),
        ('cache_dir', False),
        ('logs_dir', False),
        ('output_dir', False),
        ('temp_dir', False),
    ]
    
    for field_name, must_exist in paths_to_check:
        path_value = getattr(config, field_name)
        error = validate_path(path_value, field_name)
        if error:
            result.add_error(error)
    
    return result


def validate_http_config(config: HTTPConfig) -> ValidationResult:
    """
    Validate HTTPConfig section.
    
    Args:
        config: HTTPConfig instance to validate
        
    Returns:
        ValidationResult with any errors found
    """
    result = ValidationResult()
    
    # Validate timeouts
    timeout_fields = ['timeout', 'connect_timeout', 'read_timeout', 'write_timeout']
    for field_name in timeout_fields:
        value = getattr(config, field_name)
        error = validate_positive_int(value, field_name, 1)
        if error:
            result.add_error(error)
    
    # Validate max redirects
    error = validate_positive_int(config.max_redirects, 'max_redirects', 1)
    if error:
        result.add_error(error)
    
    # Validate retry settings
    error = validate_positive_int(config.max_retries, 'max_retries', 0)
    if error:
        result.add_error(error)
    
    error = validate_range(config.retry_backoff, 'retry_backoff', 1.0, 10.0)
    if error:
        result.add_error(error)
    
    # Validate impersonate value
    valid_browsers = ['chrome', 'firefox', 'safari', 'edge']
    if config.impersonate and config.impersonate.lower() not in valid_browsers:
        result.add_error(f"impersonate must be one of: {', '.join(valid_browsers)}")
    
    return result


def validate_proxy_config(config: ProxyConfig) -> ValidationResult:
    """
    Validate ProxyConfig section.
    
    Args:
        config: ProxyConfig instance to validate
        
    Returns:
        ValidationResult with any errors found
    """
    result = ValidationResult()
    
    # Validate proxy URLs
    error = validate_string_list(config.proxies, 'proxies')
    if error:
        result.add_error(error)
    else:
        for i, proxy in enumerate(config.proxies):
            error = validate_proxy_url(proxy, f"proxies[{i}]")
            if error:
                result.add_error(error)
    
    # Validate proxy files
    error = validate_string_list(config.proxy_files, 'proxy_files')
    if error:
        result.add_error(error)
    
    # Validate rotation settings
    error = validate_positive_int(config.rotation_interval, 'rotation_interval', 1)
    if error:
        result.add_error(error)
    
    # Validate enum
    error = validate_enum(config.rotation_strategy, 'rotation_strategy', ProxyRotationStrategy)
    if error:
        result.add_error(error)
    
    # Validate blacklist settings
    error = validate_positive_int(config.blacklist_threshold, 'blacklist_threshold', 1)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.blacklist_timeout, 'blacklist_timeout', 1)
    if error:
        result.add_error(error)
    
    # Validate health check interval
    error = validate_positive_int(config.health_check_interval, 'health_check_interval', 1)
    if error:
        result.add_error(error)
    
    # Validate URL
    error = validate_url(config.health_check_url, 'health_check_url')
    if error:
        result.add_error(error)
    
    return result


def validate_browser_config(config: BrowserConfig) -> ValidationResult:
    """
    Validate BrowserConfig section.
    
    Args:
        config: BrowserConfig instance to validate
        
    Returns:
        ValidationResult with any errors found
    """
    result = ValidationResult()
    
    # Validate enum
    error = validate_enum(config.browser_type, 'browser_type', BrowserType)
    if error:
        result.add_error(error)
    
    # Validate timeouts
    error = validate_positive_int(config.timeout, 'timeout', 5)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.network_idle_timeout, 'network_idle_timeout', 100)
    if error:
        result.add_error(error)
    
    # Validate viewport dimensions
    error = validate_positive_int(config.viewport_width, 'viewport_width', 320)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.viewport_height, 'viewport_height', 240)
    if error:
        result.add_error(error)
    
    error = validate_range(config.device_scale_factor, 'device_scale_factor', 0.1, 5.0)
    if error:
        result.add_error(error)
    
    # Validate pooling settings
    error = validate_positive_int(config.max_pages, 'max_pages', 1)
    if error:
        result.add_error(error)
    
    # Validate user data dir if specified
    if config.user_data_dir:
        error = validate_path(config.user_data_dir, 'user_data_dir')
        if error:
            result.add_error(error)
    
    return result


def validate_cache_config(config: CacheConfig) -> ValidationResult:
    """
    Validate CacheConfig section.
    
    Args:
        config: CacheConfig instance to validate
        
    Returns:
        ValidationResult with any errors found
    """
    result = ValidationResult()
    
    # Validate enum
    error = validate_enum(config.backend, 'backend', CacheBackend)
    if error:
        result.add_error(error)
    
    # Validate cache sizes
    error = validate_positive_int(config.memory_cache_size, 'memory_cache_size', 1)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.memory_cache_ttl, 'memory_cache_ttl', 1)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.disk_cache_max_size, 'disk_cache_max_size', 1)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.disk_cache_ttl, 'disk_cache_ttl', 1)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.database_cache_ttl, 'database_cache_ttl', 1)
    if error:
        result.add_error(error)
    
    # Validate disk cache directory
    if config.disk_cache_enabled:
        error = validate_path(config.disk_cache_dir, 'disk_cache_dir')
        if error:
            result.add_error(error)
    
    return result


def validate_ai_config(config: AIConfig) -> ValidationResult:
    """
    Validate AIConfig section.
    
    Args:
        config: AIConfig instance to validate
        
    Returns:
        ValidationResult with any errors found
    """
    result = ValidationResult()
    
    # Validate enum
    error = validate_enum(config.provider, 'provider', AIProvider)
    if error:
        result.add_error(error)
    
    # Validate model settings
    if config.temperature:
        error = validate_range(config.temperature, 'temperature', 0.0, 2.0)
        if error:
            result.add_error(error)
    
    error = validate_positive_int(config.max_tokens, 'max_tokens', 1)
    if error:
        result.add_error(error)
    
    # Validate rate limit
    error = validate_positive_int(config.rate_limit_requests, 'rate_limit_requests', 1)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.rate_limit_period, 'rate_limit_period', 1)
    if error:
        result.add_error(error)
    
    return result


def validate_logging_config(config: LoggingConfig) -> ValidationResult:
    """
    Validate LoggingConfig section.
    
    Args:
        config: LoggingConfig instance to validate
        
    Returns:
        ValidationResult with any errors found
    """
    result = ValidationResult()
    
    # Validate enum
    error = validate_enum(config.level, 'level', LogLevel)
    if error:
        result.add_error(error)
    
    error = validate_enum(config.console_level, 'console_level', LogLevel)
    if error:
        result.add_error(error)
    
    error = validate_enum(config.file_level, 'file_level', LogLevel)
    if error:
        result.add_error(error)
    
    # Validate file sizes
    error = validate_positive_int(config.max_file_size, 'max_file_size', 1)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.backup_count, 'backup_count', 0)
    if error:
        result.add_error(error)
    
    # Validate threshold
    error = validate_range(config.slow_request_threshold, 'slow_request_threshold', 0.0, 60.0)
    if error:
        result.add_error(error)
    
    return result


def validate_performance_config(config: PerformanceConfig) -> ValidationResult:
    """
    Validate PerformanceConfig section.
    
    Args:
        config: PerformanceConfig instance to validate
        
    Returns:
        ValidationResult with any errors found
    """
    result = ValidationResult()
    
    # Validate concurrency settings
    error = validate_positive_int(config.max_concurrency, 'max_concurrency', 1)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.concurrent_requests, 'concurrent_requests', 1)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.concurrent_browsers, 'concurrent_browsers', 1)
    if error:
        result.add_error(error)
    
    # Validate rate limit settings
    error = validate_positive_int(config.rate_limit_requests, 'rate_limit_requests', 1)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.rate_limit_period, 'rate_limit_period', 1)
    if error:
        result.add_error(error)
    
    # Validate timeouts
    error = validate_positive_int(config.task_timeout, 'task_timeout', 1)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.shutdown_timeout, 'shutdown_timeout', 1)
    if error:
        result.add_error(error)
    
    # Validate cleanup interval
    error = validate_positive_int(config.cleanup_interval, 'cleanup_interval', 1)
    if error:
        result.add_error(error)
    
    # Validate request delay
    error = validate_non_negative_int(config.request_delay, 'request_delay')
    if error:
        result.add_error(error)
    
    # Validate resource limits
    if config.max_memory_usage is not None:
        error = validate_positive_int(config.max_memory_usage, 'max_memory_usage', 1)
        if error:
            result.add_error(error)
    
    if config.max_cpu_usage is not None:
        error = validate_range(config.max_cpu_usage, 'max_cpu_usage', 0.0, 100.0)
        if error:
            result.add_error(error)
    
    return result


def validate_platform_config(config: PlatformConfig) -> ValidationResult:
    """
    Validate PlatformConfig section.
    
    Args:
        config: PlatformConfig instance to validate
        
    Returns:
        ValidationResult with any errors found
    """
    result = ValidationResult()
    
    # Validate platform timeouts
    for platform, timeout in config.platform_timeouts.items():
        error = validate_positive_int(timeout, f"platform_timeouts.{platform}", 1)
        if error:
            result.add_error(error)
    
    # Validate platform rate limits
    for platform, rate_limit in config.platform_rate_limits.items():
        if not isinstance(rate_limit, dict):
            result.add_error(f"platform_rate_limits.{platform} must be a dictionary")
            continue
        
        if 'requests' in rate_limit:
            error = validate_positive_int(rate_limit['requests'], f"platform_rate_limits.{platform}.requests", 1)
            if error:
                result.add_error(error)
        
        if 'period' in rate_limit:
            error = validate_positive_int(rate_limit['period'], f"platform_rate_limits.{platform}.period", 1)
            if error:
                result.add_error(error)
    
    return result


def validate_security_config(config: SecurityConfig) -> ValidationResult:
    """
    Validate SecurityConfig section.
    
    Args:
        config: SecurityConfig instance to validate
        
    Returns:
        ValidationResult with any errors found
    """
    result = ValidationResult()
    
    # Validate URL lists
    error = validate_string_list(config.allowed_schemes, 'allowed_schemes')
    if error:
        result.add_error(error)
    
    error = validate_string_list(config.blocked_domains, 'blocked_domains')
    if error:
        result.add_error(error)
    
    error = validate_string_list(config.allowed_domains, 'allowed_domains')
    if error:
        result.add_error(error)
    
    # Validate timeouts
    error = validate_positive_int(config.rate_limit_ban_time, 'rate_limit_ban_time', 1)
    if error:
        result.add_error(error)
    
    error = validate_positive_int(config.sandbox_timeout, 'sandbox_timeout', 1)
    if error:
        result.add_error(error)
    
    return result


# =============================================================================
# Main Validation Function
# =============================================================================

def validate_config(config: Configuration) -> ValidationResult:
    """
    Validate the entire configuration.
    
    Args:
        config: Configuration instance to validate
        
    Returns:
        ValidationResult with all errors found
        
    Raises:
        ConfigValidationError: If validation fails (when strict=True)
    """
    result = ValidationResult()
    
    # Validate each section
    section_validators = [
        ('general', validate_general_config, config.general),
        ('http', validate_http_config, config.http),
        ('proxy', validate_proxy_config, config.proxy),
        ('browser', validate_browser_config, config.browser),
        ('cache', validate_cache_config, config.cache),
        ('ai', validate_ai_config, config.ai),
        ('logging', validate_logging_config, config.logging),
        ('performance', validate_performance_config, config.performance),
        ('platform', validate_platform_config, config.platform),
        ('security', validate_security_config, config.security),
    ]
    
    for section_name, validator, section_config in section_validators:
        section_result = validator(section_config)
        if not section_result.is_valid:
            for error in section_result.errors:
                result.add_error(f"[{section_name}] {error}")
    
    return result


def validate_config_strict(config: Configuration) -> bool:
    """
    Validate configuration with strict mode (raises exception on error).
    
    Args:
        config: Configuration instance to validate
        
    Returns:
        True if valid
        
    Raises:
        ConfigValidationError: If validation fails
    """
    result = validate_config(config)
    
    if not result.is_valid:
        raise ConfigValidationError(
            f"Configuration validation failed with {len(result.errors)} errors",
            result.errors
        )
    
    return True


# =============================================================================
# Convenience Functions
# =============================================================================

def get_validation_errors(config: Configuration) -> List[str]:
    """
    Get list of validation errors for a configuration.
    
    Args:
        config: Configuration instance to validate
        
    Returns:
        List of error messages
    """
    result = validate_config(config)
    return result.errors


def is_config_valid(config: Configuration) -> bool:
    """
    Check if configuration is valid.
    
    Args:
        config: Configuration instance to validate
        
    Returns:
        True if valid, False otherwise
    """
    result = validate_config(config)
    return result.is_valid


# =============================================================================
# Environment Variable Validators
# =============================================================================

def validate_environment() -> ValidationResult:
    """
    Validate current environment for running the scraper.
    
    Returns:
        ValidationResult with any environment issues found
    """
    import sys
    import platform
    
    result = ValidationResult()
    
    # Check Python version
    if sys.version_info < (3, 10):
        result.add_error(f"Python 3.10+ required, found {sys.version}")
    
    # Check platform
    if platform.system().lower() not in ['windows', 'linux', 'darwin']:
        result.add_error(f"Unsupported platform: {platform.system()}")
    
    # Check required packages
    required_packages = ['scrapling', 'requests', 'click']
    missing_packages = []
    
    try:
        import importlib
        for package in required_packages:
            try:
                importlib.import_module(package)
            except ImportError:
                missing_packages.append(package)
    except Exception as e:
        result.add_error(f"Error checking packages: {e}")
    
    if missing_packages:
        result.add_error(f"Missing required packages: {', '.join(missing_packages)}")
    
    return result
