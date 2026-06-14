import unittest
import sys
import os

# Add the app directory to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app


class TestComfyChatbot(unittest.TestCase):

    def setUp(self):
        app.testing = True
        self.client = app.test_client()

    def test_health_endpoint(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn('status', data)
        self.assertEqual(data['status'], 'healthy')

    def test_login_page_loads(self):
        response = self.client.get('/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'username', response.data)
        self.assertIn(b'password', response.data)

    def test_main_page_requires_auth(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)

    def test_api_loras_requires_auth(self):
        response = self.client.get('/api/loras')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)

    def test_login_with_valid_credentials(self):
        response = self.client.post('/login', data={
            'username': 'user',
            'password': 'password',
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('/', response.location)

    def test_login_with_invalid_credentials(self):
        response = self.client.post('/login', data={
            'username': 'wrong',
            'password': 'credentials',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Invalid username or password', response.data)

    def test_authenticated_main_page(self):
        with self.client.session_transaction() as sess:
            sess['authenticated'] = True
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'ComfyUI', response.data)

    def test_authenticated_loras_endpoint(self):
        with self.client.session_transaction() as sess:
            sess['authenticated'] = True
        response = self.client.get('/api/loras')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsInstance(data, list)

    def test_generate_requires_auth(self):
        response = self.client.post('/api/generate',
                                    json={'prompt': 'a cat'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 302)

    def test_generate_requires_prompt(self):
        with self.client.session_transaction() as sess:
            sess['authenticated'] = True
        response = self.client.post('/api/generate',
                                    json={},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertIn('error', data)

    def test_cancel_requires_auth(self):
        response = self.client.post('/api/cancel/some-job-id')
        self.assertEqual(response.status_code, 302)

    def test_cancel_unknown_job(self):
        with self.client.session_transaction() as sess:
            sess['authenticated'] = True
        response = self.client.post('/api/cancel/does-not-exist')
        self.assertEqual(response.status_code, 404)
        data = response.get_json()
        self.assertIn('error', data)

    def test_logout(self):
        with self.client.session_transaction() as sess:
            sess['authenticated'] = True
        response = self.client.get('/logout')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)

    def test_requests_module_available(self):
        import requests
        session = requests.Session()
        self.assertIsNotNone(session)


if __name__ == '__main__':
    unittest.main()
