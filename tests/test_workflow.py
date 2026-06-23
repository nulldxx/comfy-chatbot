import json
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workflow import (
    apply_placeholders,
    apply_resolution,
    apply_steps,
    fill_lora_sentinels,
    fill_placeholders_for_validation,
    find_placeholders,
    lora_path_for_os,
    randomize_seeds,
    strip_last_frame_guide,
    strip_lora_nodes,
    LORA_NAME_SENTINEL,
)


class TestApplyPlaceholders(unittest.TestCase):
    def test_replaces_single(self):
        result = apply_placeholders("hello <NAME>", {"NAME": "world"})
        self.assertEqual(result, "hello world")

    def test_replaces_multiple(self):
        result = apply_placeholders("<A> and <B>", {"A": "foo", "B": "bar"})
        self.assertEqual(result, "foo and bar")

    def test_escapes_special_json_chars(self):
        result = apply_placeholders('"<PROMPT>"', {"PROMPT": 'say "hi"'})
        self.assertIn(r'say \"hi\"', result)

    def test_unknown_key_is_left(self):
        result = apply_placeholders("<UNKNOWN>", {"OTHER": "val"})
        self.assertEqual(result, "<UNKNOWN>")

    def test_bare_float_slot(self):
        # The runtime fills <LAST_FRAME_STRENGTH> with a float (1.0 on / 0.0 off) so the
        # unquoted guide-strength slot becomes a bare JSON number, not a quoted string.
        result = apply_placeholders('"value": <LAST_FRAME_STRENGTH>', {"LAST_FRAME_STRENGTH": 0.0})
        self.assertEqual(json.loads("{" + result + "}")["value"], 0.0)

    def test_empty_mapping(self):
        result = apply_placeholders("no change", {})
        self.assertEqual(result, "no change")


class TestFindPlaceholders(unittest.TestCase):
    def test_finds_tokens(self):
        self.assertEqual(find_placeholders("<FOO> text <BAR>"), ["<BAR>", "<FOO>"])

    def test_deduplicates(self):
        self.assertEqual(find_placeholders("<X> and <X>"), ["<X>"])

    def test_empty(self):
        self.assertEqual(find_placeholders("no tokens here"), [])

    def test_ignores_lowercase(self):
        self.assertEqual(find_placeholders("<lower>"), [])

    def test_numbers_and_underscores(self):
        self.assertIn("<LORA_1_NAME>", find_placeholders("<LORA_1_NAME>"))


class TestFillLoraSentinels(unittest.TestCase):
    def test_fills_name(self):
        result = fill_lora_sentinels('"lora_name": <LORA_1_NAME>')
        self.assertIn(LORA_NAME_SENTINEL, result)

    def test_fills_strength(self):
        result = fill_lora_sentinels('"strength": <LORA_1_STRENGTH>')
        self.assertIn("0", result)

    def test_multiple_slots(self):
        text = "<LORA_1_NAME>, <LORA_2_NAME>, <LORA_1_STRENGTH>"
        result = fill_lora_sentinels(text)
        self.assertEqual(result.count(LORA_NAME_SENTINEL), 2)

    def test_no_lora_tokens(self):
        self.assertEqual(fill_lora_sentinels("plain text"), "plain text")


class TestStripLoraNodes(unittest.TestCase):
    def _workflow_with_sentinel_lora(self):
        return {
            "1": {"inputs": {"model": ["0", 0], "clip": ["0", 1], "lora_name": LORA_NAME_SENTINEL, "strength_model": 1}},
            "2": {"inputs": {"model": ["1", 0], "clip": ["1", 1], "text": "hello"}},
        }

    def test_removes_sentinel_node(self):
        wf = self._workflow_with_sentinel_lora()
        result, removed = strip_lora_nodes(wf)
        self.assertNotIn("1", result)
        self.assertIn("1", removed)

    def test_rewires_downstream_model(self):
        wf = self._workflow_with_sentinel_lora()
        result, _ = strip_lora_nodes(wf)
        # Node 2's model input should now point to what node 1's model pointed to ("0", 0)
        self.assertEqual(result["2"]["inputs"]["model"], ["0", 0])

    def test_rewires_downstream_clip(self):
        wf = self._workflow_with_sentinel_lora()
        result, _ = strip_lora_nodes(wf)
        self.assertEqual(result["2"]["inputs"]["clip"], ["0", 1])

    def test_no_sentinel_no_change(self):
        wf = {"1": {"inputs": {"lora_name": "real_lora.safetensors"}}}
        result, removed = strip_lora_nodes(wf)
        self.assertIn("1", result)
        self.assertEqual(removed, [])


