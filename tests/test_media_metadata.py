"""Tests for media_metadata.py — reading ComfyUI's embedded generation metadata.

Fixtures are assembled byte-by-byte (there is no Pillow in requirements.txt), which
also pins the exact container layouts the readers walk.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import media_metadata as mm


def _png_chunk(ctype, data):
    return (struct.pack(">I", len(data)) + ctype + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))


def make_png(text_chunks):
    """A 1×1 PNG carrying the given (ctype, payload) text chunks."""
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00")
    body = _png_chunk(b"IHDR", ihdr)
    for ctype, payload in text_chunks:
        body += _png_chunk(ctype, payload)
    body += _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
    return mm.PNG_SIGNATURE + body


def tiff_ascii(entries, order="<"):
    """Minimal TIFF: IFD0 with ASCII entries [(tag, text)], values after the IFD."""
    bom = b"II" if order == "<" else b"MM"
    n = len(entries)
    ifd_off = 8
    data_off = ifd_off + 2 + n * 12 + 4
    ifd = struct.pack(order + "H", n)
    blob = b""
    for tag, text in entries:
        raw = text.encode() + b"\x00"
        if len(raw) <= 4:
            ifd += struct.pack(order + "HHI", tag, 2, len(raw)) + raw.ljust(4, b"\x00")
        else:
            ifd += struct.pack(order + "HHII", tag, 2, len(raw), data_off + len(blob))
            blob += raw
    ifd += struct.pack(order + "I", 0)
    return bom + struct.pack(order + "HI", 42, ifd_off) + ifd + blob


def make_webp(exif):
    vp8 = b"\x00" * 9  # odd size: exercises the even-length padding
    chunks = (b"VP8 " + struct.pack("<I", len(vp8)) + vp8 + b"\x00"
              + b"EXIF" + struct.pack("<I", len(exif)) + exif
              + (b"\x00" if len(exif) & 1 else b""))
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WEBP" + chunks


API_GRAPH = {
    "3": {"class_type": "KSampler", "_meta": {"title": "KSampler"},
          "inputs": {"seed": 2**64 - 1, "steps": 20, "cfg": 7.5,
                     "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                     "model": ["10", 0], "positive": ["6", 0], "negative": ["7", 0]}},
    "4": {"class_type": "CheckpointLoaderSimple",
          "inputs": {"ckpt_name": "sdxl.safetensors"}},
    "5": {"class_type": "EmptyLatentImage",
          "inputs": {"width": 1024, "height": 768, "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "_meta": {"title": "Positive"},
          "inputs": {"text": "a cat on a mat", "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode", "_meta": {"title": "Negative"},
          "inputs": {"text": "blurry", "clip": ["4", 1]}},
    "10": {"class_type": "LoraLoader",
           "inputs": {"lora_name": "style.safetensors", "strength_model": 0.8,
                      "model": ["4", 0], "clip": ["4", 1]}},
}
UI_GRAPH = {"nodes": [{"id": 3, "type": "KSampler"}], "links": []}


class _TmpDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, data):
        p = self.dir / name
        p.write_bytes(data)
        return p


class TestPng(_TmpDir):
    def test_text_ztxt_and_itxt(self):
        p = self.write("a.png", make_png([
            (b"tEXt", b"prompt\x00" + json.dumps(API_GRAPH).encode()),
            (b"zTXt", b"workflow\x00\x00" + zlib.compress(json.dumps(UI_GRAPH).encode())),
            (b"iTXt", b"parameters\x00\x00\x00en\x00\x00" + "café ☕".encode()),
            (b"iTXt", b"packed\x00\x01\x00\x00\x00" + zlib.compress("zü".encode())),
        ]))
        raw = mm.read_raw_metadata(p)
        self.assertEqual(json.loads(raw["prompt"]), API_GRAPH)
        self.assertEqual(json.loads(raw["workflow"]), UI_GRAPH)
        self.assertEqual(raw["parameters"], "café ☕")
        self.assertEqual(raw["packed"], "zü")

    def test_no_text_chunks(self):
        self.assertEqual(mm.read_raw_metadata(self.write("a.png", make_png([]))), {})

    def test_not_a_png(self):
        self.assertEqual(mm.read_raw_metadata(self.write("a.png", b"GIF89a....")), {})

    def test_truncated_file_keeps_what_it_read(self):
        data = make_png([(b"tEXt", b"prompt\x00{}"),
                         (b"tEXt", b"workflow\x00" + b"x" * 100)])
        cut = data.index(b"workflow") + 20
        raw = mm.read_raw_metadata(self.write("a.png", data[:cut]))
        self.assertEqual(raw, {"prompt": "{}"})

    def test_corrupt_ztxt_is_skipped(self):
        p = self.write("a.png", make_png([(b"zTXt", b"bad\x00\x00not-zlib"),
                                          (b"tEXt", b"ok\x00yes")]))
        self.assertEqual(mm.read_raw_metadata(p), {"ok": "yes"})


class TestWebp(_TmpDir):
    def _check(self, order):
        exif = tiff_ascii([(0x0110, "Prompt:" + json.dumps(API_GRAPH)),
                           (0x010F, "Workflow:" + json.dumps(UI_GRAPH))], order)
        raw = mm.read_raw_metadata(self.write("a.webp", make_webp(exif)))
        self.assertEqual(json.loads(raw["prompt"]), API_GRAPH)
        self.assertEqual(json.loads(raw["workflow"]), UI_GRAPH)

    def test_little_endian(self):
        self._check("<")

    def test_big_endian(self):
        self._check(">")

    def test_exif_header_prefix(self):
        exif = b"Exif\x00\x00" + tiff_ascii([(0x0110, "Prompt:{}")])
        raw = mm.read_raw_metadata(self.write("a.webp", make_webp(exif)))
        self.assertEqual(raw, {"prompt": "{}"})

    def test_untagged_ascii_kept_by_tag(self):
        exif = tiff_ascii([(0x010E, "just a description")])
        raw = mm.read_raw_metadata(self.write("a.webp", make_webp(exif)))
        self.assertEqual(raw, {"exif_0x010e": "just a description"})

    def test_no_exif(self):
        data = b"RIFF" + struct.pack("<I", 4) + b"WEBP"
        self.assertEqual(mm.read_raw_metadata(self.write("a.webp", data)), {})

    def test_garbage(self):
        self.assertEqual(mm.read_raw_metadata(self.write("a.webp", b"\x00" * 7)), {})


def _ffprobe(tags, returncode=0):
    out = json.dumps({"format": {"tags": tags}}).encode()
    return subprocess.CompletedProcess([], returncode, stdout=out, stderr=b"")


class TestVideo(_TmpDir):
    def setUp(self):
        super().setUp()
        self.path = self.write("a.mp4", b"\x00")

    def test_core_savevideo_tags(self):
        with patch.object(mm.subprocess, "run", return_value=_ffprobe({
            "prompt": json.dumps(API_GRAPH), "workflow": json.dumps(UI_GRAPH),
            "major_brand": "isom", "encoder": "Lavf61"})):
            raw = mm.read_raw_metadata(self.path)
        self.assertEqual(set(raw), {"prompt", "workflow"})

    def test_vhs_comment_with_double_encoded_values(self):
        comment = json.dumps({"prompt": json.dumps(API_GRAPH), "workflow": UI_GRAPH})
        with patch.object(mm.subprocess, "run", return_value=_ffprobe({"COMMENT": comment})):
            parsed = mm.parse_metadata(mm.read_raw_metadata(self.path))
        self.assertEqual(parsed["prompt"], API_GRAPH)
        self.assertEqual(parsed["workflow"], UI_GRAPH)
        self.assertEqual(parsed["other"], {})

    def test_plain_comment_kept(self):
        with patch.object(mm.subprocess, "run", return_value=_ffprobe({"comment": "hi"})):
            self.assertEqual(mm.read_raw_metadata(self.path), {"comment": "hi"})

    def test_missing_ffprobe(self):
        with patch.object(mm.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(mm.read_raw_metadata(self.path), {})

    def test_ffprobe_failure(self):
        with patch.object(mm.subprocess, "run", return_value=_ffprobe({}, returncode=1)):
            self.assertEqual(mm.read_raw_metadata(self.path), {})

    def test_jpg_is_not_read(self):
        self.assertEqual(mm.read_raw_metadata(self.write("a.jpg", b"\xff\xd8")), {})

    def test_missing_file(self):
        self.assertEqual(mm.read_raw_metadata(self.dir / "nope.png"), {})


class TestSummary(unittest.TestCase):
    def setUp(self):
        self.rows = mm.summarise_prompt_graph(API_GRAPH)

    def _get(self, label):
        return [r["value"] for r in self.rows if r["label"] == label]

    def test_prompts_first_with_titles(self):
        prompts = [r for r in self.rows if r["section"] == "prompt"]
        self.assertEqual([(r["title"], r["value"]) for r in prompts],
                         [("Positive", "a cat on a mat"), ("Negative", "blurry")])
        self.assertEqual(self.rows[:2], prompts)

    def test_linked_inputs_skipped(self):
        # KSampler.positive is a [node, slot] link, not a prompt.
        self.assertEqual(self._get("Positive"), [])

    def test_seed_is_a_string(self):
        self.assertEqual(self._get("Seed"), [str(2**64 - 1)])

    def test_sampling_models_size(self):
        self.assertEqual(self._get("Steps"), ["20"])
        self.assertEqual(self._get("CFG"), ["7.5"])
        self.assertEqual(self._get("Sampler"), ["euler"])
        self.assertEqual(self._get("Checkpoint"), ["sdxl.safetensors"])
        self.assertEqual(self._get("LoRA"), ["style.safetensors"])
        self.assertEqual(self._get("LoRA strength"), ["0.8"])
        self.assertEqual(self._get("Width"), ["1024"])
        sections = [r["section"] for r in self.rows]
        self.assertEqual(sections, sorted(sections, key=mm.SECTION_ORDER.index))

    def test_node_ids_sort_numerically(self):
        graph = {"10": {"class_type": "A", "inputs": {"steps": 1}},
                 "9": {"class_type": "B", "inputs": {"steps": 2}},
                 "320:5": {"class_type": "C", "inputs": {"steps": 3}}}
        self.assertEqual([r["node"] for r in mm.summarise_prompt_graph(graph)],
                         ["9", "10", "320:5"])

    def test_non_graph(self):
        self.assertEqual(mm.summarise_prompt_graph([1, 2]), [])
        self.assertEqual(mm.summarise_prompt_graph({"1": "x", "2": {"inputs": 3}}), [])


class TestParseMetadata(unittest.TestCase):
    def test_full(self):
        out = mm.parse_metadata({"prompt": json.dumps(API_GRAPH),
                                 "workflow": json.dumps(UI_GRAPH),
                                 "parameters": "a1111 text"})
        self.assertEqual(out["prompt"], API_GRAPH)
        self.assertEqual(out["workflow"], UI_GRAPH)
        self.assertEqual(out["other"], {"parameters": "a1111 text"})
        self.assertTrue(out["summary"])

    def test_empty(self):
        self.assertEqual(mm.parse_metadata({}),
                         {"summary": [], "prompt": None, "workflow": None, "other": {}})

    def test_non_json_prompt_is_kept_as_text(self):
        out = mm.parse_metadata({"prompt": "a plain prompt"})
        self.assertIsNone(out["prompt"])
        self.assertEqual(out["other"], {"prompt": "a plain prompt"})


if __name__ == "__main__":
    unittest.main()
