"""/sequence-replacement: case-preserving replace followed by an angle-tag dedupe."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from generation_service import apply_sequence_replacements, dedupe_angle_tags  # noqa: E402


class DedupeAngleTagsTest(unittest.TestCase):
    def test_keeps_first_drops_later(self):
        self.assertEqual(
            dedupe_angle_tags("a woman <lora:x:1> walks, the woman <lora:x:1> smiles"),
            "a woman <lora:x:1> walks, the woman smiles",
        )

    def test_comma_separated_duplicate_leaves_no_double_comma(self):
        self.assertEqual(dedupe_angle_tags("<lora:x:1>, <lora:x:1>, bar"), "<lora:x:1>, bar")

    def test_case_insensitive(self):
        self.assertEqual(dedupe_angle_tags("<lora:X:1> and <LORA:x:1>"), "<lora:X:1> and")

    def test_different_strength_is_a_different_tag(self):
        text = "<lora:x:1> and <lora:x:0.8>"
        self.assertEqual(dedupe_angle_tags(text), text)

    def test_distinct_tags_untouched(self):
        text = "<lora:a:1> foo <lora:b:1>"
        self.assertEqual(dedupe_angle_tags(text), text)

    def test_no_tags_or_empty(self):
        self.assertEqual(dedupe_angle_tags("plain"), "plain")
        self.assertEqual(dedupe_angle_tags(""), "")
        self.assertIsNone(dedupe_angle_tags(None))


class ApplySequenceReplacementsTest(unittest.TestCase):
    def test_replacement_tag_added_once(self):
        out = apply_sequence_replacements(
            "A woman waves. The woman laughs.", [("woman", "woman <lora:jane:0.9>")]
        )
        self.assertEqual(out, "A woman <lora:jane:0.9> waves. The woman laughs.")

    def test_tag_already_in_prompt_kept_and_replacement_copy_dropped(self):
        out = apply_sequence_replacements(
            "<lora:jane:0.9> a woman", [("woman", "woman <lora:jane:0.9>")]
        )
        self.assertEqual(out, "<lora:jane:0.9> a woman")

    def test_case_preserved(self):
        self.assertEqual(apply_sequence_replacements("Bird bird", [("bird", "dog")]), "Dog dog")

    def test_no_replacements_is_untouched(self):
        text = "<lora:x:1> <lora:x:1>"
        self.assertEqual(apply_sequence_replacements(text, []), text)


if __name__ == "__main__":
    unittest.main()
