import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import persistence


class TestSlugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(persistence.slugify("Man walking on Beach"), "man-walking-on-beach")

    def test_strips_leading_trailing_hyphens(self):
        self.assertEqual(persistence.slugify("  !!! "), "")

    def test_numbers_preserved(self):
        self.assertEqual(persistence.slugify("session 2024"), "session-2024")

    def test_none_returns_empty(self):
        self.assertEqual(persistence.slugify(None), "")

    def test_empty_string(self):
        self.assertEqual(persistence.slugify(""), "")

    def test_already_slug(self):
        self.assertEqual(persistence.slugify("my-slug"), "my-slug")

    def test_special_chars_collapsed(self):
        result = persistence.slugify("a  --  b")
        self.assertEqual(result, "a-b")


class TestSessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._patcher = patch.object(persistence, "IMAGES_DIR", Path(self.tmp))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sessions_dir(self):
        d = Path(self.tmp) / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- list_sessions ---

    def test_list_sessions_empty(self):
        self.assertEqual(persistence.list_sessions(), [])

    def test_list_sessions_returns_entries(self):
        d = self._sessions_dir()
        (d / "mysession.json").write_text(json.dumps({"sessionImages": ["a.png", "b.png"]}))
        result = persistence.list_sessions()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "mysession")
        self.assertEqual(result[0]["image_count"], 2)

    def test_list_sessions_corrupt_file_still_lists(self):
        d = self._sessions_dir()
        (d / "bad.json").write_text("not json")
        result = persistence.list_sessions()
        self.assertEqual(result[0]["name"], "bad")
        self.assertEqual(result[0]["image_count"], 0)

    def test_list_sessions_sorted_newest_first(self):
        d = self._sessions_dir()
        import time
        (d / "old.json").write_text("{}")
        time.sleep(0.05)
        (d / "new.json").write_text("{}")
        result = persistence.list_sessions()
        self.assertEqual(result[0]["name"], "new")

    # --- save_session ---

    def test_save_session_creates_file(self):
        persistence.save_session("mysession", {"sessionImages": [], "messages": []})
        path = Path(self.tmp) / "sessions" / "mysession.json"
        self.assertTrue(path.is_file())

    def test_save_session_strips_name_key(self):
        persistence.save_session("s", {"name": "should-be-stripped", "data": 1})
        data = json.loads((Path(self.tmp) / "sessions" / "s.json").read_text())
        self.assertNotIn("name", data)

    def test_save_session_adds_saved_at(self):
        persistence.save_session("s", {})
        data = json.loads((Path(self.tmp) / "sessions" / "s.json").read_text())
        self.assertIn("saved_at", data)

    # --- load_session ---

    def test_load_session_not_found(self):
        with self.assertRaises(FileNotFoundError):
            persistence.load_session("nonexistent")

    def test_load_session_returns_data(self):
        d = self._sessions_dir()
        payload = {"sessionImages": [], "messages": [], "imagePrompts": {}}
        (d / "s.json").write_text(json.dumps(payload))
        data = persistence.load_session("s")
        self.assertIn("sessionImages", data)

    def test_load_session_filters_missing_images(self):
        d = self._sessions_dir()
        # One image that exists, one that doesn't
        real = Path(self.tmp) / "real.png"
        real.write_bytes(b"\x89PNG")
        payload = {
            "sessionImages": ["/images/real.png", "/images/ghost.png"],
            "imagePrompts": {"/images/real.png": "a", "/images/ghost.png": "b"},
            "imageVideoMeta": {
                "/images/real.png": {"action": "x", "audio": "y"},
                "/images/ghost.png": {"action": "p", "audio": "q"},
            },
            "messages": [
                {"role": "bot", "images": ["/images/real.png", "/images/ghost.png"], "text": ""}
            ],
        }
        (d / "s.json").write_text(json.dumps(payload))
        data = persistence.load_session("s")
        self.assertEqual(data["sessionImages"], ["/images/real.png"])
        self.assertNotIn("/images/ghost.png", data["imagePrompts"])
        self.assertNotIn("/images/ghost.png", data["imageVideoMeta"])
        self.assertIn("/images/real.png", data["imageVideoMeta"])
        self.assertEqual(data["messages"][0]["images"], ["/images/real.png"])

    def test_load_session_drops_bot_message_with_no_images_no_text(self):
        d = self._sessions_dir()
        payload = {
            "sessionImages": [],
            "imagePrompts": {},
            "messages": [
                {"role": "bot", "images": ["/images/ghost.png"], "text": ""},
                {"role": "user", "text": "hi"},
            ],
        }
        (d / "s.json").write_text(json.dumps(payload))
        data = persistence.load_session("s")
        roles = [m["role"] for m in data["messages"]]
        self.assertNotIn("bot", roles)

    # --- delete_session ---

    def test_delete_session(self):
        d = self._sessions_dir()
        (d / "s.json").write_text("{}")
        persistence.delete_session("s")
        self.assertFalse((d / "s.json").exists())

    def test_delete_session_not_found(self):
        self._sessions_dir()
        with self.assertRaises(FileNotFoundError):
            persistence.delete_session("nope")


class TestAliases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._patcher = patch.object(persistence, "IMAGES_DIR", Path(self.tmp))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_aliases_missing_file_returns_empty(self):
        self.assertEqual(persistence.load_aliases(), {})

    def test_save_and_load_roundtrip(self):
        aliases = {"shortcut": "a long prompt about cats"}
        persistence.save_aliases(aliases)
        self.assertEqual(persistence.load_aliases(), aliases)

    def test_load_aliases_corrupt_returns_empty(self):
        (Path(self.tmp) / "aliases.json").write_text("not json")
        self.assertEqual(persistence.load_aliases(), {})

    def test_save_overwrites(self):
        persistence.save_aliases({"a": "1"})
        persistence.save_aliases({"b": "2"})
        self.assertEqual(persistence.load_aliases(), {"b": "2"})


if __name__ == "__main__":
    unittest.main()
