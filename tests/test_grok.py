"""Tests for grok.py — focused on the /video-sequence JSON parsing and the
structured generate_video_prompt_sequence() (with the HTTP _chat call mocked)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grok
from grok import GrokError, _parse_video_prompts, generate_video_prompt_sequence


def _shot(prompt, description="", soundscape="", music="N/A"):
    return {"prompt": prompt, "description": description, "soundscape": soundscape, "music": music}


class ParseVideoPromptsTests(unittest.TestCase):
    def test_full_objects(self):
        content = (
            '{"music": "Soft piano.", "prompts": ['
            '{"prompt": "a cat", "description": "it leaps", "soundscape": "a thud"},'
            '{"prompt": "a dog", "description": "it runs", "soundscape": "panting"}'
            ']}'
        )
        out = _parse_video_prompts(content)
        self.assertEqual(out, [
            _shot("a cat", "it leaps", "a thud", "Soft piano."),
            _shot("a dog", "it runs", "panting", "Soft piano."),
        ])

    def test_shared_music_is_copied_onto_every_shot(self):
        content = '{"music": "Low strings.", "prompts": [{"prompt": "a"}, {"prompt": "b"}]}'
        self.assertEqual([s["music"] for s in _parse_video_prompts(content)], ["Low strings."] * 2)

    def test_per_shot_music_overrides_shared(self):
        content = '{"music": "Low strings.", "prompts": [{"prompt": "a", "music": "Drums."}, {"prompt": "b"}]}'
        self.assertEqual([s["music"] for s in _parse_video_prompts(content)], ["Drums.", "Low strings."])

    def test_missing_or_blank_music_is_na(self):
        self.assertEqual(_parse_video_prompts('{"prompts": [{"prompt": "a"}]}')[0]["music"], "N/A")
        self.assertEqual(_parse_video_prompts('{"music": "  ", "prompts": [{"prompt": "a"}]}')[0]["music"], "N/A")

    def test_missing_description_and_soundscape_default_to_empty(self):
        self.assertEqual(_parse_video_prompts('{"prompts": [{"prompt": "a lone cat"}]}'), [_shot("a lone cat")])

    def test_non_string_fields_default(self):
        out = _parse_video_prompts('{"music": 3, "prompts": [{"prompt": "x", "description": 5, "soundscape": null}]}')
        self.assertEqual(out, [_shot("x")])

    def test_strips_whitespace(self):
        content = '{"music": " piano ", "prompts": [{"prompt": "  a cat  ", "description": " leaps ", "soundscape": " thud "}]}'
        self.assertEqual(_parse_video_prompts(content), [_shot("a cat", "leaps", "thud", "piano")])

    def test_items_without_prompt_are_skipped(self):
        content = '{"prompts": ["a string", {"description": "x"}, {"prompt": "", "description": "y"}, {"prompt": "ok"}]}'
        self.assertEqual(_parse_video_prompts(content), [_shot("ok")])

    def test_wrapped_in_stray_text(self):
        content = 'Sure! {"prompts": [{"prompt": "a cat"}]} hope that helps'
        self.assertEqual(_parse_video_prompts(content)[0]["prompt"], "a cat")

    def test_empty_list_raises(self):
        with self.assertRaises(GrokError):
            _parse_video_prompts('{"prompts": []}')

    def test_no_json_raises(self):
        with self.assertRaises(GrokError):
            _parse_video_prompts('I cannot help with that.')

    def test_invalid_json_raises(self):
        with self.assertRaises(GrokError):
            _parse_video_prompts('{"prompts": [bogus]}')

    def test_leaked_special_token_raises(self):
        with self.assertRaises(GrokError):
            _parse_video_prompts('{"prompts": [{"prompt": "a cat<|eos|>"}]}')


class GenerateVideoPromptSequenceTests(unittest.TestCase):
    def test_returns_parsed_objects(self):
        reply = '{"music": "N/A", "prompts": [{"prompt": "a cat", "description": "leaps", "soundscape": "thud"}]}'
        with patch.object(grok, "_chat", return_value=reply) as mock_chat:
            out = generate_video_prompt_sequence("a cat", 1)
        self.assertEqual(out, [_shot("a cat", "leaps", "thud")])
        self.assertTrue(mock_chat.called)

    def test_request_asks_for_the_h3_fields(self):
        reply = '{"prompts": [{"prompt": "a cat"}]}'
        with patch.object(grok, "_chat", return_value=reply) as mock_chat:
            generate_video_prompt_sequence("a busker on a bridge", 3)
        user = mock_chat.call_args[0][0][1]["content"]
        self.assertIn("a busker on a bridge", user)
        self.assertIn("exactly 3", user)
        for needle in ('"description"', '"soundscape"', '"music"', "<d>[English]", "(S1)",
                       "with small amplitude", "off-screen voiceover"):
            self.assertIn(needle, user)
        self.assertNotIn('"action"', user)

    def test_falls_back_to_second_model_on_corrupt_first(self):
        good = '{"prompts": [{"prompt": "a cat"}]}'
        with patch.object(grok, "GROK_MODEL", "primary"), \
             patch.object(grok, "GROK_FALLBACK_MODEL", "fallback"), \
             patch.object(grok, "_chat", side_effect=[GrokError("boom"), good]) as mock_chat:
            out = generate_video_prompt_sequence("a cat", 1)
        self.assertEqual(out[0]["prompt"], "a cat")
        self.assertEqual(mock_chat.call_count, 2)

    def test_set_cancel_event_stops_before_calling_grok(self):
        import threading
        cancel = threading.Event()
        cancel.set()
        with patch.object(grok, "_chat") as mock_chat:
            with self.assertRaises(GrokError):
                generate_video_prompt_sequence("a cat", 1, cancel_event=cancel)
        mock_chat.assert_not_called()

    def test_cancel_event_stops_fallback_attempt(self):
        # First model fails; a cancel between attempts must prevent the fallback
        # call rather than firing a second request.
        import threading
        cancel = threading.Event()

        def boom(*a, **k):
            cancel.set()  # cancelled while the first attempt is in flight
            raise GrokError("boom")

        with patch.object(grok, "GROK_MODEL", "primary"), \
             patch.object(grok, "GROK_FALLBACK_MODEL", "fallback"), \
             patch.object(grok, "_chat", side_effect=boom) as mock_chat:
            with self.assertRaises(GrokError):
                generate_video_prompt_sequence("a cat", 1, cancel_event=cancel)
        self.assertEqual(mock_chat.call_count, 1)


if __name__ == "__main__":
    unittest.main()