class TestStripLastFrameGuide(unittest.TestCase):
    def _ltx_workflow(self):
        # Minimal representation of the LTX 2.3 last-frame subgraph.
        # Nodes:
        #   "load_lf"     LoadImage (last frame)
        #   "resize_lf"   Resize (feeds preprocess)
        #   "preproc_lf"  LTXVPreprocess (feeds guide)
        #   "strength"    PrimitiveFloat (feeds guide)
        #   "guide"       LTXVAddGuide (the node to strip)
        #   "cond"        LTXVConditioning (positive/negative source)
        #   "latent_src"  LTXVImgToVideoInplace (latent source)
        #   "concat"      LTXVConcatAVLatent (downstream of guide latent output)
        #   "cfg"         CFGGuider (downstream of guide positive/negative)
        #   "crop"        LTXVCropGuides (downstream of guide positive/negative)
        return {
            "load_lf":    {"class_type": "LoadImage",             "inputs": {"image": "last.png"}},
            "resize_lf":  {"class_type": "ResizeImageMaskNode",   "inputs": {"input": ["load_lf", 0]}},
            "preproc_lf": {"class_type": "LTXVPreprocess",        "inputs": {"image": ["resize_lf", 0]}},
            "strength":   {"class_type": "PrimitiveFloat",        "inputs": {"value": 0.0}},
            "guide": {
                "class_type": "LTXVAddGuide",
                "inputs": {
                    "positive":  ["cond", 0],
                    "negative":  ["cond", 1],
                    "vae":       ["model", 2],
                    "latent":    ["latent_src", 0],
                    "image":     ["preproc_lf", 0],
                    "frame_idx": -1,
                    "strength":  ["strength", 0],
                },
            },
            "cond":       {"class_type": "LTXVConditioning",      "inputs": {"frame_rate": 24}},
            "latent_src": {"class_type": "LTXVImgToVideoInplace", "inputs": {"strength": 0.7}},
            "concat":     {"class_type": "LTXVConcatAVLatent",    "inputs": {"video_latent": ["guide", 2]}},
            "cfg":        {"class_type": "CFGGuider",             "inputs": {"positive": ["guide", 0], "negative": ["guide", 1]}},
            "crop":       {"class_type": "LTXVCropGuides",        "inputs": {"positive": ["guide", 0], "negative": ["guide", 1]}},
        }

    def test_removes_guide_and_chain(self):
        wf = self._ltx_workflow()
        strip_last_frame_guide(wf)
        for nid in ("guide", "preproc_lf", "resize_lf", "load_lf", "strength"):
            self.assertNotIn(nid, wf)

    def test_preserves_non_guide_nodes(self):
        wf = self._ltx_workflow()
        strip_last_frame_guide(wf)
        for nid in ("cond", "latent_src", "concat", "cfg", "crop"):
            self.assertIn(nid, wf)

    def test_rewires_positive_negative(self):
        wf = self._ltx_workflow()
        strip_last_frame_guide(wf)
        self.assertEqual(wf["cfg"]["inputs"]["positive"],  ["cond", 0])
        self.assertEqual(wf["cfg"]["inputs"]["negative"],  ["cond", 1])
        self.assertEqual(wf["crop"]["inputs"]["positive"], ["cond", 0])
        self.assertEqual(wf["crop"]["inputs"]["negative"], ["cond", 1])

    def test_rewires_latent(self):
        wf = self._ltx_workflow()
        strip_last_frame_guide(wf)
        self.assertEqual(wf["concat"]["inputs"]["video_latent"], ["latent_src", 0])

    def test_no_guide_node_is_noop(self):
        wf = {"a": {"class_type": "SomeOtherNode", "inputs": {}}}
        result = strip_last_frame_guide(wf)
        self.assertIn("a", result)


class TestRandomizeSeeds(unittest.TestCase):
    def test_replaces_seed(self):
        wf = {"1": {"inputs": {"seed": 42}}}
        count = randomize_seeds(wf)
        self.assertEqual(count, 1)
        self.assertNotEqual(wf["1"]["inputs"]["seed"], 42)

    def test_replaces_noise_seed(self):
        wf = {"1": {"inputs": {"noise_seed": 0}}}
        randomize_seeds(wf)
        self.assertIsInstance(wf["1"]["inputs"]["noise_seed"], int)

    def test_non_numeric_seed_ignored(self):
        wf = {"1": {"inputs": {"seed": "fixed"}}}
        count = randomize_seeds(wf)
        self.assertEqual(count, 0)
        self.assertEqual(wf["1"]["inputs"]["seed"], "fixed")

    def test_seed_in_valid_range(self):
        wf = {"1": {"inputs": {"seed": 0}}}
        randomize_seeds(wf)
        val = wf["1"]["inputs"]["seed"]
        self.assertGreaterEqual(val, 0)
        self.assertLess(val, 2**64)

    def test_multiple_nodes(self):
        wf = {
            "1": {"inputs": {"seed": 1}},
            "2": {"inputs": {"noise_seed": 2}},
            "3": {"inputs": {"other": "data"}},
        }
        count = randomize_seeds(wf)
        self.assertEqual(count, 2)


