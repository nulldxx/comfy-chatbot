import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import image_store


class _PatchedImageStore:
    """Context manager that points image_store at a temporary directory."""
    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self._patchers = []

    def __enter__(self):
        images_dir = self.tmp / "images"
        masks_dir = images_dir / ".masks"
        inpaint_inputs_dir = images_dir / ".inpaint-inputs"
        images_dir.mkdir(parents=True)
        masks_dir.mkdir()
        inpaint_inputs_dir.mkdir()

        for attr, val in [
            ("IMAGES_DIR", images_dir),
            ("MASKS_DIR", masks_dir),
            ("INPAINT_INPUTS_DIR", inpaint_inputs_dir),
        ]:
            p = patch.object(image_store, attr, val)
            p.start()
            self._patchers.append(p)
        return images_dir, masks_dir, inpaint_inputs_dir

    def __exit__(self, *args):
        for p in self._patchers:
            p.stop()


class TestMaskTokens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        image_store.mask_tokens.clear()

    def tearDown(self):
        image_store.mask_tokens.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_register_and_resolve(self):
        with _PatchedImageStore(self.tmp) as (images_dir, masks_dir, _):
            from app import app
            token = image_store.register_mask_token("alice", b"\x89PNG")
            with app.app_context():
                path, err = image_store.resolve_mask(token, "alice")
            self.assertIsNone(err)
            self.assertTrue(path.is_file())

    def test_single_use(self):
        with _PatchedImageStore(self.tmp) as (images_dir, masks_dir, _):
            from app import app
            token = image_store.register_mask_token("alice", b"\x89PNG")
            with app.app_context():
                image_store.resolve_mask(token, "alice")
                _, err = image_store.resolve_mask(token, "alice")
            resp, status = err
            self.assertEqual(status, 404)

    def test_wrong_user_rejected(self):
        with _PatchedImageStore(self.tmp) as (images_dir, masks_dir, _):
            from app import app
            token = image_store.register_mask_token("alice", b"\x89PNG")
            with app.app_context():
                _, err = image_store.resolve_mask(token, "bob")
            resp, status = err
            self.assertEqual(status, 404)

    def test_unknown_token_rejected(self):
        with _PatchedImageStore(self.tmp):
            from app import app
            with app.app_context():
                _, err = image_store.resolve_mask("no-such-token", "alice")
            resp, status = err
            self.assertEqual(status, 404)

    def test_deleted_file_returns_404(self):
        with _PatchedImageStore(self.tmp) as (images_dir, masks_dir, _):
            from app import app
            token = image_store.register_mask_token("alice", b"\x89PNG")
            # Simulate the file being deleted between registration and resolution
            for path in masks_dir.iterdir():
                path.unlink()
            with app.app_context():
                _, err = image_store.resolve_mask(token, "alice")
            resp, status = err
            self.assertEqual(status, 404)


class TestDrawTokens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        image_store.draw_tokens.clear()

    def tearDown(self):
        image_store.draw_tokens.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_register_and_resolve(self):
        with _PatchedImageStore(self.tmp) as (images_dir, _, inpaint_inputs_dir):
            from app import app
            token = image_store.register_draw_token("alice", b"\x89PNG")
            with app.app_context():
                path, err = image_store.resolve_draw_image(token, "alice")
            self.assertIsNone(err)
            self.assertTrue(path.is_file())

    def test_single_use(self):
        with _PatchedImageStore(self.tmp):
            from app import app
            token = image_store.register_draw_token("alice", b"\x89PNG")
            with app.app_context():
                image_store.resolve_draw_image(token, "alice")
                _, err = image_store.resolve_draw_image(token, "alice")
            resp, status = err
            self.assertEqual(status, 404)

    def test_wrong_user_rejected(self):
        with _PatchedImageStore(self.tmp):
            from app import app
            token = image_store.register_draw_token("alice", b"\x89PNG")
            with app.app_context():
                _, err = image_store.resolve_draw_image(token, "bob")
            resp, status = err
            self.assertEqual(status, 404)

    def test_deleted_file_returns_404(self):
        with _PatchedImageStore(self.tmp) as (images_dir, _, inpaint_inputs_dir):
            from app import app
            token = image_store.register_draw_token("alice", b"\x89PNG")
            for path in inpaint_inputs_dir.iterdir():
                path.unlink()
            with app.app_context():
                _, err = image_store.resolve_draw_image(token, "alice")
            resp, status = err
            self.assertEqual(status, 404)


