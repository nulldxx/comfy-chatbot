"""Tests for grok.py — focused on the /video-sequence JSON parsing and the
structured generate_video_prompt_sequence() (with the HTTP _chat call mocked)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grok
from grok import GrokError, _parse_video_prompts, generate_video_prompt_sequence


class ParseVideoPromptsTests(unittest.TestCase):
    def test_full_objects(self):
        content = (
            '{"prompts": ['
            '{"prompt": "a cat", "action": "it leaps", "audio": "a meow"},'
            '{"prompt": "a dog", "action": "it runs", "audio": "a bark"}'
            ']}'
        )
        out = _parse_video_prompts(content)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], {"prompt": "a cat", "action": "it leaps", "audio": "a meow"})
        self.assertEqual(out[1]["prompt"], "a dog")

    def test_missing_action_and_audio_default_to_empty(self):
        out = _parse_video_prompts('{"prompts": [{"prompt": "a lone cat"}]}')
        self.assertEqual(out, [{"prompt": "a lone cat", "action": "", "audio": ""}])

    def test_non_string_action_audio_default_to_empty(self):
        out = _parse_video_prompts('{"prompts": [{"prompt": "x", "action": 5, "audio": null}]}')
        self.assertEqual(out[0]["action"], "")
        self.assertEqual(out[0]["audio"], "")

    def test_strips_whitespace(self):
        out = _parse_video_prompts('{"prompts": [{"prompt": "  a cat  ", "action": " leaps ", "audio": " meow "}]}')
        self.assertEqual(out[0], {"prompt": "a cat", "action": "leaps", "audio": "meow"})

    def test_items_without_prompt_are_skipped(self):
        content = '{"prompts": [{"action": "x"}, {"prompt": "", "action": "y"}, {"prompt": "ok"}]}'
        out = _parse_video_prompts(content)
        self.assertEqual(out, [{"prompt": "ok", "action": "", "audio": ""}])

    def test_wrapped_in_stray_text(self):
        content = 'Sure! {"prompts": [{"prompt": "a cat"}]} hope that helps'
        out = _parse_video_prompts(content)
        self.assertEqual(out[0]["prompt"], "a cat")

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
        reply = '{"prompts": [{"prompt": "a cat", "action": "leaps", "audio": "meow"}]}'
        with patch.object(grok, "_chat", return_value=reply) as mock_chat:
            out = generate_video_prompt_sequence("a cat", 1)
        self.assertEqual(out, [{"prompt": "a cat", "action": "leaps", "audio": "meow"}])
        self.assertTrue(mock_chat.called)

    def test_falls_back_to_second_model_on_corrupt_first(self):
        good = '{"prompts": [{"prompt": "a cat"}]}'
        with patch.object(grok, "GROK_MODEL", "primary"), \
             patch.object(grok, "GROK_FALLBACK_MODEL", "fallback"), \
             patch.object(grok, "_chat", side_effect=[GrokError("boom"), good]) as mock_chat:
            out = generate_video_prompt_sequence("a cat", 1)
        self.assertEqual(out[0]["prompt"], "a cat")
        self.assertEqual(mock_chat.call_count, 2)


if __name__ == "__main__":
    unittest.main()
