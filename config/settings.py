"""
Settings Management
==================
Centralized settings management with support for multiple configuration sources.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import logging

import yaml

from .schemas import (
    Configuration, 
    ConfigSource,
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
)

# Type aliases
ConfigDict = Dict[str, Any]
EnvVarPrefix = str

# Logger
_logger = logging.getLogger(__name__)

# Global settings instance
_settings_instance: Optional[Configuration] = None

# Configuration file names to search for
CONFIG_FILE_NAMES = [
    "config.yaml",
    "config.yml", 
    "config.json",
    "scraper_config.yaml",
    "scraper_config.json",
    ".scraper.yaml",
    ".scraper.json",
]

# Environment variable prefixes
ENV_PREFIXES = ["SCRAPER_", "SCRAPING_", ""]


class ConfigError(Exception):
    """Configuration error exception"""
    pass


class ConfigFileNotFoundError(ConfigError):
    """Configuration file not found"""
    pass


class InvalidConfigError(ConfigError):
    """Invalid configuration error"""
    pass


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep merge two dictionaries, with override taking precedence.
    
    Args:
        base: Base dictionary
        override: Dictionary with override values
        
    Returns:
        Merged dictionary
    """
    result = base.copy()
    
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # Recursively merge nested dictionaries
            result[key] = _deep_merge(result[key], value)
        else:
            # Override with new value
            result[key] = value
    
    return result


def _parse_env_value(value: str) -> Any:
    """
    Parse environment variable value to appropriate Python type.
    
    Args:
        value: String value from environment
        
    Returns:
        Parsed value (bool, int, float, list, dict, or string)
    """
    if not value:
        return value
    
    # Handle booleans
    if value.lower() in ('true', 'yes', '1', 'on', 'enabled'):
        return True
    if value.lower() in ('false', 'no', '0', 'off', 'disabled'):
        return False
    
    # Handle integers
    try:
        return int(value)
    except ValueError:
        pass
    
    # Handle floats
    try:
        return float(value)
    except ValueError:
        pass
    
    # Handle JSON (lists, dicts, etc.)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    
    # Return as string
    return value


def _env_to_nested_dict(env_vars: Dict[str, str], prefix: str = "") -> Dict[str, Any]:
    """
    Convert flat environment variables to nested dictionary.
    
    Example:
        SCRAPER_HTTP_TIMEOUT=30 -> {"http": {"timeout": 30}}
        SCRAPER_PROXY_ENABLED=true -> {"proxy": {"enabled": True}}
    
    Args:
        env_vars: Flat environment variables dictionary
        prefix: Current prefix being processed
        
    Returns:
        Nested dictionary structure
    """
    result = {}
    
    for key, value in env_vars.items():
        if not key.startswith(prefix):
            continue
        
        # Remove prefix and split by underscores
        key_path = key[len(prefix):].lower().split("_")
        
        # Build nested structure
        current = result
        for i, part in enumerate(key_path[:-1]):
            if part not in current:
                current[part] = {}
            current = current[part]
        
        # Set final value
        final_key = key_path[-1]
        parsed_value = _parse_env_value(value)
        current[final_key] = parsed_value
    
    return result


def _load_env_config(prefixes: Optional[List[str]] = None) -> ConfigDict:
    """
    Load configuration from environment variables.
    
    Args:
        prefixes: List of environment variable prefixes to consider
        
    Returns:
        Configuration dictionary
    """
    prefixes = prefixes or ENV_PREFIXES
    env_config = {}
    
    for prefix in prefixes:
        # Get all environment variables that start with this prefix
        prefix_vars = {
            k: v for k, v in os.environ.items() 
            if k.startswith(prefix) and k != prefix
        }
        
        if prefix_vars:
            nested = _env_to_nested_dict(prefix_vars, prefix)
            env_config = _deep_merge(env_config, nested)
    
    return env_config


