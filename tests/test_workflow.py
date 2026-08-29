import json
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workflow import (
    apply_placeholders,
    apply_resolution,
    apply_seed,
    apply_steps,
    collect_seeds,
    fill_lora_sentinels,
    fill_placeholders_for_validation,
    find_placeholders,
    lora_path_for_os,
    randomize_seeds,
    strip_last_frame_guide,
    strip_lora_nodes,
    strip_reference_nodes,
    reference_sentinel,
    reference_marker,
    find_marked_node,
    drop_node_output_links,
    node_link_output_indices,
    optimisation_nodes,
    bypass_optimisation_nodes,
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


class TestStripReferenceNodes(unittest.TestCase):
    def _workflow_with_unset_refs(self):
        # A MiniMax-style graph: a video node with three image inputs, a ref video and
        # a ref audio. Images 2/3, video and audio are unfilled (sentinel loaders).
        s2 = reference_sentinel("REFERENCE_IMAGE_2")
        s3 = reference_sentinel("REFERENCE_IMAGE_3")
        sv = reference_sentinel("REFERENCE_VIDEO")
        sa = reference_sentinel("REFERENCE_AUDIO")
        return {
            "img1": {"class_type": "LoadImage", "inputs": {"image": "real_image.png"}},
            "img2": {"class_type": "LoadImage", "inputs": {"image": s2}},
            "img3": {"class_type": "LoadImage", "inputs": {"image": s3}},
            "vid":  {"class_type": "LoadVideo", "inputs": {"video": sv}},
            "aud":  {"class_type": "LoadAudio", "inputs": {"audio": sa}},
            "mm":   {"class_type": "MiniMaxH3", "inputs": {
                "image_1": ["img1", 0],
                "image_2": ["img2", 0],
                "image_3": ["img3", 0],
                "ref_video": ["vid", 0],
                "ref_audio": ["aud", 0],
                "prompt": "hello",
            }},
        }, {s2, s3, sv, sa}

    def test_removes_unset_loader_nodes(self):
        wf, sentinels = self._workflow_with_unset_refs()
        result, removed = strip_reference_nodes(wf, sentinels)
        for nid in ("img2", "img3", "vid", "aud"):
            self.assertNotIn(nid, result)
        self.assertEqual(set(removed), {"img2", "img3", "vid", "aud"})

    def test_keeps_filled_loader(self):
        wf, sentinels = self._workflow_with_unset_refs()
        result, _ = strip_reference_nodes(wf, sentinels)
        self.assertIn("img1", result)

    def test_drops_consumer_inputs_for_removed(self):
        wf, sentinels = self._workflow_with_unset_refs()
        result, _ = strip_reference_nodes(wf, sentinels)
        mm_inputs = result["mm"]["inputs"]
        # The optional inputs whose loaders were stripped are gone entirely...
        for key in ("image_2", "image_3", "ref_video", "ref_audio"):
            self.assertNotIn(key, mm_inputs)
        # ...while the filled image and the plain prompt remain.
        self.assertEqual(mm_inputs["image_1"], ["img1", 0])
        self.assertEqual(mm_inputs["prompt"], "hello")

    def test_empty_sentinels_no_change(self):
        wf, _ = self._workflow_with_unset_refs()
        before = json.loads(json.dumps(wf))
        result, removed = strip_reference_nodes(wf, set())
        self.assertEqual(removed, [])
        self.assertEqual(result, before)


class TestReferenceMarker(unittest.TestCase):
    def test_distinct_from_sentinel(self):
        # The two scans (unset-slot sentinels vs. node locators) must never cross-match:
        # a marked node is a *filled* slot and must survive strip_reference_nodes.
        self.assertNotEqual(reference_marker("REFERENCE_VIDEO_1"),
                            reference_sentinel("REFERENCE_VIDEO_1"))
        wf = {"vid": {"class_type": "LoadVideo",
                      "inputs": {"video": reference_marker("REFERENCE_VIDEO_1")}}}
        _, removed = strip_reference_nodes(
            wf, {reference_sentinel("REFERENCE_VIDEO_1")})
        self.assertEqual(removed, [])
        self.assertIn("vid", wf)

    def test_per_token(self):
        self.assertNotEqual(reference_marker("REFERENCE_VIDEO_1"),
                            reference_marker("REFERENCE_VIDEO_2"))

    def test_find_marked_node(self):
        marker = reference_marker("REFERENCE_VIDEO_2")
        wf = {
            "a": {"class_type": "LoadVideo", "inputs": {"video": "other.mp4"}},
            "b": {"class_type": "LoadVideo", "inputs": {"video": marker}},
        }
        self.assertEqual(find_marked_node(wf, marker), ("b", "video"))

    def test_find_marked_node_absent(self):
        wf = {"a": {"class_type": "LoadVideo", "inputs": {"video": "other.mp4"}}}
        self.assertEqual(find_marked_node(wf, reference_marker("X")), (None, None))


class TestDropNodeOutputLinks(unittest.TestCase):
    def _workflow(self):
        # One VHS loader driving both an image and an audio consumer input, plus a
        # second, unrelated loader that must be left completely alone.
        return {
            "vid":   {"class_type": "VHS_LoadVideo", "inputs": {"video": "clip.mp4"}},
            "other": {"class_type": "VHS_LoadVideo", "inputs": {"video": "other.mp4"}},
            "mm":    {"class_type": "MiniMaxH3", "inputs": {
                "ref_video":       ["vid", 0],
                "ref_video_audio": ["vid", 2],
                "ref_video_2":     ["other", 0],
                "prompt":          "hello",
            }},
        }

    def test_drops_only_named_indices(self):
        wf = self._workflow()
        removed = drop_node_output_links(wf, "vid", [2])
        self.assertEqual(removed, [("mm", "ref_video_audio")])
        self.assertNotIn("ref_video_audio", wf["mm"]["inputs"])
        self.assertEqual(wf["mm"]["inputs"]["ref_video"], ["vid", 0])

    def test_producer_survives(self):
        wf = self._workflow()
        drop_node_output_links(wf, "vid", [0])
        # The node still has to load the clip for the track that IS wanted.
        self.assertIn("vid", wf)
        self.assertEqual(wf["vid"]["inputs"]["video"], "clip.mp4")
        self.assertNotIn("ref_video", wf["mm"]["inputs"])
        self.assertEqual(wf["mm"]["inputs"]["ref_video_audio"], ["vid", 2])

    def test_other_producers_untouched(self):
        wf = self._workflow()
        drop_node_output_links(wf, "vid", [0, 2])
        self.assertEqual(wf["mm"]["inputs"]["ref_video_2"], ["other", 0])
        self.assertEqual(wf["mm"]["inputs"]["prompt"], "hello")

    def test_empty_indices_no_change(self):
        wf = self._workflow()
        before = json.loads(json.dumps(wf))
        self.assertEqual(drop_node_output_links(wf, "vid", []), [])
        self.assertEqual(wf, before)


class TestBypassOptimisationNodes(unittest.TestCase):
    def _workflow(self):
        # The video templates' shape: a chain of MODEL -> MODEL passthroughs between the
        # UNETLoader and the guider/scheduler, plus a same-class user LoRA that carries
        # no [opt:] marker and must never be bypassed.
        return {
            "unet":  {"class_type": "UNETLoader", "inputs": {"unet_name": "h3.safetensors"}},
            "turbo": {"_meta": {"title": "[opt:turbo] Load LoRA"},
                      "class_type": "LoraLoaderModelOnly",
                      "inputs": {"lora_name": "turbo.safetensors", "model": ["unet", 0]}},
            "user":  {"_meta": {"title": "Load LoRA 1"},
                      "class_type": "LoraLoaderModelOnly",
                      "inputs": {"lora_name": "user.safetensors", "model": ["turbo", 0]}},
            "sage":  {"_meta": {"title": "[opt:sage] Patch Sage Attention KJ"},
                      "class_type": "PathchSageAttentionKJ", "inputs": {"model": ["user", 0]}},
            "sol":   {"_meta": {"title": "[opt:sol] Patch Sol-Attn"},
                      "class_type": "SolAttnPatch", "inputs": {"model": ["sage", 0]}},
            "guide": {"class_type": "BasicGuider", "inputs": {"model": ["sol", 0]}},
            "sched": {"class_type": "BasicScheduler", "inputs": {"model": ["sol", 0], "steps": 20}},
        }

    def test_finds_marked_nodes_only(self):
        found = optimisation_nodes(self._workflow())
        self.assertEqual(found, {"turbo": ["turbo"], "sage": ["sage"], "sol": ["sol"]})

    def test_removes_node_and_rewires_model(self):
        wf = self._workflow()
        wf, removed = bypass_optimisation_nodes(wf, {"sol"})
        self.assertEqual(removed, ["sol"])
        self.assertNotIn("sol", wf)
        self.assertEqual(wf["guide"]["inputs"]["model"], ["sage", 0])
        self.assertEqual(wf["sched"]["inputs"]["model"], ["sage", 0])

    def test_chained_removals_collapse_the_chain(self):
        wf = self._workflow()
        wf, removed = bypass_optimisation_nodes(wf, ["sage", "sol"])
        self.assertEqual(sorted(removed), ["sage", "sol"])
        # Both gone, and the guider reaches straight past them to the user LoRA.
        self.assertEqual(wf["guide"]["inputs"]["model"], ["user", 0])

    def test_all_off_leaves_unet_driving_the_guider(self):
        wf = self._workflow()
        wf, _ = bypass_optimisation_nodes(wf, ["turbo", "sage", "sol"])
        self.assertEqual(wf["user"]["inputs"]["model"], ["unet", 0])
        self.assertEqual(wf["guide"]["inputs"]["model"], ["user", 0])

    def test_unmarked_same_class_node_survives(self):
        wf = self._workflow()
        wf, _ = bypass_optimisation_nodes(wf, ["turbo"])
        self.assertIn("user", wf)
        self.assertEqual(wf["user"]["inputs"]["lora_name"], "user.safetensors")

    def test_key_absent_from_template_is_a_no_op(self):
        wf = self._workflow()
        before = json.loads(json.dumps(wf))
        wf, removed = bypass_optimisation_nodes(wf, ["spectrum"])
        self.assertEqual(removed, [])
        self.assertEqual(wf, before)

    def test_nothing_disabled_is_a_no_op(self):
        wf = self._workflow()
        before = json.loads(json.dumps(wf))
        self.assertEqual(bypass_optimisation_nodes(wf, set())[1], [])
        self.assertEqual(wf, before)

    def _full_chain(self):
        """All five optimisations chained as the consolidated H3 templates have them."""
        wf = {"unet": {"class_type": "UNETLoader", "inputs": {"unet_name": "h3.safetensors"}}}
        prev = "unet"
        for key in ("turbo", "sage", "sol", "cache", "spectrum"):
            wf[key] = {"_meta": {"title": f"[opt:{key}] node"},
                       "class_type": f"Opt{key.capitalize()}",
                       "inputs": {"model": [prev, 0]}}
            prev = key
        wf["guide"] = {"class_type": "BasicGuider", "inputs": {"model": [prev, 0]}}
        wf["sched"] = {"class_type": "BasicScheduler", "inputs": {"model": [prev, 0], "steps": 20}}
        return wf

    def test_every_subset_leaves_the_graph_intact(self):
        """All 32 on/off combinations must leave a whole graph, not a dangling ref."""
        keys = ["turbo", "sage", "sol", "cache", "spectrum"]
        for bits in range(32):
            disabled = {k for i, k in enumerate(keys) if bits >> i & 1}
            with self.subTest(disabled=sorted(disabled)):
                wf, _ = bypass_optimisation_nodes(self._full_chain(), disabled)
                for nid, node in wf.items():
                    for k, v in node.get("inputs", {}).items():
                        if isinstance(v, list) and len(v) == 2:
                            self.assertIn(v[0], wf, f"{nid}.{k} dangles at {bits:05b}")
                # The guider and scheduler still trace back to the UNETLoader.
                for consumer in ("guide", "sched"):
                    seen, ref = set(), wf[consumer]["inputs"]["model"]
                    while ref[0] != "unet":
                        self.assertNotIn(ref[0], seen, "cycle in the model chain")
                        seen.add(ref[0])
                        ref = wf[ref[0]]["inputs"]["model"]
                self.assertEqual(len(wf), 8 - len(disabled))

    def test_raises_when_node_has_no_model_input(self):
        wf = self._workflow()
        del wf["sol"]["inputs"]["model"]
        # Deleting it would leave the guider pointing at nothing; refuse instead.
        with self.assertRaises(ValueError):
            bypass_optimisation_nodes(wf, ["sol"])


class TestNodeLinkOutputIndices(unittest.TestCase):
    def test_splits_by_consumer_input_name(self):
        wf = {
            "vid": {"class_type": "VHS_LoadVideo", "inputs": {"video": "clip.mp4"}},
            "mm":  {"class_type": "MiniMaxH3", "inputs": {
                "ref_video": ["vid", 0], "ref_video_AUDIO": ["vid", 3]}},
        }
        self.assertEqual(node_link_output_indices(wf, "vid"), ([3], [0]))

    def test_no_consumers(self):
        wf = {"vid": {"class_type": "VHS_LoadVideo", "inputs": {"video": "clip.mp4"}}}
        self.assertEqual(node_link_output_indices(wf, "vid"), ([], []))


class TestStripLastFrameGuide(unittest.TestCase):
    def _ltx_workflow(self):
        # Minimal representation of the LTX 2.3 last-frame subgraph.
        # "width_prim" and "height_prim" are shared with the main resize ("main_resize"),
        # mirroring the real workflow where 320:312/320:299 are referenced by both
        # the last-frame resize and the main image resize.
        return {
            "width_prim":  {"class_type": "PrimitiveInt",          "inputs": {"value": 1280}},
            "height_prim": {"class_type": "PrimitiveInt",          "inputs": {"value": 720}},
            "load_lf":    {"class_type": "LoadImage",             "inputs": {"image": "last.png"}},
            "resize_lf":  {"class_type": "ResizeImageMaskNode",   "inputs": {
                "input":              ["load_lf", 0],
                "resize_type.width":  ["width_prim", 0],
                "resize_type.height": ["height_prim", 0],
            }},
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
            "main_resize": {"class_type": "ResizeImageMaskNode",  "inputs": {
                "resize_type.width":  ["width_prim", 0],
                "resize_type.height": ["height_prim", 0],
            }},
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

    def test_preserves_shared_primitives(self):
        # width_prim and height_prim are referenced by both resize_lf and main_resize;
        # they must survive even though the trace reaches them via resize_lf.
        wf = self._ltx_workflow()
        strip_last_frame_guide(wf)
        self.assertIn("width_prim", wf)
        self.assertIn("height_prim", wf)
        self.assertIn("main_resize", wf)

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


class TestApplySeed(unittest.TestCase):
    def test_applies_fixed_seed_to_all(self):
        wf = {
            "1": {"inputs": {"seed": 1}},
            "2": {"inputs": {"noise_seed": 2}},
            "3": {"inputs": {"other": "data"}},
        }
        count = apply_seed(wf, 12345)
        self.assertEqual(count, 2)
        self.assertEqual(wf["1"]["inputs"]["seed"], 12345)
        self.assertEqual(wf["2"]["inputs"]["noise_seed"], 12345)
        self.assertEqual(wf["3"]["inputs"]["other"], "data")

    def test_ignores_non_numeric_seed(self):
        wf = {"1": {"inputs": {"seed": "fixed"}}}
        count = apply_seed(wf, 7)
        self.assertEqual(count, 0)
        self.assertEqual(wf["1"]["inputs"]["seed"], "fixed")

    def test_reuse_reproduces_randomized_seed(self):
        wf = {"1": {"inputs": {"seed": 0}}}
        randomize_seeds(wf)
        remembered = collect_seeds(wf)[0]
        wf2 = {"1": {"inputs": {"seed": 999}}}
        apply_seed(wf2, remembered)
        self.assertEqual(wf2["1"]["inputs"]["seed"], remembered)


class TestCollectSeeds(unittest.TestCase):
    def test_collects_int_seeds(self):
        wf = {
            "1": {"inputs": {"seed": 11}},
            "2": {"inputs": {"noise_seed": 22}},
            "3": {"inputs": {"other": "data"}},
        }
        self.assertEqual(sorted(collect_seeds(wf)), [11, 22])

    def test_ignores_bool_and_non_numeric(self):
        wf = {
            "1": {"inputs": {"seed": True}},
            "2": {"inputs": {"seed": "fixed"}},
            "3": {"inputs": {"seed": 5}},
        }
        self.assertEqual(collect_seeds(wf), [5])

    def test_empty_when_no_seeds(self):
        self.assertEqual(collect_seeds({"1": {"inputs": {"cfg": 7}}}), [])


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
