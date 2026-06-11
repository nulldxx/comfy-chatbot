#!/usr/bin/env python3
"""
Test script to verify all required imports work correctly
"""

print("Testing all imports...")

try:
    from flask import Flask
    print("✅ Flask import successful")
except ImportError as e:
    print(f"❌ Flask import failed: {e}")

try:
    import requests
    print(f"✅ requests import successful (version: {requests.__version__})")
    
    # Test that requests can handle basic functionality (without making actual requests)
    # This ensures the module is properly installed and functional
    session = requests.Session()
    print("✅ requests basic functionality works")
    
except ImportError as e:
    print(f"❌ requests import failed: {e}")
except Exception as e:
    print(f"❌ requests functionality test failed: {e}")

try:
    from werkzeug.serving import WSGIRequestHandler
    print("✅ Werkzeug import successful")
except ImportError as e:
    print(f"❌ Werkzeug import failed: {e}")

try:
    import gunicorn
    print("✅ Gunicorn import successful")
except ImportError as e:
    print(f"❌ Gunicorn import failed: {e}")

print("\n🔍 Summary:")
print("All imports should work for both local development and Docker deployment")
print("requests is required for Docker health checks")
print("gunicorn is used for production deployment")