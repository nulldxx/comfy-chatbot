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

    def test_save_session_atomic_no_tmp_left(self):
        persistence.save_session("s", {"data": 1})
        d = Path(self.tmp) / "sessions"
        leftovers = list(d.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    # --- append_session_image ---

    def test_append_creates_file(self):
        persistence.append_session_image("run1", "/images/a.png", "a cat")
        path = Path(self.tmp) / "sessions" / "run1.json"
        self.assertTrue(path.is_file())
        doc = json.loads(path.read_text())
        self.assertEqual(doc["sessionImages"], ["/images/a.png"])
        self.assertEqual(doc["imagePrompts"]["/images/a.png"], "a cat")
        self.assertEqual(doc["recordingName"], "run1")

    def test_append_builds_user_and_bot_messages(self):
        persistence.append_session_image("run1", "/images/a.png", "a cat")
        doc = json.loads((Path(self.tmp) / "sessions" / "run1.json").read_text())
        self.assertEqual(doc["messages"], [
            {"role": "user", "prompt": "a cat"},
            {"role": "bot", "images": ["/images/a.png"], "text": ""},
        ])

    def test_append_accumulates(self):
        persistence.append_session_image("run1", "/images/a.png", "a cat")
        persistence.append_session_image("run1", "/images/b.png", "a dog")
        doc = json.loads((Path(self.tmp) / "sessions" / "run1.json").read_text())
        self.assertEqual(doc["sessionImages"], ["/images/a.png", "/images/b.png"])
        self.assertEqual(len(doc["messages"]), 4)

    def test_append_dedups_url(self):
        persistence.append_session_image("run1", "/images/a.png", "a cat")
        persistence.append_session_image("run1", "/images/a.png", "a cat again")
        doc = json.loads((Path(self.tmp) / "sessions" / "run1.json").read_text())
        self.assertEqual(doc["sessionImages"], ["/images/a.png"])

    def test_append_stores_video_meta(self):
        persistence.append_session_image(
            "run1", "/images/a.png", "a cat", video_meta={"action": "runs", "audio": "meow"}
        )
        doc = json.loads((Path(self.tmp) / "sessions" / "run1.json").read_text())
        self.assertEqual(doc["imageVideoMeta"]["/images/a.png"], {"action": "runs", "audio": "meow"})

    def test_append_seeds_settings_once(self):
        persistence.append_session_image("run1", "/images/a.png", "a", settings={"workflow": "wf1"})
        persistence.append_session_image("run1", "/images/b.png", "b", settings={"workflow": "wf2"})
        doc = json.loads((Path(self.tmp) / "sessions" / "run1.json").read_text())
        self.assertEqual(doc["settings"], {"workflow": "wf1"})

    def test_append_output_loads_cleanly(self):
        # A file the server appended to must be readable by load_session.
        real = Path(self.tmp) / "real.png"
        real.write_bytes(b"\x89PNG")
        persistence.append_session_image("run1", "/images/real.png", "a cat")
        data = persistence.load_session("run1")
        self.assertEqual(data["sessionImages"], ["/images/real.png"])

    # --- append_session_note ---

    def test_note_creates_file(self):
        persistence.append_session_note("run1", "a cat", "⚠ Generation failed: boom")
        path = Path(self.tmp) / "sessions" / "run1.json"
        self.assertTrue(path.is_file())
        doc = json.loads(path.read_text())
        self.assertEqual(doc["messages"], [
            {"role": "user", "prompt": "a cat"},
            {"role": "bot", "images": [], "text": "⚠ Generation failed: boom"},
        ])
        self.assertEqual(doc["sessionImages"], [])
        self.assertEqual(doc["recordingName"], "run1")

    def test_note_does_not_touch_session_images(self):
        persistence.append_session_image("run1", "/images/a.png", "a cat")
        persistence.append_session_note("run1", "a dog", "⚠ Generation failed: boom")
        doc = json.loads((Path(self.tmp) / "sessions" / "run1.json").read_text())
        self.assertEqual(doc["sessionImages"], ["/images/a.png"])
        self.assertEqual(len(doc["messages"]), 4)

    def test_note_survives_load_session_filtering(self):
        # A text-only bot message (no images) must survive load_session's filter,
        # which drops bot messages with no images AND no text — this one has text.
        persistence.append_session_note("run1", "a cat", "⚠ Generation failed: boom")
        data = persistence.load_session("run1")
        bot_msgs = [m for m in data["messages"] if m["role"] == "bot"]
        self.assertEqual(len(bot_msgs), 1)
        self.assertEqual(bot_msgs[0]["text"], "⚠ Generation failed: boom")

    # --- rename_session ---

    def test_rename_moves_file_and_rewrites_name(self):
        persistence.append_session_image("temp-x", "/images/a.png", "a cat")
        persistence.rename_session("temp-x", "sunsets")
        d = Path(self.tmp) / "sessions"
        self.assertFalse((d / "temp-x.json").exists())
        self.assertTrue((d / "sunsets.json").exists())
        doc = json.loads((d / "sunsets.json").read_text())
        self.assertEqual(doc["recordingName"], "sunsets")
        self.assertEqual(doc["sessionImages"], ["/images/a.png"])

    def test_rename_missing_src_raises(self):
        with self.assertRaises(FileNotFoundError):
            persistence.rename_session("nope", "dst")

    def test_rename_existing_dst_raises(self):
        persistence.save_session("a", {})
        persistence.save_session("b", {})
        with self.assertRaises(FileExistsError):
            persistence.rename_session("a", "b")

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
