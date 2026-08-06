"""
Utilities Module
===============
Utility functions for URL manipulation, content processing, and other helpers.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from dataclasses import dataclass

from .fetcher import FetchMode


# =============================================================================
# URL Utilities
# =============================================================================

def clean_url(url: str) -> str:
    """
    Clean and normalize a URL.
    
    Args:
        url: URL to clean
        
    Returns:
        Cleaned URL
    """
    if not url:
        return url
    
    # Convert to string if not already
    url = str(url).strip()
    
    # Remove leading/trailing whitespace and quotes
    url = url.strip('\'"\' ')
    
    # Parse URL
    parsed = urllib.parse.urlparse(url)
    
    # Ensure scheme
    if not parsed.scheme:
        # Try to detect if it's a relative URL
        if url.startswith(('/')):
            # Keep as relative
            pass
        else:
            # Add https:// by default
            url = f'https://{url}'
            parsed = urllib.parse.urlparse(url)
    
    # Remove fragment
    parsed = parsed._replace(fragment='')
    
    # Remove default ports
    if parsed.port:
        if (parsed.scheme == 'http' and parsed.port == 80) or \
           (parsed.scheme == 'https' and parsed.port == 443):
            parsed = parsed._replace(netloc=parsed.netloc.replace(f':{parsed.port}', ''))
    
    # Remove www. if present (optional)
    netloc = parsed.netloc
    if netloc.startswith('www.'):
        netloc = netloc[4:]
        parsed = parsed._replace(netloc=netloc)
    
    # Reconstruct URL
    return urllib.parse.urlunparse(parsed)


def normalize_url(url: str, keep_fragment: bool = False, keep_query: bool = True) -> str:
    """
    Normalize URL to a standard format.
    
    Args:
        url: URL to normalize
        keep_fragment: Whether to keep URL fragment
        keep_query: Whether to keep query parameters
        
    Returns:
        Normalized URL
    """
    if not url:
        return url
    
    url = clean_url(url)
    parsed = urllib.parse.urlparse(url)
    
    # Normalize scheme and host to lowercase
    scheme = parsed.scheme.lower() if parsed.scheme else ''
    netloc = parsed.netloc.lower() if parsed.netloc else ''
    
    # Normalize path (remove duplicate slashes, trailing slashes)
    path = parsed.path
    if path:
        # Remove duplicate slashes
        path = re.sub(r'/+', '/', path)
        # Remove trailing slash (except for root)
        if len(path) > 1 and path.endswith('/'):
            path = path.rstrip('/')
    
    # Handle query parameters
    query = parsed.query
    if not keep_query:
        query = ''
    elif query:
        # Sort query parameters for consistent normalization
        query_parts = urllib.parse.parse_qsl(query, keep_blank_values=True)
        query_parts.sort()
        query = urllib.parse.urlencode(query_parts)
    
    # Handle fragment
    fragment = parsed.fragment if keep_fragment else ''
    
    # Reconstruct URL
    return urllib.parse.urlunparse(parsed._replace(
        scheme=scheme,
        netloc=netloc,
        path=path,
        query=query,
        fragment=fragment
    ))


def extract_domain(url: str, include_subdomains: bool = True) -> str:
    """
    Extract domain from URL.
    
    Args:
        url: URL to extract domain from
        include_subdomains: Whether to include subdomains
        
    Returns:
        Domain name
    """
    if not url:
        return ''
    
    url = clean_url(url)
    parsed = urllib.parse.urlparse(url)
    
    if not parsed.netloc:
        return ''
    
    domain = parsed.netloc
    
    # Remove port
    if ':' in domain:
        domain = domain.split(':')[0]
    
    # Remove www. prefix
    if domain.startswith('www.'):
        domain = domain[4:]
    
    # Remove subdomains if not including them
    if not include_subdomains:
        parts = domain.split('.')
        if len(parts) > 2:
            # Keep only the last two parts for most TLDs
            # But handle cases like co.uk, com.br, etc.
            if len(parts[-1]) == 2:  # Country TLD
                if len(parts) > 2 and len(parts[-2]) <= 3:  # Like co.uk, com.br
                    domain = '.'.join(parts[-3:])
                else:
                    domain = '.'.join(parts[-2:])
            else:
                domain = '.'.join(parts[-2:])
    
    return domain.lower()


def extract_domain_parts(url: str) -> Dict[str, str]:
    """
    Extract domain parts (protocol, domain, subdomains, TLD, etc.).
    
    Args:
        url: URL to extract parts from
        
    Returns:
        Dictionary with domain parts
    """
    if not url:
        return {}
    
    url = clean_url(url)
    parsed = urllib.parse.urlparse(url)
    
    result = {
        'full_url': url,
        'protocol': parsed.scheme or '',
        'port': parsed.port or 0,
        'path': parsed.path or '/',
        'query': parsed.query or '',
        'fragment': parsed.fragment or '',
    }
    
    if not parsed.netloc:
        return result
    
    # Remove port from netloc
    netloc = parsed.netloc
    if ':' in netloc:
        netloc = netloc.split(':')[0]
    
    result['netloc'] = netloc
    result['domain'] = netloc
    
    # Split into parts
    parts = netloc.split('.')
    
    if len(parts) >= 2:
        result['main_domain'] = '.'.join(parts[-2:])
        result['tld'] = parts[-1]
        result['domain_name'] = parts[-2]
        
        if len(parts) > 2:
            result['subdomains'] = '.'.join(parts[:-2])
            result['subdomain_list'] = parts[:-2]
        else:
            result['subdomains'] = ''
            result['subdomain_list'] = []
    
    return result


def url_matches_pattern(url: str, patterns: List[str]) -> bool:
    """
    Check if URL matches any of the given patterns.
    
    Args:
        url: URL to check
        patterns: List of patterns (can be regex, wildcard, or exact)
        
    Returns:
        True if URL matches any pattern
    """
    if not url or not patterns:
        return False
    
    url = normalize_url(url)
    
    for pattern in patterns:
        if not pattern:
            continue
        
        # Exact match
        if url == pattern:
            return True
        
        # Wildcard match
        if '*' in pattern:
            # Convert wildcard to regex
            regex_pattern = pattern.replace('*', '.*').replace('?', '.')
            regex_pattern = f'^{regex_pattern}$'
            if re.match(regex_pattern, url, re.IGNORECASE):
                return True
        
        # Regex match
        try:
            if re.match(pattern, url, re.IGNORECASE):
                return True
        except re.error:
            # Not a valid regex, try substring match
            if pattern.lower() in url.lower():
                return True
        
        # Substring match
        if pattern.lower() in url.lower():
            return True
    
    return False


def is_valid_url(url: str, allowed_schemes: Optional[List[str]] = None) -> bool:
    """
    Check if a URL is valid.
    
    Args:
        url: URL to validate
        allowed_schemes: List of allowed schemes (http, https, etc.)
        
    Returns:
        True if URL is valid
    """
    if not url:
        return False
    
    try:
        parsed = urllib.parse.urlparse(str(url))
        
        if not parsed.scheme:
            return False
        
        if allowed_schemes:
            if parsed.scheme.lower() not in [s.lower() for s in allowed_schemes]:
                return False
        else:
            # Default to http and https
            if parsed.scheme.lower() not in ['http', 'https']:
                return False
        
        if not parsed.netloc and not (parsed.path and parsed.path.startswith('/')):
            return False
        
        return True
        
    except (ValueError, AttributeError):
        return False


# =============================================================================
# Platform Detection
# =============================================================================

@dataclass
class PlatformInfo:
    """Information about a detected platform"""
    platform: str
    category: str
    confidence: float
    url: str
    features: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'platform': self.platform,
            'category': self.category,
            'confidence': self.confidence,
            'url': self.url,
            'features': self.features,
        }


# Platform patterns for detection
PLATFORM_PATTERNS = [
    # Social Media - High confidence
    {
        'platform': 'tiktok',
        'category': 'social',
        'patterns': [
            r'(?:^|\.)tiktok\.com',
            r'(?:^|\.)douyin\.com',
            r'tiktok\.com/@[\w\.\-]+',
            r'tiktok\.com/t/[\w\-]+',
        ],
        'features': ['video', 'short_video', 'music', 'influencer'],
        'confidence': 0.95,
    },
    {
        'platform': 'instagram',
        'category': 'social',
        'patterns': [
            r'(?:^|\.)instagram\.com',
            r'instagram\.com/[\w\.\-]+',
            r'instagr\.am',
        ],
        'features': ['photo', 'video', 'story', 'reel', 'influencer'],
        'confidence': 0.95,
    },
    {
        'platform': 'snapchat',
        'category': 'social',
        'patterns': [
            r'(?:^|\.)snapchat\.com',
            r'snapchat\.com/add/[\w\.\-]+',
            r'snapchat\.com/add/\w+',
        ],
        'features': ['story', 'photo', 'video', 'disappearing'],
        'confidence': 0.95,
    },
    {
        'platform': 'youtube',
        'category': 'social',
        'patterns': [
            r'(?:^|\.)youtube\.com',
            r'(?:^|\.)youtu\.be',
            r'youtube\.com/watch\?v=',
            r'youtube\.com/shorts/',
            r'youtube\.com/c/',
            r'youtube\.com/user/',
            r'youtube\.com/channel/',
        ],
        'features': ['video', 'long_video', 'streaming', 'live', 'music'],
        'confidence': 0.95,
    },
    {
        'platform': 'twitter',
        'category': 'social',
        'patterns': [
            r'(?:^|\.)twitter\.com',
            r'(?:^|\.)x\.com',
            r'twitter\.com/[\w]+',
            r'x\.com/[\w]+',
        ],
        'features': ['microblog', 'text', 'image', 'video', 'thread'],
        'confidence': 0.95,
    },
    {
        'platform': 'facebook',
        'category': 'social',
        'patterns': [
            r'(?:^|\.)facebook\.com',
            r'(?:^|\.)fb\.com',
            r'facebook\.com/[\w\.\-]+',
            r'fb\.com/[\w\.\-]+',
        ],
        'features': ['profile', 'page', 'group', 'event', 'marketplace'],
        'confidence': 0.95,
    },
    {
        'platform': 'linkedin',
        'category': 'professional',
        'patterns': [
            r'(?:^|\.)linkedin\.com',
            r'linkedin\.com/in/',
            r'linkedin\.com/company/',
        ],
        'features': ['profile', 'job', 'company', 'networking'],
        'confidence': 0.95,
    },
    {
        'platform': 'reddit',
        'category': 'social',
        'patterns': [
            r'(?:^|\.)reddit\.com',
            r'redd\.it',
            r'reddit\.com/r/',
            r'reddit\.com/user/',
        ],
        'features': ['forum', 'subreddit', 'thread', 'comment'],
        'confidence': 0.95,
    },
    {
        'platform': 'pinterest',
        'category': 'social',
        'patterns': [
            r'(?:^|\.)pinterest\.com',
            r'pinterest\.com/[\w\.\-]+',
            r'pin\.it',
        ],
        'features': ['image', 'board', 'pin', 'visual_discovery'],
        'confidence': 0.95,
    },
    
    # E-commerce
    {
        'platform': 'amazon',
        'category': 'ecommerce',
        'patterns': [
            r'(?:^|\.)amazon\.(?:com|co\.uk|de|fr|ca|au|jp|cn|in)',
            r'amzn\.to',
        ],
        'features': ['product', 'search', 'review', 'price_comparison'],
        'confidence': 0.9,
    },
    {
        'platform': 'ebay',
        'category': 'ecommerce',
        'patterns': [
            r'(?:^|\.)ebay\.com',
            r'(?:^|\.)ebay\.(?:co\.uk|de|fr|ca|au)',
        ],
        'features': ['auction', 'product', 'bidding'],
        'confidence': 0.9,
    },
    {
        'platform': 'etsy',
        'category': 'ecommerce',
        'patterns': [
            r'(?:^|\.)etsy\.com',
            r'etsy\.com/listing/',
            r'etsy\.com/shop/',
        ],
        'features': ['handmade', 'vintage', 'craft', 'product'],
        'confidence': 0.9,
    },
    
    # News and Media
    {
        'platform': 'medium',
        'category': 'media',
        'patterns': [
            r'(?:^|\.)medium\.com',
            r'medium\.com/@[\w\.\-]+',
        ],
        'features': ['article', 'blog', 'publication'],
        'confidence': 0.9,
    },
    {
        'platform': 'wordpress',
        'category': 'media',
        'patterns': [
            r'[\w\.\-]+\.wordpress\.com',
            r'wordpress\.com',
        ],
        'features': ['blog', 'article', 'website'],
        'confidence': 0.8,
    },
    
    # Professional and Business
    {
        'platform': 'github',
        'category': 'development',
        'patterns': [
            r'(?:^|\.)github\.com',
            r'github\.com/[\w\.\-]+',
        ],
        'features': ['repository', 'code', 'issue', 'pull_request'],
        'confidence': 0.95,
    },
    {
        'platform': 'gitlab',
        'category': 'development',
        'patterns': [
            r'(?:^|\.)gitlab\.com',
            r'gitlab\.com/[\w\.\-]+',
        ],
        'features': ['repository', 'code', 'issue', 'merge_request'],
        'confidence': 0.9,
    },
    
    # Search Engines
    {
        'platform': 'google',
        'category': 'search',
        'patterns': [
            r'(?:^|\.)google\.com',
            r'(?:^|\.)google\.(?:co\.uk|de|fr|ca|au|jp|cn|in)',
            r'google\.com/search',
        ],
        'features': ['search', 'web_search', 'image_search', 'video_search'],
        'confidence': 0.9,
    },
    {
        'platform': 'bing',
        'category': 'search',
        'patterns': [
            r'(?:^|\.)bing\.com',
            r'bing\.com/search',
        ],
        'features': ['search', 'web_search', 'image_search'],
        'confidence': 0.8,
    },
]


def detect_platform(url: str, additional_patterns: Optional[List[Dict[str, Any]]] = None) -> Optional[PlatformInfo]:
    """
    Detect the platform from a URL.
    
    Args:
        url: URL to analyze
        additional_patterns: Additional platform patterns to check
        
    Returns:
        PlatformInfo if detected, None otherwise
    """
    if not url:
        return None
    
    url = normalize_url(url)
    
    # Combine built-in patterns with additional patterns
    patterns = PLATFORM_PATTERNS + (additional_patterns or [])
    
    best_match = None
    best_confidence = 0.0
    
    for platform_data in patterns:
        for pattern in platform_data['patterns']:
            try:
                if re.search(pattern, url, re.IGNORECASE):
                    confidence = platform_data.get('confidence', 0.8)
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_match = PlatformInfo(
                            platform=platform_data['platform'],
                            category=platform_data.get('category', 'unknown'),
                            confidence=confidence,
                            url=url,
                            features=platform_data.get('features', []),
                        )
                    break  # Found a match for this platform
            except re.error:
                # Invalid regex pattern, skip
                continue
    
    return best_match


def get_suggested_mode(url: str) -> FetchMode:
    """
    Get suggested fetch mode based on platform.
    
    Args:
        url: URL to analyze
        
    Returns:
        Suggested FetchMode
    """
    platform_info = detect_platform(url)
    
    if platform_info:
        platform = platform_info.platform.lower()
        
        # Platforms that typically need stealth/browser mode
        stealth_platforms = ['tiktok', 'instagram', 'snapchat', 'facebook', 'twitter', 'x']
        if platform in stealth_platforms:
            return FetchMode.STEALTH
        
        # Platforms that might need browser mode
        browser_platforms = ['youtube', 'linkedin', 'reddit', 'pinterest']
        if platform in browser_platforms:
            return FetchMode.BROWSER
        
        # Most other platforms can use HTTP or session
        return FetchMode.SESSION
    
    # Default to AUTO
    return FetchMode.AUTO


# =============================================================================
# Content Processing Utilities
# =============================================================================

def sanitize_content(content: str, remove_scripts: bool = True, remove_styles: bool = True,
                      remove_comments: bool = True, trim_whitespace: bool = True) -> str:
    """
    Sanitize HTML content for safer processing.
    
    Args:
        content: HTML content to sanitize
        remove_scripts: Remove script tags and content
        remove_styles: Remove style tags and content
        remove_comments: Remove HTML comments
        trim_whitespace: Trim and normalize whitespace
        
    Returns:
        Sanitized content
    """
    if not content:
        return content
    
    # Remove scripts
    if remove_scripts:
        content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'on\w+\s*=', '', content)  # Remove event handlers
    
    # Remove styles
    if remove_styles:
        content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'style\s*=', '', content, flags=re.IGNORECASE)
    
    # Remove comments
    if remove_comments:
        content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)
    
    # Remove other potentially harmful content
    content = re.sub(r'<iframe[^>]*>.*?</iframe>', '', content, flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r'<object[^>]*>.*?</object>', '', content, flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r'<embed[^>]*>', '', content, flags=re.IGNORECASE)
    
    # Trim whitespace
    if trim_whitespace:
        content = re.sub(r'\s+', ' ', content)  # Replace multiple whitespace with single space
        content = re.sub(r'>\s+<', '><', content)  # Remove space between tags
        content = content.strip()
    
    return content


def extract_text(content: str, strip_html: bool = True, preserve_links: bool = True) -> str:
    """
    Extract text content from HTML.
    
    Args:
        content: HTML content
        strip_html: Remove all HTML tags
        preserve_links: Preserve link URLs in the text
        
    Returns:
        Extracted text
    """
    if not content:
        return content
    
    if not strip_html:
        return content
    
    # Extract text between tags
    text = re.sub(r'<[^>]+>', ' ', content)
    
    # Preserve links if requested
    if preserve_links:
        # Extract and reinsert link URLs
        links = re.findall(r'href=["\']([^"\']+)["\']', content)
        for link in links:
            # Add link after the anchor text
            text = re.sub(r'<a[^>]*>', f' {link} ', text, count=1)
    
    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    
    return text


def clean_text(text: str, remove_extra_whitespace: bool = True, remove_special_chars: bool = False,
                max_length: Optional[int] = None) -> str:
    """
    Clean and normalize text content.
    
    Args:
        text: Text to clean
        remove_extra_whitespace: Remove extra whitespace
        remove_special_chars: Remove special characters (keep alphanumeric, space, basic punctuation)
        max_length: Maximum length of result
        
    Returns:
        Cleaned text
    """
    if not text:
        return text
    
    # Remove extra whitespace
    if remove_extra_whitespace:
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
    
    # Remove special characters if requested
    if remove_special_chars:
        # Keep alphanumeric, spaces, and basic punctuation
        text = re.sub(r'[^\w\s.,;:!?\-\'\"()\[\]{}]', '', text)
    
    # Limit length
    if max_length and len(text) > max_length:
        text = text[:max_length] + '...'
    
    return text


def extract_emails(text: str) -> List[str]:
    """
    Extract email addresses from text.
    
    Args:
        text: Text to search in
        
    Returns:
        List of found email addresses
    """
    if not text:
        return []
    
    # Email regex pattern
    pattern = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    emails = re.findall(pattern, text)
    
    return list(set(emails))  # Remove duplicates


def extract_phone_numbers(text: str) -> List[str]:
    """
    Extract phone numbers from text.
    
    Args:
        text: Text to search in
        
    Returns:
        List of found phone numbers
    """
    if not text:
        return []
    
    # Phone number patterns
    patterns = [
        r'\+?1?\d{1,3}[\s\-\.]?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}',  # US/Canada
        r'\+?\d{1,3}[\s\-\.]?\(?\d{1,4}\)?[\s\-\.]?\d{1,4}[\s\-\.]?\d{1,9}',  # International
    ]
    
    phones = []
    for pattern in patterns:
        found = re.findall(pattern, text)
        phones.extend(found)
    
    return list(set(phones))  # Remove duplicates


def extract_urls(text: str) -> List[str]:
    """
    Extract URLs from text.
    
    Args:
        text: Text to search in
        
    Returns:
        List of found URLs
    """
    if not text:
        return []
    
    # URL regex pattern
    pattern = r'https?://(?:[\w\.\-]+\.)+[\w\-]+(?:/[\w\.\-~/]*)*(?:\?[\w=&]*)?(?:#[\w\-]*)?'
    urls = re.findall(pattern, text)
    
    return list(set(urls))  # Remove duplicates


# =============================================================================
# User Agent Utilities
# =============================================================================

# Common user agents
USER_AGENTS = {
    'chrome': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'firefox': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'safari': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15',
    'edge': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    'mobile_chrome': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    'mobile_safari': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1',
    'curl': 'curl/8.1.2',
    'python_requests': 'python-requests/2.31.0',
}


def get_user_agent(browser: str = 'chrome', mobile: bool = False) -> str:
    """
    Get a user agent string.
    
    Args:
        browser: Browser name (chrome, firefox, safari, edge, etc.)
        mobile: Whether to use mobile user agent
        
    Returns:
        User agent string
    """
    browser = browser.lower()
    
    if mobile:
        if browser in ['chrome', 'chromium']:
            return USER_AGENTS.get('mobile_chrome', USER_AGENTS['chrome'])
        elif browser in ['safari']:
            return USER_AGENTS.get('mobile_safari', USER_AGENTS['safari'])
        else:
            return USER_AGENTS.get('mobile_chrome', USER_AGENTS['chrome'])
    
    return USER_AGENTS.get(browser, USER_AGENTS['chrome'])


def get_random_user_agent() -> str:
    """
    Get a random user agent.
    
    Returns:
        Random user agent string
    """
    import random
    return random.choice(list(USER_AGENTS.values()))


def parse_headers(headers: Union[Dict[str, str], str, List[Tuple[str, str]]]) -> Dict[str, str]:
    """
    Parse headers from various formats into a dictionary.
    
    Args:
        headers: Headers in various formats
        
    Returns:
        Dictionary of headers
    """
    if not headers:
        return {}
    
    if isinstance(headers, dict):
        return headers.copy()
    
    if isinstance(headers, str):
        # Parse from string (e.g., "Key: Value\nKey2: Value2")
        result = {}
        for line in headers.split('\n'):
            line = line.strip()
            if not line or ':' not in line:
                continue
            key, value = line.split(':', 1)
            result[key.strip()] = value.strip()
        return result
    
    if isinstance(headers, list):
        # Parse from list of tuples
        return dict(headers)
    
    return {}


def merge_headers(base: Dict[str, str], override: Dict[str, str]) -> Dict[str, str]:
    """
    Merge headers with override taking precedence.
    
    Args:
        base: Base headers
        override: Headers to override base
        
    Returns:
        Merged headers
    """
    result = base.copy()
    result.update(override)
    return result


# =============================================================================
# Hashing and Identification
# =============================================================================

def generate_hash(content: Union[str, bytes], algorithm: str = 'sha256') -> str:
    """
    Generate hash of content.
    
    Args:
        content: Content to hash
        algorithm: Hash algorithm (md5, sha1, sha256, etc.)
        
    Returns:
        Hexadecimal hash string
    """
    if isinstance(content, str):
        content = content.encode('utf-8')
    
    if algorithm == 'md5':
        return hashlib.md5(content).hexdigest()
    elif algorithm == 'sha1':
        return hashlib.sha1(content).hexdigest()
    elif algorithm == 'sha256':
        return hashlib.sha256(content).hexdigest()
    elif algorithm == 'sha512':
        return hashlib.sha512(content).hexdigest()
    else:
        return hashlib.sha256(content).hexdigest()


def generate_request_id(url: str, method: str = 'GET', data: Optional[Union[str, bytes]] = None) -> str:
    """
    Generate a unique request ID.
    
    Args:
        url: Request URL
        method: HTTP method
        data: Request data
        
    Returns:
        Unique request ID
    """
    parts = [url, method.upper()]
    
    if data:
        if isinstance(data, str):
            parts.append(data)
        else:
            parts.append(data.decode('utf-8', errors='ignore'))
    
    return generate_hash('|'.join(parts), 'sha256')[:16]


# =============================================================================
# String Utilities
# =============================================================================

def truncate_text(text: str, max_length: int = 100, ellipsis: str = '...') -> str:
    """
    Truncate text to maximum length.
    
    Args:
        text: Text to truncate
        max_length: Maximum length
        ellipsis: Ellipsis string to append
        
    Returns:
        Truncated text
    """
    if not text:
        return text
    
    if len(text) <= max_length:
        return text
    
    return text[:max_length - len(ellipsis)] + ellipsis


def format_bytes(size: int) -> str:
    """
    Format bytes into human-readable string.
    
    Args:
        size: Size in bytes
        
    Returns:
        Formatted string (e.g., "1.5 KB", "2.3 MB")
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def format_time(seconds: float) -> str:
    """
    Format seconds into human-readable string.
    
    Args:
        seconds: Time in seconds
        
    Returns:
        Formatted string (e.g., "1.23s", "5m 30s", "2h 15m")
    """
    if seconds < 60:
        return f"{seconds:.2f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.1f}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if secs:
            parts.append(f"{secs:.0f}s")
        return ' '.join(parts)