def _load_file_config(file_path: Path) -> ConfigDict:
    """
    Load configuration from a file.
    
    Args:
        file_path: Path to configuration file
        
    Returns:
        Configuration dictionary
        
    Raises:
        ConfigError: If file cannot be loaded or parsed
    """
    file_path = Path(file_path)
    
    if not file_path.exists():
        raise ConfigFileNotFoundError(f"Configuration file not found: {file_path}")
    
    content = file_path.read_text(encoding='utf-8')
    
    # Determine file type and parse accordingly
    suffix = file_path.suffix.lower()
    
    try:
        if suffix in ('.yaml', '.yml'):
            return yaml.safe_load(content) or {}
        elif suffix == '.json':
            return json.loads(content)
        elif suffix == '.toml':
            try:
                import tomllib
                return tomllib.loads(content)
            except ImportError:
                # Fallback for Python < 3.11
                import tomli
                return tomli.loads(content)
        else:
            # Try YAML first, then JSON
            try:
                return yaml.safe_load(content) or {}
            except yaml.YAMLError:
                return json.loads(content)
    except (yaml.YAMLError, json.JSONDecodeError, ImportError) as e:
        raise InvalidConfigError(f"Failed to parse {file_path}: {e}")


def _load_files_config(search_paths: Optional[List[Union[str, Path]]] = None) -> ConfigDict:
    """
    Load configuration from files in search paths.
    
    Args:
        search_paths: List of paths to search for configuration files
        
    Returns:
        Merged configuration from all found files
    """
    search_paths = search_paths or [
        Path.cwd(),
        Path.home(),
        Path("/etc/scrapper"),
    ]
    
    file_config = {}
    loaded_files = []
    
    for search_path in search_paths:
        search_path = Path(search_path)
        
        for config_file in CONFIG_FILE_NAMES:
            file_path = search_path / config_file
            
            if file_path.exists():
                try:
                    loaded_config = _load_file_config(file_path)
                    file_config = _deep_merge(file_config, loaded_config)
                    loaded_files.append(str(file_path))
                    _logger.info(f"Loaded configuration from {file_path}")
                except (ConfigFileNotFoundError, InvalidConfigError) as e:
                    _logger.warning(f"Failed to load {file_path}: {e}")
    
    if loaded_files:
        _logger.info(f"Loaded configuration files: {', '.join(loaded_files)}")
    else:
        _logger.debug("No configuration files found")
    
    return file_config


def _dict_to_dataclass(data: ConfigDict, dataclass_type: type) -> Any:
    """
    Convert dictionary to dataclass instance.
    
    Args:
        data: Dictionary data
        dataclass_type: Target dataclass type
        
    Returns:
        Dataclass instance
    """
    if not data:
        return dataclass_type()
    
    # Handle nested dataclasses
    field_types = {f.name: f.type for f in dataclass_type.__dataclass_fields__.values()}
    
    kwargs = {}
    for field_name, field_type in field_types.items():
        if field_name in data:
            field_data = data[field_name]
            
            # Handle nested dataclasses
            if hasattr(field_type, '__dataclass_fields__'):
                kwargs[field_name] = _dict_to_dataclass(field_data, field_type)
            # Handle Path types
            elif hasattr(field_type, '__origin__') and field_type.__origin__ is Union:
                args = field_type.__args__
                if Path in args:
                    kwargs[field_name] = Path(field_data) if field_data else None
                else:
                    kwargs[field_name] = field_data
            else:
                kwargs[field_name] = field_data
        elif hasattr(dataclass_type, field_name):
            # Use default value
            default_value = getattr(dataclass_type(), field_name)
            kwargs[field_name] = default_value
    
    return dataclass_type(**kwargs)


