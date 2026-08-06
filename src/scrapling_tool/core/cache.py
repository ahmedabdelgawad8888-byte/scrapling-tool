"""
Cache Module
============
Multi-level caching system for request/response caching and performance optimization.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import pickle
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union, Tuple
from functools import wraps
from collections import OrderedDict
import threading
import zlib

from ..config.schemas import CacheConfig
from ..config.settings import get_settings

# Logger
_logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Cache entry with value and metadata"""
    value: Any
    timestamp: float = field(default_factory=time.time)
    ttl: Optional[float] = None  # Time to live in seconds
    size: int = 0
    key: str = ""
    tags: List[str] = field(default_factory=list)
    
    def is_expired(self) -> bool:
        """Check if this cache entry has expired"""
        if self.ttl is None:
            return False
        return time.time() > (self.timestamp + self.ttl)
    
    def remaining_ttl(self) -> float:
        """Get remaining TTL in seconds"""
        if self.ttl is None:
            return float('inf')
        remaining = (self.timestamp + self.ttl) - time.time()
        return max(0, remaining)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'value': self.value,
            'timestamp': self.timestamp,
            'ttl': self.ttl,
            'size': self.size,
            'key': self.key,
            'tags': self.tags,
        }


@dataclass
class CacheKey:
    """Cache key with components"""
    key: str
    components: Tuple[str, ...] = ()
    prefix: str = ""
    tags: List[str] = field(default_factory=list)
    
    @classmethod
    def from_parts(cls, *parts: Any, prefix: str = "", tags: Optional[List[str]] = None) -> CacheKey:
        """Create cache key from parts"""
        components = tuple(str(part) for part in parts)
        key = ':'.join(components)
        return cls(key=key, components=components, prefix=prefix, tags=tags or [])
    
    def with_prefix(self, prefix: str) -> CacheKey:
        """Create new key with additional prefix"""
        return CacheKey(
            key=f"{prefix}:{self.key}" if prefix else self.key,
            components=self.components,
            prefix=f"{prefix}:{self.prefix}" if prefix and self.prefix else prefix or self.prefix,
            tags=self.tags.copy()
        )
    
    def with_tags(self, tags: List[str]) -> CacheKey:
        """Create new key with additional tags"""
        return CacheKey(
            key=self.key,
            components=self.components,
            prefix=self.prefix,
            tags=self.tags + tags
        )


@dataclass
class CacheStats:
    """Cache statistics"""
    hits: int = 0
    misses: int = 0
    sets: int = 0
    deletes: int = 0
    evictions: int = 0
    total_size: int = 0
    current_size: int = 0
    max_size: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'hits': self.hits,
            'misses': self.misses,
            'sets': self.sets,
            'deletes': self.deletes,
            'evictions': self.evictions,
            'total_size': self.total_size,
            'current_size': self.current_size,
            'max_size': self.max_size,
            'hit_rate': self.hit_rate,
        }
    
    @property
    def hit_rate(self) -> float:
        """Calculate hit rate percentage"""
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0.0


class CacheError(Exception):
    """Base cache exception"""
    pass


class CacheConnectionError(CacheError):
    """Cache connection error"""
    pass


class CacheSerializeError(CacheError):
    """Cache serialization error"""
    pass


