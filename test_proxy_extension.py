#!/usr/bin/env python3
"""
Quick test of the updated proxy extension functionality.
"""
import sys
sys.path.insert(0, '/Users/sqrtv2/Project/PF')

print("Testing proxy extension creation...")

try:
    from core.browser_manager import BrowserManager
    
    manager = BrowserManager()
    
    # Test extension creation
    ext_path = manager._create_proxy_extension(
        host='185.234.59.13',
        port=12138,
        username='Hes9yF',
        password='zAU2vaEUf4TU',
        proxy_type='socks5'
    )
    
    print(f"✅ Extension created at: {ext_path}")
    
    import os
    if os.path.exists(ext_path):
        print(f"✅ File exists: {os.path.getsize(ext_path)} bytes")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