def _config_dict_to_configuration(config_dict: ConfigDict) -> Configuration:
    """
    Convert configuration dictionary to Configuration instance.
    
    Args:
        config_dict: Configuration dictionary
        
    Returns:
        Configuration instance
    """
    config = Configuration()
    
    # Convert each section
    for section_name in config.__dataclass_fields__:
        if section_name in config_dict:
            section_data = config_dict[section_name]
            section_type = config.__dataclass_fields__[section_name].type
            
            if hasattr(section_type, '__dataclass_fields__'):
                section_instance = _dict_to_dataclass(section_data, section_type)
                setattr(config, section_name, section_instance)
    
    # Set source information
    config.source = ConfigSource.FILE if config_dict else ConfigSource.DEFAULT
    config.sources = ["environment" if _load_env_config() else "", "files"]
    
    return config


def _create_default_configuration() -> Configuration:
    """Create configuration with all default values."""
    return Configuration()


def load_configuration(
    config_path: Optional[Union[str, Path]] = None,
    env_prefixes: Optional[List[str]] = None,
    use_env: bool = True,
    use_files: bool = True,
    use_defaults: bool = True,
) -> Configuration:
    """
    Load configuration from all sources with proper precedence.
    
    Priority order (highest to lowest):
    1. Explicit config_path parameter
    2. Environment variables
    3. Configuration files
    4. Default values
    
    Args:
        config_path: Explicit path to configuration file
        env_prefixes: Environment variable prefixes to consider
        use_env: Whether to load from environment variables
        use_files: Whether to load from configuration files
        use_defaults: Whether to use default values
        
    Returns:
        Merged Configuration instance
    """
    config_dict: ConfigDict = {}
    sources: List[str] = []
    
    # Start with defaults
    if use_defaults:
        default_config = _create_default_configuration()
        config_dict = default_config.to_dict()
        sources.append("defaults")
    
    # Load from files
    if use_files:
        if config_path:
            # Load explicit file
            try:
                file_config = _load_file_config(Path(config_path))
                config_dict = _deep_merge(config_dict, file_config)
                sources.append(f"file:{config_path}")
                _logger.info(f"Loaded explicit configuration from {config_path}")
            except (ConfigFileNotFoundError, InvalidConfigError) as e:
                _logger.warning(f"Failed to load configuration from {config_path}: {e}")
        else:
            # Search for configuration files
            files_config = _load_files_config()
            if files_config:
                config_dict = _deep_merge(config_dict, files_config)
                sources.extend([f"file:{path}" for path in _find_config_files()])
    
    # Load from environment
    if use_env:
        env_config = _load_env_config(env_prefixes)
        if env_config:
            config_dict = _deep_merge(config_dict, env_config)
            sources.append("environment")
            _logger.info("Loaded configuration from environment variables")
    
    # Convert to Configuration instance
    configuration = _config_dict_to_configuration(config_dict)
    configuration.sources = sources
    
    return configuration


def _find_config_files() -> List[str]:
    """Find all configuration files in search paths."""
    search_paths = [Path.cwd(), Path.home(), Path("/etc/scrapper")]
    found_files = []
    
    for search_path in search_paths:
        for config_file in CONFIG_FILE_NAMES:
            file_path = search_path / config_file
            if file_path.exists():
                found_files.append(str(file_path))
    
    return found_files


@lru_cache(maxsize=1)
def get_settings(
    config_path: Optional[Union[str, Path]] = None,
    reload: bool = False,
    reset: bool = False,
) -> Configuration:
    """
    Get global settings instance.
    
    This function uses caching to avoid reloading configuration on every call.
    Use reload=True to force reload from sources.
    Use reset=True to reset to defaults.
    
    Args:
        config_path: Explicit configuration file path
        reload: Force reload from sources
        reset: Reset to default configuration
        
    Returns:
        Global Configuration instance
    """
    global _settings_instance
    
    if reset:
        _settings_instance = None
        get_settings.cache_clear()
    
    if reload or _settings_instance is None:
        _settings_instance = load_configuration(config_path=config_path)
        _logger.info(f"Settings loaded from: {', '.join(_settings_instance.sources)}")
    
    return _settings_instance