class MemoryCache:
    """
    In-memory cache with LRU eviction policy.
    """
    
    def __init__(self, max_size: int = 1000, ttl: Optional[float] = None):
        """
        Initialize MemoryCache.
        
        Args:
            max_size: Maximum number of entries
            ttl: Default TTL for entries in seconds
        """
        self.max_size = max_size
        self.default_ttl = ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self.stats = CacheStats(max_size=max_size)
    
    def _evict_if_needed(self):
        """Evict entries if cache exceeds max size"""
        with self._lock:
            while len(self._cache) > self.max_size:
                # Remove oldest entry
                oldest_key, oldest_entry = self._cache.popitem(last=False)
                self.stats.evictions += 1
                self.stats.current_size -= oldest_entry.size
                _logger.debug(f"Evicted cache entry: {oldest_key}")
    
    def get(self, key: Union[str, CacheKey]) -> Optional[Any]:
        """
        Get value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found or expired
        """
        if isinstance(key, CacheKey):
            key = key.key
        
        with self._lock:
            if key not in self._cache:
                self.stats.misses += 1
                return None
            
            entry = self._cache[key]
            
            # Check if expired
            if entry.is_expired():
                del self._cache[key]
                self.stats.misses += 1
                self.stats.evictions += 1
                self.stats.current_size -= entry.size
                _logger.debug(f"Cache entry expired: {key}")
                return None
            
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self.stats.hits += 1
            return entry.value
    
    def set(self, key: Union[str, CacheKey], value: Any, ttl: Optional[float] = None,
            size: Optional[int] = None) -> bool:
        """
        Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: TTL in seconds (overrides default)
            size: Size of the value (for stats)
            
        Returns:
            True if successful
        """
        if isinstance(key, CacheKey):
            key = key.key
        
        # Calculate size if not provided
        if size is None:
            size = self._calculate_size(value)
        
        with self._lock:
            # Remove existing entry first
            if key in self._cache:
                old_entry = self._cache[key]
                self.stats.current_size -= old_entry.size
            
            # Create new entry
            entry = CacheEntry(
                value=value,
                ttl=ttl if ttl is not None else self.default_ttl,
                size=size,
                key=key
            )
            
            # Add to cache
            self._cache[key] = entry
            self.stats.sets += 1
            self.stats.current_size += size
            self.stats.total_size += size
            
            # Evict if needed
            self._evict_if_needed()
        
        return True
    
    def delete(self, key: Union[str, CacheKey]) -> bool:
        """
        Delete entry from cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if entry was deleted, False if not found
        """
        if isinstance(key, CacheKey):
            key = key.key
        
        with self._lock:
            if key not in self._cache:
                return False
            
            entry = self._cache.pop(key)
            self.stats.deletes += 1
            self.stats.current_size -= entry.size
            return True
    
    def exists(self, key: Union[str, CacheKey]) -> bool:
        """
        Check if key exists in cache and is not expired.
        
        Args:
            key: Cache key
            
        Returns:
            True if key exists and is not expired
        """
        return self.get(key) is not None
    
    def clear(self) -> int:
        """
        Clear all entries from cache.
        
        Returns:
            Number of entries cleared
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self.stats.current_size = 0
            return count
    
    def get_many(self, keys: List[Union[str, CacheKey]]) -> Dict[str, Any]:
        """
        Get multiple values from cache.
        
        Args:
            keys: List of cache keys
            
        Returns:
            Dictionary of key-value pairs for found keys
        """
        result = {}
        for key in keys:
            value = self.get(key)
            if value is not None:
                actual_key = key.key if isinstance(key, CacheKey) else key
                result[actual_key] = value
        return result
    
    def set_many(self, items: Dict[Union[str, CacheKey], Any], ttl: Optional[float] = None) -> bool:
        """
        Set multiple values in cache.
        
        Args:
            items: Dictionary of key-value pairs
            ttl: TTL in seconds
            
        Returns:
            True if successful
        """
        for key, value in items.items():
            self.set(key, value, ttl)
        return True
    
    def delete_many(self, keys: List[Union[str, CacheKey]]) -> int:
        """
        Delete multiple entries from cache.
        
        Args:
            keys: List of cache keys
            
        Returns:
            Number of entries deleted
        """
        count = 0
        for key in keys:
            if self.delete(key):
                count += 1
        return count
    
    def invalidate_by_pattern(self, pattern: str) -> int:
        """
        Invalidate cache entries matching a pattern.
        
        Args:
            pattern: Regex pattern to match against keys
            
        Returns:
            Number of entries invalidated
        """
        count = 0
        keys_to_delete = []
        
        with self._lock:
            for key in self._cache.keys():
                if re.match(pattern, key):
                    keys_to_delete.append(key)
            
            for key in keys_to_delete:
                entry = self._cache.pop(key)
                self.stats.current_size -= entry.size
                self.stats.evictions += 1
                count += 1
        
        return count
    
    def _calculate_size(self, value: Any) -> int:
        """Calculate approximate size of a value in bytes"""
        if isinstance(value, (str, bytes)):
            return len(value)
        elif isinstance(value, (list, tuple, set)):
            return sum(self._calculate_size(item) for item in value)
        elif isinstance(value, dict):
            return sum(self._calculate_size(k) + self._calculate_size(v) for k, v in value.items())
        else:
            # For other types, use a fixed size estimate
            return 64
    
    def get_stats(self) -> CacheStats:
        """Get cache statistics"""
        with self._lock:
            return CacheStats(
                hits=self.stats.hits,
                misses=self.stats.misses,
                sets=self.stats.sets,
                deletes=self.stats.deletes,
                evictions=self.stats.evictions,
                total_size=self.stats.total_size,
                current_size=self.stats.current_size,
                max_size=self.max_size,
            )
    
    def __len__(self) -> int:
        return len(self._cache)
    
    def __contains__(self, key: Union[str, CacheKey]) -> bool:
        return self.exists(key)


class DiskCache:
    """
    Disk-based cache with file storage.
    """
    
    def __init__(self, cache_dir: Union[str, Path], max_size: int = 100 * 1024 * 1024, 
                 ttl: Optional[float] = None, compress: bool = False):
        """
        Initialize DiskCache.
        
        Args:
            cache_dir: Directory to store cache files
            max_size: Maximum cache size in bytes
            ttl: Default TTL for entries in seconds
            compress: Whether to compress cache values
        """
        self.cache_dir = Path(cache_dir)
        self.max_size = max_size
        self.default_ttl = ttl
        self.compress = compress
        self._lock = threading.RLock()
        self.stats = CacheStats(max_size=max_size)
        
        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_cache_path(self, key: str) -> Path:
        """Get file path for a cache key"""
        # Use hash to create filename
        hash_name = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / f"{hash_name}.cache"
    
    def _get_metadata_path(self, key: str) -> Path:
        """Get metadata file path for a cache key"""
        hash_name = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / f"{hash_name}.meta"
    
    def get(self, key: Union[str, CacheKey]) -> Optional[Any]:
        """
        Get value from disk cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found or expired
        """
        if isinstance(key, CacheKey):
            key = key.key
        
        with self._lock:
            cache_path = self._get_cache_path(key)
            metadata_path = self._get_metadata_path(key)
            
            # Check if files exist
            if not cache_path.exists() or not metadata_path.exists():
                self.stats.misses += 1
                return None
            
            # Load metadata
            try:
                with open(metadata_path, 'rb') as f:
                    metadata = pickle.load(f)
                
                # Check if expired
                if metadata.get('ttl') and time.time() > (metadata['timestamp'] + metadata['ttl']):
                    # Remove expired entry
                    cache_path.unlink(missing_ok=True)
                    metadata_path.unlink(missing_ok=True)
                    self.stats.misses += 1
                    self.stats.evictions += 1
                    self.stats.current_size -= metadata.get('size', 0)
                    return None
                
                # Load and deserialize value
                with open(cache_path, 'rb') as f:
                    data = f.read()
                
                # Decompress if needed
                if metadata.get('compressed'):
                    data = zlib.decompress(data)
                
                value = pickle.loads(data)
                self.stats.hits += 1
                return value
                
            except (pickle.PickleError, zlib.error, EOFError, FileNotFoundError) as e:
                _logger.warning(f"Error loading cache entry {key}: {e}")
                self.stats.misses += 1
                return None
    
    def set(self, key: Union[str, CacheKey], value: Any, ttl: Optional[float] = None,
            size: Optional[int] = None) -> bool:
        """
        Set value in disk cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: TTL in seconds (overrides default)
            size: Size of the value (for stats)
            
        Returns:
            True if successful
        """
        if isinstance(key, CacheKey):
            key = key.key
        
        with self._lock:
            cache_path = self._get_cache_path(key)
            metadata_path = self._get_metadata_path(key)
            
            try:
                # Serialize value
                data = pickle.dumps(value)
                
                # Compress if enabled
                if self.compress:
                    data = zlib.compress(data)
                
                # Calculate size if not provided
                if size is None:
                    size = len(data)
                
                # Write cache data
                with open(cache_path, 'wb') as f:
                    f.write(data)
                
                # Write metadata
                metadata = {
                    'timestamp': time.time(),
                    'ttl': ttl if ttl is not None else self.default_ttl,
                    'size': size,
                    'compressed': self.compress,
                }
                with open(metadata_path, 'wb') as f:
                    pickle.dump(metadata, f)
                
                self.stats.sets += 1
                self.stats.current_size += size
                self.stats.total_size += size
                
                # Check if we need to evict
                self._evict_if_needed()
                
                return True
                
            except (pickle.PickleError, zlib.error, IOError) as e:
                _logger.error(f"Error saving cache entry {key}: {e}")
                return False
    
    def delete(self, key: Union[str, CacheKey]) -> bool:
        """
        Delete entry from disk cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if entry was deleted, False if not found
        """
        if isinstance(key, CacheKey):
            key = key.key
        
        with self._lock:
            cache_path = self._get_cache_path(key)
            metadata_path = self._get_metadata_path(key)
            
            deleted = False
            size = 0
            
            # Try to load size from metadata first
            try:
                if metadata_path.exists():
                    with open(metadata_path, 'rb') as f:
                        metadata = pickle.load(f)
                    size = metadata.get('size', 0)
            except Exception:
                pass
            
            # Delete files
            if cache_path.exists():
                cache_path.unlink()
                deleted = True
            
            if metadata_path.exists():
                metadata_path.unlink()
                deleted = True
            
            if deleted:
                self.stats.deletes += 1
                self.stats.current_size -= size
            
            return deleted
    
    def exists(self, key: Union[str, CacheKey]) -> bool:
        """
        Check if key exists in cache and is not expired.
        
        Args:
            key: Cache key
            
        Returns:
            True if key exists and is not expired
        """
        return self.get(key) is not None
    
    def clear(self) -> int:
        """
        Clear all entries from cache.
        
        Returns:
            Number of entries cleared
        """
        with self._lock:
            count = 0
            for cache_file in self.cache_dir.glob("*.cache"):
                try:
                    cache_file.unlink()
                    count += 1
                except OSError:
                    pass
            
            for meta_file in self.cache_dir.glob("*.meta"):
                try:
                    meta_file.unlink()
                except OSError:
                    pass
            
            self.stats.current_size = 0
            return count
    
    def _evict_if_needed(self):
        """Evict entries if cache exceeds max size"""
        with self._lock:
            # Get all cache files
            cache_files = list(self.cache_dir.glob("*.cache"))
            total_size = sum(f.stat().st_size for f in cache_files)
            
            while total_size > self.max_size and cache_files:
                # Find oldest file
                oldest_file = min(cache_files, key=lambda f: f.stat().st_mtime)
                oldest_meta = self.cache_dir / (oldest_file.stem + ".meta")
                
                # Delete files
                try:
                    size = oldest_file.stat().st_size
                    oldest_file.unlink()
                    oldest_meta.unlink(missing_ok=True)
                    total_size -= size
                    self.stats.evictions += 1
                    self.stats.current_size -= size
                    cache_files.remove(oldest_file)
                except OSError:
                    cache_files.remove(oldest_file)
    
    def get_stats(self) -> CacheStats:
        """Get cache statistics"""
        with self._lock:
            return CacheStats(
                hits=self.stats.hits,
                misses=self.stats.misses,
                sets=self.stats.sets,
                deletes=self.stats.deletes,
                evictions=self.stats.evictions,
                total_size=self.stats.total_size,
                current_size=self.stats.current_size,
                max_size=self.max_size,
            )
    
    def cleanup(self) -> int:
        """
        Clean up expired entries.
        
        Returns:
            Number of entries cleaned up
        """
        with self._lock:
            count = 0
            cache_files = list(self.cache_dir.glob("*.cache"))
            
            for cache_file in cache_files:
                metadata_path = self.cache_dir / (cache_file.stem + ".meta")
                
                try:
                    if metadata_path.exists():
                        with open(metadata_path, 'rb') as f:
                            metadata = pickle.load(f)
                        
                        if metadata.get('ttl') and time.time() > (metadata['timestamp'] + metadata['ttl']):
                            cache_file.unlink()
                            metadata_path.unlink()
                            self.stats.current_size -= metadata.get('size', 0)
                            count += 1
                except Exception:
                    pass
            
            return count
    
    def __len__(self) -> int:
        return len(list(self.cache_dir.glob("*.cache")))
    
    def __contains__(self, key: Union[str, CacheKey]) -> bool:
        return self.exists(key)


class DatabaseCache:
    """
    Database-based cache using SQLite.
    """
    
    def __init__(self, db_path: Union[str, Path] = "cache.db", 
                 table_name: str = "cache", ttl: Optional[float] = None):
        """
        Initialize DatabaseCache.
        
        Args:
            db_path: Path to SQLite database file
            table_name: Table name for cache
            ttl: Default TTL for entries in seconds
        """
        self.db_path = Path(db_path)
        self.table_name = table_name
        self.default_ttl = ttl
        self._lock = threading.RLock()
        self.stats = CacheStats()
        
        # Initialize database
        self._init_db()
    
    def _init_db(self):
        """Initialize database and create tables if needed"""
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                # Create cache table
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS {self.table_name} (
                        key TEXT PRIMARY KEY,
                        value BLOB NOT NULL,
                        timestamp REAL NOT NULL,
                        ttl REAL,
                        size INTEGER NOT NULL,
                        tags TEXT
                    )
                """)
                
                # Create index for timestamp (for cleanup)
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_timestamp 
                    ON {self.table_name}(timestamp)
                """)
                
                conn.commit()
                conn.close()
                
            except sqlite3.Error as e:
                _logger.error(f"Error initializing cache database: {e}")
                raise CacheConnectionError(f"Failed to initialize database: {e}")
    
    def get(self, key: Union[str, CacheKey]) -> Optional[Any]:
        """
        Get value from database cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found or expired
        """
        if isinstance(key, CacheKey):
            key = key.key
        
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                cursor.execute(f"""
                    SELECT value, timestamp, ttl, size 
                    FROM {self.table_name} 
                    WHERE key = ?
                """, (key,))
                
                row = cursor.fetchone()
                conn.close()
                
                if not row:
                    self.stats.misses += 1
                    return None
                
                value_blob, timestamp, ttl, size = row
                
                # Check if expired
                if ttl and time.time() > (timestamp + ttl):
                    # Delete expired entry
                    self.delete(key)
                    self.stats.misses += 1
                    self.stats.evictions += 1
                    return None
                
                # Deserialize value
                value = pickle.loads(value_blob)
                self.stats.hits += 1
                return value
                
            except (sqlite3.Error, pickle.PickleError) as e:
                _logger.warning(f"Error loading cache entry {key}: {e}")
                self.stats.misses += 1
                return None
    
    def set(self, key: Union[str, CacheKey], value: Any, ttl: Optional[float] = None,
            size: Optional[int] = None) -> bool:
        """
        Set value in database cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: TTL in seconds (overrides default)
            size: Size of the value (for stats)
            
        Returns:
            True if successful
        """
        if isinstance(key, CacheKey):
            key = key.key
        
        with self._lock:
            try:
                # Serialize value
                value_blob = pickle.dumps(value)
                
                # Calculate size if not provided
                if size is None:
                    size = len(value_blob)
                
                # Get TTL
                entry_ttl = ttl if ttl is not None else self.default_ttl
                
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                # Insert or replace
                cursor.execute(f"""
                    INSERT OR REPLACE INTO {self.table_name} 
                    (key, value, timestamp, ttl, size, tags) 
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (key, value_blob, time.time(), entry_ttl, size, ''))
                
                conn.commit()
                conn.close()
                
                self.stats.sets += 1
                self.stats.current_size += size
                self.stats.total_size += size
                
                return True
                
            except (sqlite3.Error, pickle.PickleError) as e:
                _logger.error(f"Error saving cache entry {key}: {e}")
                return False
    
    def delete(self, key: Union[str, CacheKey]) -> bool:
        """
        Delete entry from database cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if entry was deleted, False if not found
        """
        if isinstance(key, CacheKey):
            key = key.key
        
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                # Get size first
                cursor.execute(f"SELECT size FROM {self.table_name} WHERE key = ?", (key,))
                row = cursor.fetchone()
                size = row[0] if row else 0
                
                # Delete
                cursor.execute(f"DELETE FROM {self.table_name} WHERE key = ?", (key,))
                deleted = cursor.rowcount > 0
                
                conn.commit()
                conn.close()
                
                if deleted:
                    self.stats.deletes += 1
                    self.stats.current_size -= size
                
                return deleted
                
            except sqlite3.Error as e:
                _logger.warning(f"Error deleting cache entry {key}: {e}")
                return False
    
    def exists(self, key: Union[str, CacheKey]) -> bool:
        """
        Check if key exists in cache and is not expired.
        
        Args:
            key: Cache key
            
        Returns:
            True if key exists and is not expired
        """
        return self.get(key) is not None
    
    def clear(self) -> int:
        """
        Clear all entries from cache.
        
        Returns:
            Number of entries cleared
        """
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                # Get count
                cursor.execute(f"SELECT COUNT(*) FROM {self.table_name}")
                count = cursor.fetchone()[0]
                
                # Delete all
                cursor.execute(f"DELETE FROM {self.table_name}")
                
                conn.commit()
                conn.close()
                
                self.stats.current_size = 0
                return count
                
            except sqlite3.Error as e:
                _logger.error(f"Error clearing cache: {e}")
                return 0
    
    def cleanup(self) -> int:
        """
        Clean up expired entries.
        
        Returns:
            Number of entries cleaned up
        """
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                # Delete expired entries
                cursor.execute(f"""
                    DELETE FROM {self.table_name} 
                    WHERE ttl IS NOT NULL AND timestamp + ttl < ?
                """, (time.time(),))
                
                count = cursor.rowcount
                conn.commit()
                conn.close()
                
                return count
                
            except sqlite3.Error as e:
                _logger.error(f"Error cleaning up cache: {e}")
                return 0
    
    def get_stats(self) -> CacheStats:
        """Get cache statistics"""
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                # Get count and size
                cursor.execute(f"SELECT COUNT(*), SUM(size) FROM {self.table_name}")
                row = cursor.fetchone()
                count = row[0] or 0
                current_size = row[1] or 0
                
                conn.close()
                
                return CacheStats(
                    hits=self.stats.hits,
                    misses=self.stats.misses,
                    sets=self.stats.sets,
                    deletes=self.stats.deletes,
                    evictions=self.stats.evictions,
                    total_size=self.stats.total_size,
                    current_size=current_size,
                    max_size=self.stats.max_size,
                )
                
            except sqlite3.Error as e:
                _logger.warning(f"Error getting cache stats: {e}")
                return CacheStats()
    
    def __len__(self) -> int:
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute(f"SELECT COUNT(*) FROM {self.table_name}")
                count = cursor.fetchone()[0]
                conn.close()
                return count
            except sqlite3.Error:
                return 0
    
    def __contains__(self, key: Union[str, CacheKey]) -> bool:
        return self.exists(key)


