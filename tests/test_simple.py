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


def _load_archive_agent():
    """Load the archive-agent script (no .py extension) as a module so its pure
    helpers can be unit-tested. The extensionless filename means we must hand
    importlib a SourceFileLoader explicitly rather than letting it infer one."""
    import importlib.util
    from importlib.machinery import SourceFileLoader
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'packaging', 'agent', 'archive-agent',
    )
    loader = SourceFileLoader('archive_agent', path)
    spec = importlib.util.spec_from_loader('archive_agent', loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class TestE2fsckInterpretation(unittest.TestCase):
    """The e2fsck exit-code -> structured-result mapping in the archive-agent."""

    @classmethod
    def setUpClass(cls):
        # staticmethod so storing the plain function on the class doesn't turn it
        # into a bound method that would pass `self` as the first argument.
        cls.interpret = staticmethod(_load_archive_agent().interpret_e2fsck)

    def test_clean(self):
        r = self.interpret(0)
        self.assertTrue(r['ok'])
        self.assertTrue(r['clean'])
        self.assertFalse(r['corrected'])
        self.assertFalse(r['uncorrected'])

    def test_errors_corrected(self):
        r = self.interpret(1)
        self.assertTrue(r['ok'])
        self.assertFalse(r['clean'])
        self.assertTrue(r['corrected'])
        self.assertFalse(r['uncorrected'])

    def test_corrected_reboot_bit(self):
        r = self.interpret(2)
        self.assertTrue(r['ok'])
        self.assertTrue(r['corrected'])
        self.assertFalse(r['uncorrected'])

    def test_errors_uncorrected(self):
        r = self.interpret(4)
        # The check ran (ok) but couldn't fix everything.
        self.assertTrue(r['ok'])
        self.assertFalse(r['clean'])
        self.assertTrue(r['uncorrected'])

    def test_corrected_and_uncorrected(self):
        r = self.interpret(1 | 4)
        self.assertTrue(r['corrected'])
        self.assertTrue(r['uncorrected'])
        self.assertTrue(r['ok'])

    def test_operational_error(self):
        r = self.interpret(8)
        self.assertFalse(r['ok'])
        self.assertIn('error', r)

    def test_usage_error(self):
        r = self.interpret(16)
        self.assertFalse(r['ok'])

    def test_output_passed_through(self):
        r = self.interpret(0, 'clean output')
        self.assertEqual(r['output'], 'clean output')
        self.assertEqual(r['exit_code'], 0)


if __name__ == '__main__':
    unittest.main()