class TestLoraPathForOs(unittest.TestCase):
    def test_unix_unchanged(self):
        self.assertEqual(lora_path_for_os("loras/my.safetensors", "unix"), "loras/my.safetensors")

    def test_windows_converts_slashes(self):
        self.assertEqual(lora_path_for_os("loras/my.safetensors", "windows"), "loras\\my.safetensors")

    def test_unknown_os_unchanged(self):
        self.assertEqual(lora_path_for_os("a/b", "linux"), "a/b")


class TestApplyResolution(unittest.TestCase):
    def test_sets_width_and_height(self):
        wf = {"1": {"inputs": {"width": 512, "height": 512}}}
        apply_resolution(wf, 1024, 768)
        self.assertEqual(wf["1"]["inputs"]["width"], 1024)
        self.assertEqual(wf["1"]["inputs"]["height"], 768)

    def test_skips_nodes_without_both(self):
        wf = {"1": {"inputs": {"width": 512}}}
        apply_resolution(wf, 1024, 768)
        self.assertNotIn("height", wf["1"]["inputs"])

    def test_multiple_nodes(self):
        wf = {
            "1": {"inputs": {"width": 0, "height": 0}},
            "2": {"inputs": {"width": 0, "height": 0}},
        }
        apply_resolution(wf, 800, 600)
        self.assertEqual(wf["1"]["inputs"]["width"], 800)
        self.assertEqual(wf["2"]["inputs"]["height"], 600)


class TestApplySteps(unittest.TestCase):
    def test_sets_steps(self):
        wf = {"1": {"inputs": {"steps": 20}}}
        apply_steps(wf, 30)
        self.assertEqual(wf["1"]["inputs"]["steps"], 30)

    def test_skips_nodes_without_steps(self):
        wf = {"1": {"inputs": {"other": 5}}}
        apply_steps(wf, 30)
        self.assertNotIn("steps", wf["1"]["inputs"])


class TestFillPlaceholdersForValidation(unittest.TestCase):
    def test_fills_lora_strength(self):
        result = fill_placeholders_for_validation('"strength": <LORA_1_STRENGTH>')
        self.assertIn("1.0", result)

    def test_fills_denoise(self):
        result = fill_placeholders_for_validation('"denoise": <DENOISE>')
        self.assertIn("1.0", result)

    def test_fills_generic(self):
        result = fill_placeholders_for_validation('"prompt": "<PROMPT>"')
        self.assertIn("placeholder", result)

    def test_fills_video_settings_as_numbers(self):
        # <DURATION>/<FRAMES>/<FPS> are unquoted numeric slots, so they must parse
        # as bare numbers rather than the quoted "placeholder" string.
        template = '{"duration": <DURATION>, "frames": <FRAMES>, "fps": <FPS>}'
        parsed = json.loads(fill_placeholders_for_validation(template))
        self.assertEqual(parsed["duration"], 1)
        self.assertEqual(parsed["frames"], 1)
        self.assertEqual(parsed["fps"], 1)

    def test_fills_last_frame_strength_as_bare_number(self):
        # <LAST_FRAME_STRENGTH> is an unquoted float slot (image2video guide), so it
        # must parse as a bare JSON number rather than the quoted "placeholder" string.
        template = '{"value": <LAST_FRAME_STRENGTH>}'
        parsed = json.loads(fill_placeholders_for_validation(template))
        self.assertAlmostEqual(parsed["value"], 1.0)

    def test_result_is_parseable(self):
        # Numeric slots (<LORA_N_STRENGTH>, <DENOISE>) appear unquoted in workflow JSON;
        # string slots appear quoted. This mirrors a realistic API workflow template.
        template = '{"prompt": "<PROMPT>", "denoise": <DENOISE>, "lora": "<LORA_1_NAME>", "strength": <LORA_1_STRENGTH>}'
        result = fill_placeholders_for_validation(template)
        parsed = json.loads(result)
        self.assertEqual(parsed["prompt"], "placeholder")
        self.assertAlmostEqual(parsed["denoise"], 1.0)
        self.assertAlmostEqual(parsed["strength"], 1.0)


if __name__ == "__main__":
    unittest.main()
