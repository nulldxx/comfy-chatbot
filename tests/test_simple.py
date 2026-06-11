import unittest
import sys
import os

# Add the app directory to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

class TestSimple(unittest.TestCase):
    
    def setUp(self):
        """Set up test client"""
        self.app = app.test_client()
        self.app.testing = True
    
    def test_health_endpoint(self):
        """Test that the health endpoint works correctly"""
        response = self.app.get('/health')
        self.assertEqual(response.status_code, 200)
        
        # Check that response is JSON
        data = response.get_json()
        self.assertIsNotNone(data)
        
        # Check required fields are present
        self.assertIn('status', data)
        self.assertEqual(data['status'], 'healthy')
    
    def test_login_page_loads(self):
        """Test that login page loads correctly"""
        response = self.app.get('/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Flask App', response.data)
        self.assertIn(b'username', response.data)
        self.assertIn(b'password', response.data)
    
    def test_main_page_requires_auth(self):
        """Test that main page redirects to login when not authenticated"""
        response = self.app.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)
    
    def test_api_endpoint_requires_auth(self):
        """Test that API endpoint requires authentication"""
        response = self.app.get('/api/data')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)
    
    def test_login_with_valid_credentials(self):
        """Test login with valid credentials"""
        response = self.app.post('/login', data={
            'username': 'user',
            'password': 'password'
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('/', response.location)
    
    def test_login_with_invalid_credentials(self):
        """Test login with invalid credentials"""
        response = self.app.post('/login', data={
            'username': 'wrong',
            'password': 'credentials'
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Invalid username or password', response.data)
    
    def test_authenticated_main_page(self):
        """Test main page when authenticated"""
        with self.app.session_transaction() as sess:
            sess['authenticated'] = True
        
        response = self.app.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Flask Application', response.data)
        self.assertIn(b'Welcome to your Flask App!', response.data)
    
    def test_authenticated_api_endpoint(self):
        """Test API endpoint when authenticated"""
        with self.app.session_transaction() as sess:
            sess['authenticated'] = True
        
        response = self.app.get('/api/data')
        self.assertEqual(response.status_code, 200)
        
        data = response.get_json()
        self.assertIsNotNone(data)
        self.assertIn('message', data)
        self.assertIn('status', data)
        self.assertEqual(data['status'], 'success')
    
    def test_logout(self):
        """Test logout functionality"""
        # First authenticate
        with self.app.session_transaction() as sess:
            sess['authenticated'] = True
        
        # Then logout
        response = self.app.get('/logout')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)
        
        # Verify we can't access protected pages anymore
        response = self.app.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)
    
    def test_requests_module_available(self):
        """Test that requests module is available for health checks"""
        try:
            import requests
            # Test that we can create a session (basic functionality)
            session = requests.Session()
            self.assertIsNotNone(session)
        except ImportError:
            self.fail("requests module is not available - required for Docker health checks")

if __name__ == '__main__':
    unittest.main()