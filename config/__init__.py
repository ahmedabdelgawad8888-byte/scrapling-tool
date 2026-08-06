"""
Configuration Module for Scrapper Magic
====================================
Centralized configuration management with support for multiple sources:
- Environment variables
- Configuration files (YAML, JSON, TOML)
- Command-line arguments
- Default values
"""

from .settings import Settings, get_settings
from .schemas import (
    GeneralConfig,
    HTTPConfig,
    ProxyConfig,
    BrowserConfig,
    CacheConfig,
    AIConfig,
    LoggingConfig,
    PerformanceConfig,
    RetryConfig,
    Configuration,
)
from .validators import validate_config, ConfigValidationError

__all__ = [
    "Settings",
    "get_settings",
    "GeneralConfig",
    "HTTPConfig", 
    "ProxyConfig",
    "BrowserConfig",
    "CacheConfig",
    "AIConfig",
    "LoggingConfig",
    "PerformanceConfig",
    "RetryConfig",
    "Configuration",
    "validate_config",
    "ConfigValidationError",
]

# Convenience function for easy access
def reload_settings():
    """Reload settings from all sources"""
    return get_settings(reload=True)

def reset_settings():
    """Reset settings to defaults"""
    return get_settings(reset=True)