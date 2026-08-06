#!/usr/bin/env python3
import sys
import os

# Add the project directories to path
project_root = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(project_root, 'src')
config_path = os.path.join(project_root, 'config')

sys.path.insert(0, src_path)
sys.path.insert(0, config_path)
sys.path.insert(0, project_root)

print("Testing module imports...")

# Test direct imports from each file
print("1. Testing retry.py...")
try:
    import retry
    print("   ✓ retry.py can be imported")
except ImportError as e:
    print(f"   ❌ retry.py import failed: {e}")

print("2. Testing session.py...")
try:
    import session
    print("   ✓ session.py can be imported")
except ImportError as e:
    print(f"   ❌ session.py import failed: {e}")

print("3. Testing proxy.py...")
try:
    import proxy
    print("   ✓ proxy.py can be imported")
except ImportError as e:
    print(f"   ❌ proxy.py import failed: {e}")

print("4. Testing schemas.py...")
try:
    import schemas
    print("   ✓ schemas.py can be imported")
    print(f"   ✓ Has RetryConfig: {hasattr(schemas, 'RetryConfig')}")
except ImportError as e:
    print(f"   ❌ schemas.py import failed: {e}")

# Test package imports
print("\n5. Testing package structure...")

# Change to src directory and test
os.chdir(src_path)
sys.path.insert(0, os.getcwd())

print("   Current directory:", os.getcwd())
print("   Python path:", sys.path[:3])

try:
    from scrapling_tool.core import retry
    print("   ✓ Can import from scrapling_tool.core.retry")
except ImportError as e:
    print(f"   ❌ Cannot import from scrapling_tool.core.retry: {e}")

try:
    from scrapling_tool.core import session
    print("   ✓ Can import from scrapling_tool.core.session")
except ImportError as e:
    print(f"   ❌ Cannot import from scrapling_tool.core.session: {e}")

try:
    from scrapling_tool.core import proxy
    print("   ✓ Can import from scrapling_tool.core.proxy")
except ImportError as e:
    print(f"   ❌ Cannot import from scrapling_tool.core.proxy: {e}")

# Test actual classes
print("\n6. Testing class instantiation...")

try:
    from scrapling_tool.core.retry import RetryStrategy, ExponentialBackoff
    strategy = ExponentialBackoff()
    print(f"   ✓ Created ExponentialBackoff: {strategy}")
except Exception as e:
    print(f"   ❌ Failed to create ExponentialBackoff: {e}")

try:
    from scrapling_tool.core.proxy import ProxyInfo
    proxy = ProxyInfo(url="http://test.com:8080")
    print(f"   ✓ Created ProxyInfo: {proxy}")
except Exception as e:
    print(f"   ❌ Failed to create ProxyInfo: {e}")

try:
    from config.schemas import RetryConfig, Configuration
    config = Configuration()
    print(f"   ✓ Created Configuration with retry: {hasattr(config, 'retry')}")
    if hasattr(config, 'retry'):
        print(f"   ✓ RetryConfig type: {type(config.retry).__name__}")
        print(f"   ✓ RetryConfig max_retries: {config.retry.max_retries}")
except Exception as e:
    print(f"   ❌ Failed to create Configuration: {e}")

print("\n7. Testing core __init__.py imports...")

try:
    from scrapling_tool.core import (
        EnhancedFetcher, SmartProxyRotator, SessionManager,
        RateLimiter, RetryStrategy, clean_url
    )
    print("   ✓ All core module imports work")
except ImportError as e:
    print(f"   ❌ Core module import failed: {e}")

print("\n✅ Import testing complete!")