class MultiLevelCache:
    """
    Multi-level cache with fallback between cache levels.
    Priority: Memory -> Disk -> Database
    """
    
    def __init__(self, config: Optional[CacheConfig] = None):
        """
        Initialize MultiLevelCache.
        
        Args:
            config: Cache configuration
        """
        self.config = config or get_settings().config.cache
        self.stats = CacheStats()
        
        # Initialize cache levels
        self.memory_cache = None
        self.disk_cache = None
        self.database_cache = None
        
        if self.config.memory_cache_enabled:
            self.memory_cache = MemoryCache(
                max_size=self.config.memory_cache_size,
                ttl=self.config.memory_cache_ttl if self.config.memory_cache_ttl > 0 else None
            )
        
        if self.config.disk_cache_enabled:
            self.disk_cache = DiskCache(
                cache_dir=self.config.disk_cache_dir,
                max_size=self.config.disk_cache_max_size,
                ttl=self.config.disk_cache_ttl if self.config.disk_cache_ttl > 0 else None,
                compress=self.config.compress_cache
            )
        
        if self.config.database_cache_enabled:
            self.database_cache = DatabaseCache(
                db_path=self.config.database_url.replace('sqlite:///', ''),
                ttl=self.config.database_cache_ttl if self.config.database_cache_ttl > 0 else None
            )
    
    def get(self, key: Union[str, CacheKey]) -> Optional[Any]:
        """
        Get value from cache (tries all levels in order).
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found in any level
        """
        # Try memory cache first
        if self.memory_cache:
            value = self.memory_cache.get(key)
            if value is not None:
                self.stats.hits += 1
                return value
        
        # Try disk cache
        if self.disk_cache:
            value = self.disk_cache.get(key)
            if value is not None:
                # Cache in memory for future requests
                if self.memory_cache:
                    ttl = self.memory_cache.default_ttl if self.memory_cache else None
                    self.memory_cache.set(key, value, ttl)
                self.stats.hits += 1
                return value
        
        # Try database cache
        if self.database_cache:
            value = self.database_cache.get(key)
            if value is not None:
                # Cache in memory for future requests
                if self.memory_cache:
                    ttl = self.memory_cache.default_ttl if self.memory_cache else None
                    self.memory_cache.set(key, value, ttl)
                self.stats.hits += 1
                return value
        
        # Not found in any cache
        self.stats.misses += 1
        return None
    
    def set(self, key: Union[str, CacheKey], value: Any, ttl: Optional[float] = None,
            to_all_levels: bool = True) -> bool:
        """
        Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: TTL in seconds
            to_all_levels: Whether to store in all enabled cache levels
            
        Returns:
            True if successful
        """
        success = False
        
        # Set in memory cache
        if self.memory_cache and (to_all_levels or not (self.disk_cache or self.database_cache)):
            cache_ttl = self._calculate_ttl('memory', ttl)
            if self.memory_cache.set(key, value, cache_ttl):
                success = True
        
        # Set in disk cache
        if self.disk_cache and to_all_levels:
            cache_ttl = self._calculate_ttl('disk', ttl)
            if self.disk_cache.set(key, value, cache_ttl):
                success = True
        
        # Set in database cache
        if self.database_cache and to_all_levels:
            cache_ttl = self._calculate_ttl('database', ttl)
            if self.database_cache.set(key, value, cache_ttl):
                success = True
        
        if success:
            self.stats.sets += 1
        
        return success
    
    def _calculate_ttl(self, level: str, ttl: Optional[float]) -> Optional[float]:
        """Calculate TTL for a specific cache level"""
        if ttl is not None:
            return ttl
        
        if level == 'memory':
            return self.config.memory_cache_ttl if self.config.memory_cache_ttl > 0 else None
        elif level == 'disk':
            return self.config.disk_cache_ttl if self.config.disk_cache_ttl > 0 else None
        elif level == 'database':
            return self.config.database_cache_ttl if self.config.database_cache_ttl > 0 else None
        
        return None
    
    def delete(self, key: Union[str, CacheKey], from_all_levels: bool = True) -> bool:
        """
        Delete entry from cache.
        
        Args:
            key: Cache key
            from_all_levels: Whether to delete from all cache levels
            
        Returns:
            True if entry was deleted from any level
        """
        deleted = False
        
        if self.memory_cache and (from_all_levels or not (self.disk_cache or self.database_cache)):
            if self.memory_cache.delete(key):
                deleted = True
        
        if self.disk_cache and from_all_levels:
            if self.disk_cache.delete(key):
                deleted = True
        
        if self.database_cache and from_all_levels:
            if self.database_cache.delete(key):
                deleted = True
        
        if deleted:
            self.stats.deletes += 1
        
        return deleted
    
    def exists(self, key: Union[str, CacheKey]) -> bool:
        """
        Check if key exists in any cache level.
        
        Args:
            key: Cache key
            
        Returns:
            True if key exists in any cache level
        """
        return self.get(key) is not None
    
    def clear(self, all_levels: bool = True) -> int:
        """
        Clear all cache levels.
        
        Args:
            all_levels: Whether to clear all cache levels
            
        Returns:
            Total number of entries cleared
        """
        total_cleared = 0
        
        if self.memory_cache and (all_levels or not (self.disk_cache or self.database_cache)):
            total_cleared += self.memory_cache.clear()
        
        if self.disk_cache and all_levels:
            total_cleared += self.disk_cache.clear()
        
        if self.database_cache and all_levels:
            total_cleared += self.database_cache.clear()
        
        return total_cleared
    
    def cleanup(self) -> int:
        """
        Clean up expired entries from all cache levels.
        
        Returns:
            Total number of entries cleaned up
        """
        total_cleaned = 0
        
        if self.disk_cache:
            total_cleaned += self.disk_cache.cleanup()
        
        if self.database_cache:
            total_cleaned += self.database_cache.cleanup()
        
        return total_cleaned
    
    def invalidate_by_pattern(self, pattern: str) -> int:
        """
        Invalidate cache entries matching a pattern.
        
        Args:
            pattern: Regex pattern to match against keys
            
        Returns:
            Number of entries invalidated
        """
        count = 0
        
        if self.memory_cache:
            count += self.memory_cache.invalidate_by_pattern(pattern)
        
        # Note: Disk and database caches don't support pattern invalidation
        # For those, you would need to get all keys and filter manually
        
        return count
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics for all cache levels"""
        stats = {
            'overall': self.stats.to_dict(),
        }
        
        if self.memory_cache:
            stats['memory'] = self.memory_cache.get_stats().to_dict()
        
        if self.disk_cache:
            stats['disk'] = self.disk_cache.get_stats().to_dict()
        
        if self.database_cache:
            stats['database'] = self.database_cache.get_stats().to_dict()
        
        return stats
    
    async def clear_async(self) -> int:
        """
        Async version of clear (useful for async contexts).
        """
        return self.clear()
    
    async def cleanup_async(self) -> int:
        """
        Async version of cleanup.
        """
        return self.cleanup()
    
    def __len__(self) -> int:
        """Get total number of entries across all levels"""
        total = 0
        if self.memory_cache:
            total += len(self.memory_cache)
        if self.disk_cache:
            total += len(self.disk_cache)
        if self.database_cache:
            total += len(self.database_cache)
        return total
    
    def __contains__(self, key: Union[str, CacheKey]) -> bool:
        return self.exists(key)


# =============================================================================
# Decorator Functions
# =============================================================================

def cached(cache: Optional[MultiLevelCache] = None, key_func: Optional[Callable[..., str]] = None,
           ttl: Optional[float] = None, prefix: str = ""):
    """
    Decorator to cache function results.
    
    Args:
        cache: Cache instance to use (defaults to global cache)
        key_func: Function to generate cache key from function arguments
        ttl: TTL for cache entries
        prefix: Prefix for cache keys
        
    Returns:
        Decorator function
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Get cache instance
            if cache is None:
                cache_instance = MultiLevelCache()
            else:
                cache_instance = cache
            
            # Generate cache key
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                # Generate key from function name and arguments
                key_parts = [func.__name__, str(args), str(sorted(kwargs.items()))]
                cache_key = ':'.join(key_parts)
            
            if prefix:
                cache_key = f"{prefix}:{cache_key}"
            
            # Check cache
            cached_result = cache_instance.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            # Execute function
            result = func(*args, **kwargs)
            
            # Cache result
            cache_instance.set(cache_key, result, ttl)
            
            return result
        
        return wrapper
    
    return decorator