class Settings:
    """
    Settings management class providing convenient access to configuration.
    
    This class provides both attribute-style and dictionary-style access to settings,
    with support for reloading and resetting configuration.
    """
    
    def __init__(self, config_path: Optional[Union[str, Path]] = None):
        """
        Initialize Settings instance.
        
        Args:
            config_path: Optional path to configuration file
        """
        self._config_path = config_path
        self._configuration: Optional[Configuration] = None
        self.load()
    
    def load(self, reload: bool = False) -> None:
        """
        Load or reload configuration.
        
        Args:
            reload: Force reload from sources
        """
        if reload:
            self._configuration = None
        
        if self._configuration is None:
            self._configuration = load_configuration(self._config_path)
    
    def reset(self) -> None:
        """Reset configuration to defaults."""
        self._configuration = _create_default_configuration()
    
    @property
    def config(self) -> Configuration:
        """Get current configuration."""
        if self._configuration is None:
            self.load()
        return self._configuration
    
    def __getattr__(self, name: str) -> Any:
        """Get configuration attribute by name."""
        return getattr(self.config, name)
    
    def __getitem__(self, key: str) -> Any:
        """Get configuration item by key (dictionary-style)."""
        config_dict = self.config.to_dict()
        return config_dict[key]
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value with fallback."""
        try:
            return self[key]
        except (KeyError, AttributeError):
            return default
    
    def to_dict(self) -> ConfigDict:
        """Convert configuration to dictionary."""
        return self.config.to_dict()
    
    def to_env_dict(self) -> Dict[str, str]:
        """Convert configuration to environment variable format."""
        return self.config.to_env_dict()
    
    def save(self, path: Optional[Union[str, Path]] = None) -> None:
        """
        Save current configuration to file.
        
        Args:
            path: Path to save configuration (defaults to original config_path)
        """
        path = path or self._config_path or "config.yaml"
        path = Path(path)
        
        # Create parent directories if they don't exist
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Convert to dictionary and save
        config_dict = self.config.to_dict()
        
        # Remove non-serializable fields
        if 'sources' in config_dict:
            del config_dict['sources']
        if 'source' in config_dict:
            del config_dict['source']
        
        # Determine format based on file extension
        suffix = path.suffix.lower()
        
        if suffix in ('.yaml', '.yml'):
            with open(path, 'w', encoding='utf-8') as f:
                yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)
        elif suffix == '.json':
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, indent=2)
        else:
            # Default to YAML
            with open(path, 'w', encoding='utf-8') as f:
                yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)
        
        _logger.info(f"Configuration saved to {path}")
    
    def __repr__(self) -> str:
        return f"Settings(sources={self.config.sources})"
    
    def __str__(self) -> str:
        return f"Settings loaded from: {', '.join(self.config.sources)}"


def create_settings_manager(config_path: Optional[Union[str, Path]] = None) -> Settings:
    """
    Create a new Settings manager instance.
    
    Args:
        config_path: Optional path to configuration file
        
    Returns:
        Settings manager instance
    """
    return Settings(config_path)


# Convenience function for quick access
def get_config(key: str, default: Any = None) -> Any:
    """
    Quick access to configuration value.
    
    Args:
        key: Configuration key (dot notation for nested keys)
        default: Default value if key not found
        
    Returns:
        Configuration value
    """
    settings = get_settings()
    config_dict = settings.to_dict()
    
    # Support dot notation
    keys = key.split('.')
    current = config_dict
    
    for k in keys:
        if isinstance(current, dict) and k in current:
            current = current[k]
        else:
            return default
    
    return current


# Initialize logging for this module
_logging_config = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'DEBUG',
            'formatter': 'simple',
        },
    },
    'formatters': {
        'simple': {
            'format': '[%(levelname)s] %(name)s: %(message)s'
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
}

# Only configure logging if it hasn't been configured yet
if not logging.getLogger().hasHandlers():
    logging.basicConfig(**_logging_config)