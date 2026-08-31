"""Tests for profanity.py — the optional PROFANITY_FILTER word list guarding the
master prompt of /sequence and /video-sequence."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import profanity


class _Configured(unittest.TestCase):
    """Restores whatever word list the environment configured at import."""

    WORDS = ""

    def setUp(self):
        self._saved = ",".join(profanity.WORDS)
        profanity.configure(self.WORDS)

    def tearDown(self):
        profanity.configure(self._saved)


class TestParseWords(unittest.TestCase):
    def test_empty_and_none(self):
        self.assertEqual(profanity.parse_words(""), [])
        self.assertEqual(profanity.parse_words(None), [])

    def test_splits_strips_and_lowercases(self):
        self.assertEqual(profanity.parse_words(" Foo , BAR ,baz"), ["foo", "bar", "baz"])

    def test_drops_empty_entries(self):
        self.assertEqual(profanity.parse_words("foo,,  ,bar,"), ["foo", "bar"])

    def test_dedupes_case_insensitively(self):
        self.assertEqual(profanity.parse_words("foo,FOO,foo"), ["foo"])


class TestDisabled(_Configured):
    WORDS = ""

    def test_not_enabled(self):
        self.assertFalse(profanity.enabled())

    def test_nothing_is_blocked(self):
        self.assertEqual(profanity.find_profanity("any words at all"), [])
        self.assertIsNone(profanity.check("any words at all"))


class TestMatching(_Configured):
    WORDS = "damn, blast, bad phrase"

    def test_enabled(self):
        self.assertTrue(profanity.enabled())

    def test_clean_prompt_passes(self):
        self.assertEqual(profanity.find_profanity("a woman practising yoga"), [])
        self.assertIsNone(profanity.check("a woman practising yoga"))

    def test_exact_word_matches(self):
        self.assertEqual(profanity.find_profanity("oh damn"), ["damn"])

    def test_case_insensitive(self):
        self.assertEqual(profanity.find_profanity("Oh DAMN"), ["DAMN"])

    def test_punctuation_is_a_boundary(self):
        self.assertEqual(profanity.find_profanity("(damn!)"), ["damn"])

    def test_suffix_does_not_match_without_wildcard(self):
        self.assertEqual(profanity.find_profanity("damned and damning"), [])

    def test_prefix_does_not_match(self):
        # The Scunthorpe case: an entry must start at a word boundary.
        self.assertEqual(profanity.find_profanity("goddamn"), [])

    def test_phrase_entry(self):
        self.assertEqual(profanity.find_profanity("this is a bad phrase here"), ["bad phrase"])

    def test_reports_each_hit_once_in_order(self):
        self.assertEqual(
            profanity.find_profanity("blast, damn, blast again"), ["blast", "damn"]
        )

    def test_empty_text(self):
        self.assertEqual(profanity.find_profanity(""), [])
        self.assertEqual(profanity.find_profanity(None), [])


class TestWildcard(_Configured):
    WORDS = "fuck*"

    def test_bare_word_matches(self):
        self.assertEqual(profanity.find_profanity("fuck"), ["fuck"])

    def test_suffix_matches_and_is_reported_as_typed(self):
        self.assertEqual(profanity.find_profanity("a fucking mess"), ["fucking"])

    def test_still_anchored_at_the_start(self):
        self.assertEqual(profanity.find_profanity("clusterfuck"), [])

    def test_lone_star_entry_matches_nothing(self):
        profanity.configure("*")
        self.assertFalse(profanity.enabled())
        self.assertEqual(profanity.find_profanity("anything"), [])


class TestCheckMessage(_Configured):
    WORDS = "damn,blast"

    def test_names_the_offending_words(self):
        msg = profanity.check("damn and blast")
        self.assertIn('"damn"', msg)
        self.assertIn('"blast"', msg)
        self.assertIn("profanity filter", msg.lower())


if __name__ == "__main__":
    unittest.main()