class TestResolveInputImage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_image_found(self):
        with _PatchedImageStore(self.tmp) as (images_dir, _, __):
            img = images_dir / "photo.png"
            img.write_bytes(b"\x89PNG")
            from app import app
            with app.app_context():
                safe, path, err = image_store.resolve_input_image("/images/photo.png")
            self.assertIsNone(err)
            self.assertEqual(safe, "photo.png")
            self.assertEqual(path, img)

    def test_missing_image_returns_404(self):
        with _PatchedImageStore(self.tmp):
            from app import app
            with app.app_context():
                _, _, err = image_store.resolve_input_image("/images/nope.png")
            resp, status = err
            self.assertEqual(status, 404)

    def test_invalid_extension_returns_400(self):
        with _PatchedImageStore(self.tmp):
            from app import app
            with app.app_context():
                _, _, err = image_store.resolve_input_image("/images/file.txt")
            resp, status = err
            self.assertEqual(status, 400)

    def test_filename_altered_by_secure_filename_rejected(self):
        # rsplit("/",1) extracts "my photo.png"; secure_filename adds underscore → 400
        with _PatchedImageStore(self.tmp):
            from app import app
            with app.app_context():
                _, _, err = image_store.resolve_input_image("/images/my photo.png")
            resp, status = err
            self.assertEqual(status, 400)


class TestSelectImages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_image(self, images_dir, name):
        p = Path(images_dir) / name
        p.write_bytes(b"\x89PNG")
        return p

    def test_all_scope(self):
        with _PatchedImageStore(self.tmp) as (images_dir, _, __):
            self._make_image(images_dir, "a.png")
            self._make_image(images_dir, "b.png")
            result = image_store.select_images("all")
        self.assertEqual(len(result), 2)

    def test_today_scope_includes_today_files(self):
        with _PatchedImageStore(self.tmp) as (images_dir, _, __):
            self._make_image(images_dir, "today.png")
            from datetime import datetime, timedelta
            old_path = images_dir / "old.png"
            old_path.write_bytes(b"\x89PNG")
            past = (datetime.now() - timedelta(days=5)).timestamp()
            os.utime(str(old_path), (past, past))
            result = image_store.select_images("today")
        names = [p.name for p in result]
        self.assertIn("today.png", names)
        self.assertNotIn("old.png", names)

    def test_session_scope_validates_filenames(self):
        with _PatchedImageStore(self.tmp) as (images_dir, _, __):
            self._make_image(images_dir, "s.png")
            result = image_store.select_images("session", filenames=["s.png"])
        self.assertEqual(len(result), 1)

    def test_session_scope_rejects_traversal(self):
        # "../etc/passwd" → secure_filename → "passwd" ≠ original → ValueError
        with _PatchedImageStore(self.tmp):
            with self.assertRaises(ValueError):
                image_store.select_images("session", filenames=["../etc/passwd"])

    def test_session_scope_rejects_filename_with_spaces(self):
        # "my file.png" → secure_filename → "my_file.png" ≠ original → ValueError
        with _PatchedImageStore(self.tmp):
            with self.assertRaises(ValueError):
                image_store.select_images("session", filenames=["my file.png"])

    def test_session_scope_rejects_bad_extension(self):
        with _PatchedImageStore(self.tmp):
            with self.assertRaises(ValueError):
                image_store.select_images("session", filenames=["file.exe"])

    def test_unknown_scope_raises(self):
        with _PatchedImageStore(self.tmp):
            with self.assertRaises(ValueError):
                image_store.select_images("invalid")

    def test_all_excludes_non_image_files(self):
        with _PatchedImageStore(self.tmp) as (images_dir, _, __):
            self._make_image(images_dir, "a.png")
            (images_dir / "readme.txt").write_text("hi")
            result = image_store.select_images("all")
        self.assertEqual(len(result), 1)

    def test_session_skips_missing_files(self):
        with _PatchedImageStore(self.tmp):
            result = image_store.select_images("session", filenames=["ghost.png"])
        self.assertEqual(result, [])


class TestOutputStorageError(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disabled_returns_none(self):
        with _PatchedImageStore(self.tmp) as (images_dir, _, __):
            from app import app
            with patch.object(image_store, "OUTPUT_VOLUME", ""):
                with app.app_context():
                    self.assertIsNone(image_store.output_storage_error())

    def test_enabled_without_marker_returns_503(self):
        with _PatchedImageStore(self.tmp) as (images_dir, _, __):
            from app import app
            with patch.object(image_store, "OUTPUT_VOLUME", "/host/output.luks"):
                with app.app_context():
                    result = image_store.output_storage_error()
            resp, status = result
            self.assertEqual(status, 503)

    def test_enabled_with_marker_returns_none(self):
        with _PatchedImageStore(self.tmp) as (images_dir, _, __):
            from app import app
            (images_dir / image_store.OUTPUT_MARKER).write_text("ok")
            with patch.object(image_store, "OUTPUT_VOLUME", "/host/output.luks"):
                with app.app_context():
                    self.assertIsNone(image_store.output_storage_error())


if __name__ == "__main__":
    unittest.main()
