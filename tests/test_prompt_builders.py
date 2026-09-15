"""Tests for prompt_builders — the server-side ports of utils.js's prompt builders.

The cases mirror tests/js/utils.test.js (deriveFaceDetailPrompt, buildVideoPrompt), so
a sequence run's auto face-detail / image2video prompts match what the client builds."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompt_builders import (
    I2VA_INSTRUCTION, apply_replacements, build_video_prompt, derive_face_detail_prompt,
    normalize_video_meta,
)


class ApplyReplacementsTests(unittest.TestCase):
    def test_falsy_prompt_passes_through(self):
        self.assertIsNone(apply_replacements(None, [("a", "b")]))
        self.assertEqual(apply_replacements("", [("a", "b")]), "")

    def test_replaces_every_occurrence_case_sensitively(self):
        self.assertEqual(apply_replacements("cat Cat cat", [("cat", "dog")]), "dog Cat dog")

    def test_pairs_apply_in_order(self):
        self.assertEqual(apply_replacements("cat", [("cat", "dog"), ("dog", "fox")]), "fox")

    def test_empty_from_is_ignored(self):
        self.assertEqual(apply_replacements("cat", [("", "x")]), "cat")


class DeriveFaceDetailPromptTests(unittest.TestCase):
    def test_none_and_empty(self):
        self.assertIsNone(derive_face_detail_prompt(None))
        self.assertIsNone(derive_face_detail_prompt(""))

    def test_no_lora_tag(self):
        self.assertIsNone(derive_face_detail_prompt("a woman in a red dress"))

    def test_no_subject_uses_a_face(self):
        self.assertEqual(derive_face_detail_prompt("landscape <lora:nature:1.0>"),
                         "a face <lora:nature:1.0>")

    def test_woman_subject(self):
        self.assertEqual(derive_face_detail_prompt("a woman in a park <lora:name:1.0>"),
                         "a woman's face <lora:name:1.0>")

    def test_man_not_matched_inside_woman(self):
        self.assertIn("a man's face", derive_face_detail_prompt("an old man <lora:x:0.8>"))
        self.assertNotIn("a man's face", derive_face_detail_prompt("a beautiful woman <lora:x:1.0>"))

    def test_expressions_deduped_lowercased_in_order(self):
        self.assertEqual(
            derive_face_detail_prompt("A Woman SMILING, then laughing and smiling <lora:a:1> <LORA:b:0.5>"),
            "a woman's face, smiling, laughing <lora:a:1> <LORA:b:0.5>",
        )

    def test_multi_word_expression_wins(self):
        self.assertIn("open mouth", derive_face_detail_prompt("a girl, open mouth <lora:x:1>"))


class BuildVideoPromptTests(unittest.TestCase):
    META = {"description": "Live-action, cinematic, a cat leaps.",
            "soundscape": "Paws thud on stone.", "music": "Soft piano."}

    def test_instruction_matches_the_guide(self):
        self.assertEqual(
            I2VA_INSTRUCTION,
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced.",
        )

    def test_i2va_layout(self):
        self.assertEqual(
            build_video_prompt("a cat on a wall", self.META),
            f"{I2VA_INSTRUCTION}\n\n"
            "integrated_multimodal_description: [Shot 1] Live-action, cinematic, a cat leaps.\n\n"
            "overall_soundscape: Paws thud on stone.\n\n"
            "non_diegetic_music: Soft piano.",
        )

    def test_audio_off_sends_both_sound_fields_as_na(self):
        parts = build_video_prompt("a cat", self.META, audio=False).split("\n\n")
        self.assertEqual(parts[2:], ["overall_soundscape: N/A", "non_diegetic_music: N/A"])

    def test_empty_soundscape_omitted_and_empty_music_na(self):
        parts = build_video_prompt("a cat", {"description": "leaps", "soundscape": "", "music": ""}).split("\n\n")
        self.assertEqual(parts, [I2VA_INSTRUCTION,
                                 "integrated_multimodal_description: [Shot 1] leaps",
                                 "non_diegetic_music: N/A"])

    def test_still_prompt_is_the_description_without_meta(self):
        self.assertEqual(
            build_video_prompt("  a cat on a wall ", None),
            f"{I2VA_INSTRUCTION}\n\nintegrated_multimodal_description: [Shot 1] a cat on a wall"
            "\n\nnon_diegetic_music: N/A",
        )

    def test_legacy_action_audio_meta(self):
        self.assertEqual(
            build_video_prompt("a cat", {"action": "it leaps down", "audio": "a meow"}),
            f"{I2VA_INSTRUCTION}\n\n"
            "integrated_multimodal_description: [Shot 1] a cat. it leaps down\n\n"
            "overall_soundscape: a meow\n\n"
            "non_diegetic_music: N/A",
        )

    def test_normalize_trims_and_defaults(self):
        self.assertEqual(normalize_video_meta(None),
                         {"description": "", "soundscape": "", "music": ""})
        self.assertEqual(normalize_video_meta({"music": " x "}),
                         {"description": "", "soundscape": "", "music": "x"})


if __name__ == "__main__":
    unittest.main()