def safe_filename(text: str, extension: str = '', max_length: int = 255) -> str:
    """
    Create a safe filename from text.
    
    Args:
        text: Text to convert to filename
        extension: File extension (without dot)
        max_length: Maximum filename length
        
    Returns:
        Safe filename
    """
    if not text:
        text = 'untitled'
    
    # Remove invalid characters
    filename = re.sub(r'[\\/*?:"<>|]', '_', text)
    filename = re.sub(r'\s+', '_', filename)
    filename = filename.strip('_')
    
    # Add extension
    if extension:
        if not extension.startswith('.'):
            extension = f'.{extension}'
        filename += extension
    
    # Truncate if too long
    if len(filename) > max_length:
        # Keep extension if possible
        if extension and len(extension) < max_length:
            name_part = filename[:max_length - len(extension)]
            filename = name_part + extension
        else:
            filename = filename[:max_length]
    
    return filename or 'untitled' + extension


# =============================================================================
# File Utilities
# =============================================================================

def ensure_directory(path: Union[str, Path]) -> Path:
    """
    Ensure a directory exists, create it if it doesn't.
    
    Args:
        path: Directory path
        
    Returns:
        Path to the directory
    """
    path = Path(path)
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    elif not path.is_dir():
        raise ValueError(f"Path exists and is not a directory: {path}")
    return path


def get_file_extension(path: Union[str, Path]) -> str:
    """
    Get file extension from path.
    
    Args:
        path: File path
        
    Returns:
        File extension (with dot)
    """
    path = Path(path)
    return path.suffix.lower()


def get_mime_type(extension: str) -> str:
    """
    Get MIME type for a file extension.
    
    Args:
        extension: File extension (with or without dot)
        
    Returns:
        MIME type
    """
    if extension.startswith('.'):
        extension = extension[1:]
    
    mime_types = {
        'html': 'text/html',
        'htm': 'text/html',
        'txt': 'text/plain',
        'json': 'application/json',
        'xml': 'application/xml',
        'csv': 'text/csv',
        'css': 'text/css',
        'js': 'application/javascript',
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'gif': 'image/gif',
        'svg': 'image/svg+xml',
        'pdf': 'application/pdf',
        'zip': 'application/zip',
        'tar': 'application/x-tar',
        'gz': 'application/gzip',
        'mp3': 'audio/mpeg',
        'mp4': 'video/mp4',
        'avi': 'video/x-msvideo',
        'mov': 'video/quicktime',
    }
    
    return mime_types.get(extension.lower(), 'application/octet-stream')
