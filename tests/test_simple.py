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
        self.assertIsInstance(data, dict)
        self.assertIsInstance(data['loras'], list)
        self.assertIn('error', data)

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

    def test_face_detail_requires_auth(self):
        response = self.client.post('/api/face-detail',
                                    json={'prompt': 'a face', 'image': '/images/x.png'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 302)

    def test_face_detail_super_count_not_integer(self):
        with self.client.session_transaction() as sess:
            sess['authenticated'] = True
        response = self.client.post('/api/face-detail',
                                    json={'prompt': 'a face', 'image': '/images/x.png',
                                          'count': 'abc'},
                                    content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('count', response.get_json()['error'])

    def test_face_detail_super_count_out_of_range(self):
        with self.client.session_transaction() as sess:
            sess['authenticated'] = True
        for bad in (0, 17, 100):
            response = self.client.post('/api/face-detail',
                                        json={'prompt': 'a face', 'image': '/images/x.png',
                                              'count': bad},
                                        content_type='application/json')
            self.assertEqual(response.status_code, 400, f'count={bad}')
            self.assertIn('between 1 and 16', response.get_json()['error'])

    def test_face_detail_super_valid_count_passes_validation(self):
        # A valid count clears the count check; the request then proceeds to
        # image resolution (which fails here because the image doesn't exist),
        # proving the count itself was accepted rather than rejected.
        with self.client.session_transaction() as sess:
            sess['authenticated'] = True
        response = self.client.post('/api/face-detail',
                                    json={'prompt': 'a face', 'image': '/images/nope.png',
                                          'count': 4},
                                    content_type='application/json')
        error = response.get_json().get('error', '')
        self.assertNotIn('count', error)
        self.assertNotIn('between 1 and 16', error)

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


class TestSettingsBackup(unittest.TestCase):
    """The /api/settings-backup ZIP endpoint."""

    def setUp(self):
        app.testing = True
        self.client = app.test_client()

    def test_requires_auth(self):
        response = self.client.get('/api/settings-backup')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)

    def test_returns_zip_with_settings(self):
        import io
        import zipfile
        import persistence

        # Seed a macro and a session; restore/remove them afterwards so the
        # test leaves the output folder as it found it.
        macros_path = persistence.macros_file()
        original_macros = macros_path.read_text() if macros_path.is_file() else None
        session_path = persistence.sessions_dir() / '__backup_test__.json'
        try:
            persistence.save_macros({'greet': ['hello']})
            persistence.save_session('__backup_test__', {'messages': []})

            with self.client.session_transaction() as sess:
                sess['authenticated'] = True
            response = self.client.get('/api/settings-backup')

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, 'application/zip')
            self.assertIn('attachment', response.headers.get('Content-Disposition', ''))

            names = zipfile.ZipFile(io.BytesIO(response.data)).namelist()
            self.assertIn('macros.json', names)
            self.assertIn('sessions/__backup_test__.json', names)
        finally:
            if session_path.is_file():
                session_path.unlink()
            if original_macros is None:
                if macros_path.is_file():
                    macros_path.unlink()
            else:
                macros_path.write_text(original_macros)