async def cached_async(cache: Optional[MultiLevelCache] = None, key_func: Optional[Callable[..., str]] = None,
                      ttl: Optional[float] = None, prefix: str = ""):
    """
    Decorator to cache async function results.
    
    Args:
        cache: Cache instance to use
        key_func: Function to generate cache key from function arguments
        ttl: TTL for cache entries
        prefix: Prefix for cache keys
        
    Returns:
        Decorator function
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Get cache instance
            if cache is None:
                cache_instance = MultiLevelCache()
            else:
                cache_instance = cache
            
            # Generate cache key
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                # Generate key from function name and arguments
                key_parts = [func.__name__, str(args), str(sorted(kwargs.items()))]
                cache_key = ':'.join(key_parts)
            
            if prefix:
                cache_key = f"{prefix}:{cache_key}"
            
            # Check cache
            cached_result = cache_instance.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            # Execute function
            result = await func(*args, **kwargs)
            
            # Cache result
            cache_instance.set(cache_key, result, ttl)
            
            return result
        
        return wrapper
    
    return decorator


# Global cache instance
_global_cache: Optional[MultiLevelCache] = None


def get_cache() -> MultiLevelCache:
    """Get global cache instance"""
    global _global_cache
    if _global_cache is None:
        config = get_settings().config.cache
        _global_cache = MultiLevelCache(config)
    return _global_cache


def reset_cache() -> None:
    """Reset global cache instance"""
    global _global_cache
    if _global_cache:
        _global_cache.clear()
        _global_cache = None


# Convenience functions
def cache_get(key: Union[str, CacheKey]) -> Optional[Any]:
    """Get value from global cache"""
    return get_cache().get(key)


def cache_set(key: Union[str, CacheKey], value: Any, ttl: Optional[float] = None) -> bool:
    """Set value in global cache"""
    return get_cache().set(key, value, ttl)


def cache_delete(key: Union[str, CacheKey]) -> bool:
    """Delete value from global cache"""
    return get_cache().delete(key)


def cache_clear() -> int:
    """Clear global cache"""
    return get_cache().clear()
