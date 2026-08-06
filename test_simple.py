#!/usr/bin/env python3
import sys
import os

# Add paths
project_root = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(project_root, 'src')
sys.path.insert(0, src_path)
sys.path.insert(0, project_root)

print("Testing basic imports...")

try:
    print("Testing retry module...")
    from scrapling_tool.core.retry import RetryStrategy, ExponentialBackoff
    print("✓ Retry module works")
    
    print("Testing session module...")
    from scrapling_tool.core.session import SessionManager
    print("✓ Session module works")
    
    print("Testing proxy module...")
    from scrapling_tool.core.proxy import SmartProxyRotator
    print("✓ Proxy module works")
    
    print("Testing config...")
    from config.schemas import RetryConfig, Configuration
    print("✓ Config works")
    
    # Test functionality
    print("\nTesting functionality...")
    
    # Test retry
    from scrapling_tool.core.retry import with_exponential_backoff
    strategy = with_exponential_backoff()
    print(f"✓ Created retry strategy: {strategy}")
    
    # Test proxy
    from scrapling_tool.core.proxy import ProxyInfo
    proxy = ProxyInfo(url="http://test.com:8080")
    print(f"✓ Created proxy: {proxy}")
    
    # Test config
    config = Configuration()
    print(f"✓ Config has retry: {hasattr(config, 'retry')}")
    print(f"✓ Retry config: {config.retry.max_retries}")
    
    print("\n🎉 All basic tests passed!")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)