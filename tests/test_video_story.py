"""Tests for /video-story: grok.generate_video_story / _parse_video_story and
POST /api/video-story."""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grok
from grok import GrokError, _parse_video_story, generate_video_story

import app as app_module
from app import app


def _reply(obj):
    return json.dumps(obj)


class TestParseVideoStory(unittest.TestCase):
    def test_parses_clips_and_forces_music_na(self):
        out = _parse_video_story(_reply({"clips": [
            {"description": " The man waves. ", "soundscape": "Wind.", "music": "Strings"},
            {"description": "He walks off.", "soundscape": ""},
        ]}))
        self.assertEqual(out, [
            {"description": "The man waves.", "soundscape": "Wind.", "music": "N/A"},
            {"description": "He walks off.", "soundscape": "", "music": "N/A"},
        ])

    def test_drops_items_without_description(self):
        out = _parse_video_story(_reply({"clips": [
            {"description": "", "soundscape": "Rain."},
            "not a dict",
            {"description": "She laughs."},
        ]}))
        self.assertEqual([c["description"] for c in out], ["She laughs."])

    def test_accepts_prompts_key(self):
        out = _parse_video_story(_reply({"prompts": [{"description": "A door opens."}]}))
        self.assertEqual(out[0]["description"], "A door opens.")

    def test_no_usable_clips_raises(self):
        with self.assertRaises(GrokError):
            _parse_video_story(_reply({"clips": [{"soundscape": "x"}]}))
        with self.assertRaises(GrokError):
            _parse_video_story("no json here")


class TestGenerateVideoStory(unittest.TestCase):
    def test_prompt_carries_count_and_clip_seconds(self):
        captured = {}

        def fake_chat(messages, **kw):
            captured["user"] = messages[1]["content"]
            return _reply({"clips": [{"description": "x", "soundscape": "y"}]})

        with patch.object(grok, "_chat", side_effect=fake_chat):
            out = generate_video_story("two friends hiking", 4, 6.5)
        self.assertEqual(len(out), 1)
        self.assertIn("exactly 4 consecutive video clips", captured["user"])
        self.assertIn("about 6.5 seconds", captured["user"])
        self.assertIn("two friends hiking", captured["user"])

    def test_whole_seconds_formatted_without_decimal(self):
        captured = {}

        def fake_chat(messages, **kw):
            captured["user"] = messages[1]["content"]
            return _reply({"clips": [{"description": "x"}]})

        with patch.object(grok, "_chat", side_effect=fake_chat):
            generate_video_story("m", 2, 5.0)
        self.assertIn("about 5 seconds", captured["user"])

    def test_falls_back_to_second_model(self):
        calls = []

        def fake_chat(messages, model=None, **kw):
            calls.append(model)
            if len(calls) == 1:
                return "garbage"
            return _reply({"clips": [{"description": "ok"}]})

        with patch.object(grok, "GROK_MODEL", "a"), \
                patch.object(grok, "GROK_FALLBACK_MODEL", "b"), \
                patch.object(grok, "_chat", side_effect=fake_chat):
            out = generate_video_story("m", 1, 5)
        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(out[0]["description"], "ok")


class TestVideoStoryEndpoint(unittest.TestCase):
    def setUp(self):
        app.testing = True
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True

    def _post(self, body):
        return self.client.post("/api/video-story", json=body)

    def test_missing_prompt_is_400(self):
        r = self._post({"count": 3})
        self.assertEqual(r.status_code, 400)

    def test_clip_seconds_out_of_range_is_400(self):
        for bad in (0, 0.1, 61, "abc"):
            r = self._post({"prompt": "a story", "clip_seconds": bad})
            self.assertEqual(r.status_code, 400, bad)

    def test_job_returns_items(self):
        captured = {}

        def fake_start(fn, kind="task", summary=None):
            captured["fn"] = fn
            captured["summary"] = summary
            return "job-1"

        items = [{"description": "d", "soundscape": "s", "music": "N/A"}]
        with patch.object(app_module, "start_background_job", side_effect=fake_start), \
                patch.object(grok, "generate_video_story", return_value=items) as gen:
            r = self._post({"prompt": "a story", "count": 3, "clip_seconds": 4})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json(), {"job_id": "job-1"})
            result = captured["fn"](lambda msg: None)
        gen.assert_called_once_with("a story", 3, 4.0)
        self.assertEqual(result["items"], items)
        self.assertEqual(result["clip_seconds"], 4.0)
        self.assertTrue(captured["summary"].startswith("Video story: "))

    def test_sequence_replacements_applied(self):
        captured = {}

        def fake_start(fn, kind="task", summary=None):
            captured["fn"] = fn
            return "job-1"

        items = [{"description": "A bird flies; the bird lands",
                  "soundscape": "Bird song", "music": "N/A"}]
        with patch.object(app_module, "start_background_job", side_effect=fake_start), \
                patch.object(grok, "generate_video_story", return_value=items):
            r = self._post({"prompt": "a story", "count": 1, "clip_seconds": 4,
                            "replacements": [["bird", "dog"]]})
            self.assertEqual(r.status_code, 200)
            result = captured["fn"](lambda msg: None)
        self.assertEqual(result["items"][0]["description"], "A dog flies; the dog lands")
        self.assertEqual(result["items"][0]["soundscape"], "Dog song")
        self.assertEqual(result["items"][0]["music"], "N/A")


if __name__ == "__main__":
    unittest.main()