class TestSettingsRestore(unittest.TestCase):
    """The /api/settings-restore detect-and-restore endpoint."""

    def setUp(self):
        app.testing = True
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess['authenticated'] = True

    def _post(self, filename, raw, apply=False):
        import io
        data = {'file': (io.BytesIO(raw), filename)}
        if apply:
            data['apply'] = '1'
        return self.client.post('/api/settings-restore', data=data,
                                content_type='multipart/form-data')

    def test_requires_auth(self):
        response = app.test_client().post('/api/settings-restore')
        self.assertEqual(response.status_code, 302)

    def test_detect_macros_is_dry_run(self):
        import json
        import persistence
        before = persistence.load_macros()
        raw = json.dumps({'a': ['x'], 'b': ['y', 'z']}).encode()
        response = self._post('macros.json', raw, apply=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['detected']['kind'], 'macros')
        # A dry-run detection must not write anything.
        self.assertEqual(persistence.load_macros(), before)

    def test_detect_session(self):
        import json
        raw = json.dumps({'recordingName': 'My Sess',
                          'sessionImages': [], 'messages': []}).encode()
        det = self._post('my-sess.json', raw).get_json()['detected']
        self.assertEqual(det['kind'], 'session')

    def test_detect_servers(self):
        import json
        raw = json.dumps({'servers': [{'name': 's', 'host': 'h',
                                       'port': 8188, 'os': 'unix'}]}).encode()
        det = self._post('servers.json', raw).get_json()['detected']
        self.assertEqual(det['kind'], 'servers')

    def test_unrecognised_json_is_unknown(self):
        import json
        raw = json.dumps({'foo': 123, 'bar': True}).encode()
        det = self._post('whatever.json', raw).get_json()['detected']
        self.assertEqual(det['kind'], 'unknown')

    def test_apply_macros(self):
        import json
        import persistence
        macros_path = persistence.macros_file()
        original = macros_path.read_text() if macros_path.is_file() else None
        try:
            raw = json.dumps({'greet': ['hello', 'hi']}).encode()
            response = self._post('macros.json', raw, apply=True)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['restored']['macros'], 1)
            self.assertEqual(persistence.load_macros(), {'greet': ['hello', 'hi']})
        finally:
            if original is None:
                if macros_path.is_file():
                    macros_path.unlink()
            else:
                macros_path.write_text(original)

    def test_apply_full_backup_zip(self):
        import io
        import json
        import shutil
        import tempfile
        import zipfile
        from pathlib import Path
        import app as app_module
        import persistence

        macros_path = persistence.macros_file()
        aliases_path = persistence.aliases_file()
        orig_macros = macros_path.read_text() if macros_path.is_file() else None
        orig_aliases = aliases_path.read_text() if aliases_path.is_file() else None
        orig_wf = app_module.COMFY_WORKFLOW_DIR
        tmp = tempfile.mkdtemp()
        app_module.COMFY_WORKFLOW_DIR = Path(tmp)
        session_created = None
        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as zf:
                zf.writestr('macros.json', json.dumps({'m1': ['step']}))
                zf.writestr('aliases.json', json.dumps({'hq': 'high quality'}))
                zf.writestr('servers.json', json.dumps(
                    {'servers': [{'name': 's', 'host': 'h', 'port': 8188, 'os': 'unix'}]}))
                zf.writestr('sessions/restoreziptest.json',
                            json.dumps({'messages': [], 'sessionImages': []}))

            response = self._post('comfy-settings-backup.zip', buf.getvalue(), apply=True)
            self.assertEqual(response.status_code, 200)
            restored = response.get_json()['restored']

            self.assertEqual(restored['macros'], 1)
            self.assertEqual(restored['aliases'], 1)
            self.assertEqual(restored['servers'], 1)
            self.assertEqual(len(restored['sessions']), 1)
            session_created = persistence.sessions_dir() / f"{restored['sessions'][0]}.json"

            self.assertEqual(persistence.load_macros(), {'m1': ['step']})
            self.assertEqual(persistence.load_aliases(), {'hq': 'high quality'})
            self.assertTrue((Path(tmp) / 'servers.json').is_file())
            self.assertTrue(session_created.is_file())
        finally:
            app_module.COMFY_WORKFLOW_DIR = orig_wf
            shutil.rmtree(tmp, ignore_errors=True)
            if session_created and session_created.is_file():
                session_created.unlink()
            if orig_macros is None:
                if macros_path.is_file():
                    macros_path.unlink()
            else:
                macros_path.write_text(orig_macros)
            if orig_aliases is None:
                if aliases_path.is_file():
                    aliases_path.unlink()
            else:
                aliases_path.write_text(orig_aliases)


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
