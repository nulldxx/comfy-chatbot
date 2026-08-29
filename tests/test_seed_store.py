"""Tests for seed_store.py — the per-image seed index behind "Copy seed".

The store deliberately holds no in-memory cache (IMAGES_DIR is the lazily-mounted
encrypted output volume), so every test here exercises the real read-modify-write
against a temp file.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import seed_store


class _StoreFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.images_dir = Path(self.tmp)
        self.seeds_file = self.images_dir / ".seeds.json"
        self._patchers = [
            patch.object(seed_store, "IMAGES_DIR", self.images_dir),
            patch.object(seed_store, "SEEDS_FILE", self.seeds_file),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _touch(self, name):
        (self.images_dir / name).write_bytes(b"x")


class TestRecordAndGet(_StoreFixture):
    def test_roundtrip(self):
        seed_store.record_seeds(["a.png"], 12345)
        self.assertEqual(seed_store.get_seed("a.png"), "12345")

    def test_seed_stored_as_string(self):
        # A 64-bit seed must survive as a string — a JS Number would round it.
        big = 2**64 - 1
        seed_store.record_seeds(["a.png"], big)
        self.assertEqual(seed_store.get_seed("a.png"), str(big))
        self.assertIsInstance(json.loads(self.seeds_file.read_text())["a.png"], str)

    def test_all_files_of_one_job_share_the_seed(self):
        seed_store.record_seeds(["a.png", "b.png"], 7)
        self.assertEqual(seed_store.get_seed("a.png"), "7")
        self.assertEqual(seed_store.get_seed("b.png"), "7")

    def test_unknown_file_is_none(self):
        self.assertIsNone(seed_store.get_seed("nope.png"))

    def test_no_file_yet_is_none(self):
        self.assertFalse(self.seeds_file.exists())
        self.assertIsNone(seed_store.get_seed("a.png"))

    def test_none_seed_records_nothing(self):
        # A workflow with no seed/noise_seed input yields no seed at all.
        seed_store.record_seeds(["a.png"], None)
        self.assertFalse(self.seeds_file.exists())

    def test_empty_filenames_records_nothing(self):
        seed_store.record_seeds([], 5)
        self.assertFalse(self.seeds_file.exists())

    def test_corrupt_file_reads_as_empty(self):
        self.seeds_file.write_text("{not json")
        self.assertIsNone(seed_store.get_seed("a.png"))
        # ...and is recoverable by the next write rather than wedged forever.
        seed_store.record_seeds(["a.png"], 1)
        self.assertEqual(seed_store.get_seed("a.png"), "1")

    def test_non_dict_file_reads_as_empty(self):
        self.seeds_file.write_text("[1, 2, 3]")
        self.assertIsNone(seed_store.get_seed("a.png"))

    def test_rerecord_overwrites(self):
        seed_store.record_seeds(["a.png"], 1)
        seed_store.record_seeds(["a.png"], 2)
        self.assertEqual(seed_store.get_seed("a.png"), "2")

    def test_write_is_atomic(self):
        # atomic_write_json leaves no .tmp behind, so a reader never sees a
        # half-written map.
        seed_store.record_seeds(["a.png"], 1)
        self.assertEqual([p.name for p in self.images_dir.glob("*.tmp")], [])


class TestForgetAndClear(_StoreFixture):
    def test_forget_drops_one(self):
        seed_store.record_seeds(["a.png", "b.png"], 1)
        seed_store.forget("a.png")
        self.assertIsNone(seed_store.get_seed("a.png"))
        self.assertEqual(seed_store.get_seed("b.png"), "1")

    def test_forget_unknown_is_a_noop(self):
        seed_store.record_seeds(["a.png"], 1)
        seed_store.forget("nope.png")
        self.assertEqual(seed_store.get_seed("a.png"), "1")

    def test_clear_drops_everything(self):
        seed_store.record_seeds(["a.png", "b.png"], 1)
        seed_store.clear()
        self.assertIsNone(seed_store.get_seed("a.png"))
        self.assertIsNone(seed_store.get_seed("b.png"))


class TestPrune(_StoreFixture):
    def test_under_the_cap_keeps_orphans(self):
        # Pruning is a cap-relief measure, not a garbage collector: below the cap a
        # missing image costs nothing and its entry is left alone.
        seed_store.record_seeds(["gone.png"], 1)
        seed_store.record_seeds(["also-gone.png"], 2)
        self.assertEqual(seed_store.get_seed("gone.png"), "1")

    def test_over_the_cap_drops_missing_images_first(self):
        with patch.object(seed_store, "SEED_STORE_MAX", 3):
            for i in range(3):
                seed_store.record_seeds([f"gone{i}.png"], i)
            self._touch("kept.png")
            seed_store.record_seeds(["kept.png"], 99)
            store = json.loads(self.seeds_file.read_text())
        # The four entries exceed the cap of 3, so the three whose files are absent
        # go and only the one still on disk survives.
        self.assertEqual(store, {"kept.png": "99"})

    def test_over_the_cap_drops_oldest_when_all_files_exist(self):
        with patch.object(seed_store, "SEED_STORE_MAX", 2):
            for name in ("a.png", "b.png", "c.png"):
                self._touch(name)
                seed_store.record_seeds([name], name)
            store = json.loads(self.seeds_file.read_text())
        self.assertEqual(list(store), ["b.png", "c.png"])

    def test_rerecord_refreshes_position(self):
        # Re-inserting at the end keeps the oldest-first drop honest: a file whose
        # seed was just rewritten is the newest entry, not the oldest.
        with patch.object(seed_store, "SEED_STORE_MAX", 2):
            for name in ("a.png", "b.png"):
                self._touch(name)
                seed_store.record_seeds([name], name)
            seed_store.record_seeds(["a.png"], "a2")
            self._touch("c.png")
            seed_store.record_seeds(["c.png"], "c")
            store = json.loads(self.seeds_file.read_text())
        self.assertEqual(list(store), ["a.png", "c.png"])


if __name__ == "__main__":
    unittest.main()
