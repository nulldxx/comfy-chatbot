import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comfy_progress import (ProgressListener, SAMPLER_SHARE,
                            node_titles_for, node_weights_for)


def _listener(total=4, titles=None, prompt_id="p1"):
    """A listener with no socket — tests drive _handle directly."""
    lis = ProgressListener("host:1", "cid", titles or {}, total)
    if prompt_id is not None:
        lis.bind(prompt_id)
    return lis


def _msg(mtype, **data):
    return {"type": mtype, "data": data}


class TestNodeTitles(unittest.TestCase):
    def test_prefers_meta_title(self):
        wf = {"3": {"class_type": "KSampler", "_meta": {"title": "Sampling"}}}
        self.assertEqual(node_titles_for(wf), {"3": "Sampling"})

    def test_falls_back_to_class_type(self):
        # convert_ui_to_api_format() drops _meta, so this is the real path for
        # UI-format templates, not an edge case.
        self.assertEqual(node_titles_for({"3": {"class_type": "KSampler"}}),
                         {"3": "KSampler"})

    def test_tolerates_junk(self):
        self.assertEqual(node_titles_for(None), {})
        self.assertEqual(node_titles_for({"3": "not a dict", "4": {}}), {})


class TestProgressAccounting(unittest.TestCase):
    def test_no_data_yet_is_none(self):
        self.assertIsNone(_listener().latest())

    def test_cached_nodes_count_immediately(self):
        lis = _listener(total=4)
        lis._handle(_msg("execution_cached", nodes=["1", "2"], prompt_id="p1"))
        self.assertEqual(lis.latest()["percent"], 50.0)

    def test_executing_advances_and_names_the_phase(self):
        lis = _listener(total=4, titles={"1": "Load", "2": "Sampling"})
        lis._handle(_msg("executing", node="1", prompt_id="p1"))
        self.assertEqual(lis.latest()["phase"], "Load")
        lis._handle(_msg("executing", node="2", prompt_id="p1"))
        snap = lis.latest()
        self.assertEqual(snap["phase"], "Sampling")
        self.assertEqual(snap["percent"], 25.0)      # node 1 done, 2 just started
        self.assertEqual(snap["node_index"], 2)
        self.assertEqual(snap["node_total"], 4)

    def test_step_progress_interpolates_within_the_node(self):
        lis = _listener(total=4, titles={"2": "Sampling"})
        lis._handle(_msg("executing", node="2", prompt_id="p1"))
        lis._handle(_msg("progress", node="2", value=10, max=20, prompt_id="p1"))
        snap = lis.latest()
        # Node 2 is half done and each node is a quarter of the graph.
        self.assertEqual(snap["percent"], 12.5)
        self.assertEqual((snap["step"], snap["steps"]), (10, 20))

    def test_node_null_completes_the_prompt(self):
        lis = _listener(total=4)
        lis._handle(_msg("executing", node="1", prompt_id="p1"))
        lis._handle(_msg("executing", node=None, prompt_id="p1"))
        snap = lis.latest()
        self.assertEqual(snap["percent"], 100.0)
        self.assertNotIn("phase", snap)

    def test_percent_never_decreases_across_a_second_pass(self):
        # A two-pass video graph re-runs its sampler; the raw fraction drops back
        # to zero, and a bar that goes backwards is worse than no bar.
        lis = _listener(total=4, titles={"2": "Sampling"})
        lis._handle(_msg("executing", node="2", prompt_id="p1"))
        lis._handle(_msg("progress", node="2", value=19, max=20, prompt_id="p1"))
        high = lis.latest()["percent"]
        lis._handle(_msg("progress", node="2", value=1, max=20, prompt_id="p1"))
        self.assertEqual(lis.latest()["percent"], high)

    def test_progress_for_a_new_node_closes_the_previous_one(self):
        # Some builds send `progress` without a preceding `executing`.
        lis = _listener(total=4)
        lis._handle(_msg("progress", node="1", value=1, max=1, prompt_id="p1"))
        lis._handle(_msg("progress", node="2", value=1, max=2, prompt_id="p1"))
        self.assertEqual(lis.latest()["percent"], 37.5)   # 1 done + half of node 2

    def test_execution_start_resets(self):
        lis = _listener(total=4)
        lis._handle(_msg("execution_cached", nodes=["1", "2"], prompt_id="p1"))
        lis._handle(_msg("execution_start", prompt_id="p1"))
        # Back to 0% and running — not back to "nothing heard yet".
        self.assertEqual(lis.latest()["percent"], 0.0)

    def test_foreign_prompt_is_ignored(self):
        lis = _listener(total=4)
        lis._handle(_msg("executing", node="1", prompt_id="someone-else"))
        self.assertIsNone(lis.latest())

    def test_messages_without_a_prompt_id_are_accepted(self):
        # They arrive on our own clientId socket, and older ComfyUI builds omit
        # the field — dropping them would lose all progress there.
        lis = _listener(total=4)
        lis._handle(_msg("executing", node="1"))
        self.assertIsNotNone(lis.latest())

    def test_zero_max_does_not_divide_by_zero(self):
        lis = _listener(total=4)
        lis._handle(_msg("executing", node="1", prompt_id="p1"))
        lis._handle(_msg("progress", node="1", value=0, max=0, prompt_id="p1"))
        snap = lis.latest()
        self.assertEqual(snap["percent"], 0.0)
        self.assertNotIn("steps", snap)

    def test_unknown_message_types_are_inert(self):
        lis = _listener(total=4)
        lis._handle(_msg("executed", node="1", prompt_id="p1"))
        lis._handle({"type": "progress", "data": "not a dict"})
        self.assertIsNone(lis.latest())

    def test_no_total_nodes_reports_nothing(self):
        lis = _listener(total=0)
        lis._handle(_msg("executing", node="1", prompt_id="p1"))
        self.assertIsNone(lis.latest())


class TestQueueDepth(unittest.TestCase):
    def test_queue_remaining_excludes_our_own_prompt(self):
        lis = _listener(total=4)
        lis._handle(_msg("status", status={"exec_info": {"queue_remaining": 3}}))
        self.assertEqual(lis.latest(), {"queue": 2})

    def test_queue_of_one_is_only_us(self):
        lis = _listener(total=4)
        lis._handle(_msg("status", status={"exec_info": {"queue_remaining": 1}}))
        self.assertIsNone(lis.latest())

    def test_queue_rides_alongside_a_percentage(self):
        lis = _listener(total=4)
        lis._handle(_msg("status", status={"exec_info": {"queue_remaining": 3}}))
        lis._handle(_msg("executing", node="1", prompt_id="p1"))
        self.assertEqual(lis.latest()["queue"], 2)

    def test_junk_queue_value_is_ignored(self):
        lis = _listener(total=4)
        lis._handle(_msg("status", status={"exec_info": {"queue_remaining": "lots"}}))
        self.assertIsNone(lis.latest())


class TestProgressState(unittest.TestCase):
    """Newer ComfyUI sends the whole per-node state in one message."""

    def test_finished_and_running_nodes(self):
        lis = _listener(total=4, titles={"2": "Sampling"})
        lis._handle(_msg("progress_state", prompt_id="p1", nodes={
            "1": {"state": "finished", "value": 1, "max": 1},
            "2": {"state": "running", "value": 5, "max": 20},
            "3": {"state": "pending"},
        }))
        snap = lis.latest()
        self.assertEqual(snap["phase"], "Sampling")
        self.assertEqual((snap["step"], snap["steps"]), (5, 20))
        self.assertEqual(snap["percent"], 31.2)      # (1 + 0.25) / 4, rounded

    def test_display_node_id_names_the_phase(self):
        lis = _listener(total=2, titles={"9": "Inner sampler"})
        lis._handle(_msg("progress_state", prompt_id="p1", nodes={
            "2": {"state": "running", "value": 1, "max": 4, "display_node_id": "9"},
        }))
        self.assertEqual(lis.latest()["phase"], "Inner sampler")

    def test_nothing_running_still_counts_finished_nodes(self):
        lis = _listener(total=4)
        lis._handle(_msg("progress_state", prompt_id="p1", nodes={
            "1": {"state": "finished"}, "2": {"state": "finished"},
        }))
        snap = lis.latest()
        self.assertEqual(snap["percent"], 50.0)
        self.assertNotIn("phase", snap)

    def test_junk_payload_is_ignored(self):
        lis = _listener(total=4)
        lis._handle(_msg("progress_state", prompt_id="p1", nodes="nope"))
        self.assertIsNone(lis.latest())


class TestObservedComfyUISequence(unittest.TestCase):
    """Pinned to a real message trace captured from ComfyUI 0.34.2."""

    def test_progress_state_does_not_forget_cached_nodes(self):
        # 0.34.2 sends progress_state listing ONLY the nodes it holds records
        # for — a node that arrived via execution_cached never appears in it. If
        # it replaced the done-set instead of adding to it, progress would drop
        # back on every one of these messages.
        lis = _listener(total=4)
        lis._handle(_msg("execution_cached", nodes=["1", "2"], prompt_id="p1"))
        self.assertEqual(lis.latest()["percent"], 50.0)
        lis._handle(_msg("progress_state", prompt_id="p1", nodes={
            "3": {"value": 1, "max": 1, "state": "finished"},
        }))
        self.assertEqual(lis.latest()["percent"], 75.0)

    def test_full_trace_ends_at_100_and_a_sane_node_index(self):
        lis = _listener(total=2, titles={"1": "Blank canvas", "2": "Preview"})
        for m in [
            _msg("status", status={"exec_info": {"queue_remaining": 1}}),
            _msg("execution_start", prompt_id="p1"),
            _msg("execution_cached", nodes=["1", "2"], prompt_id="p1"),
            _msg("executed", node="2", prompt_id="p1"),
            _msg("progress_state", prompt_id="p1", nodes={
                "2": {"value": 1, "max": 1, "state": "finished",
                      "display_node_id": "2"}}),
            _msg("execution_success", prompt_id="p1"),
            _msg("status", status={"exec_info": {"queue_remaining": 0}}),
            _msg("executing", node=None, prompt_id="p1"),
        ]:
            lis._handle(m)
        snap = lis.latest()
        self.assertEqual(snap["percent"], 100.0)
        # "node 3/2" would be nonsense.
        self.assertEqual(snap["node_index"], 2)
        self.assertEqual(snap["node_total"], 2)


class TestDegradation(unittest.TestCase):
    def test_dead_listener_reports_nothing(self):
        lis = _listener(total=4)
        lis._handle(_msg("executing", node="1", prompt_id="p1"))
        self.assertIsNotNone(lis.latest())
        lis._dead = True                      # what a read error leaves behind
        self.assertIsNone(lis.latest())

    def test_stop_is_safe_before_start(self):
        _listener().stop()                    # must not raise

    def test_start_without_nodes_marks_dead(self):
        lis = ProgressListener("host:1", "cid", {}, 0)
        lis.start()
        self.assertTrue(lis._dead)
        self.assertIsNone(lis.latest())


def _sampler_graph(sampler_steps=8, extras=None):
    """A minimal SamplerCustomAdvanced graph: scheduler holds the step count."""
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "m"}},
        "2": {"class_type": "BasicScheduler",
              "inputs": {"steps": sampler_steps, "model": ["1", 0]}},
        "3": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "4": {"class_type": "SamplerCustomAdvanced",
              "inputs": {"sigmas": ["2", 0], "sampler": ["3", 0],
                         "latent_image": ["5", 0]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512}},
    }
    wf.update(extras or {})
    return wf


class TestNodeWeights(unittest.TestCase):
    def _share(self, weights, *node_ids):
        total = sum(weights.values())
        return sum(weights[n] for n in node_ids) / total

    def test_samplers_own_the_agreed_share_of_the_bar(self):
        w = node_weights_for(_sampler_graph())
        self.assertAlmostEqual(self._share(w, "4"), SAMPLER_SHARE)

    def test_a_sampler_naming_node_is_not_a_sampler(self):
        # KSamplerSelect matches the class regex but takes no latent: it picks a
        # sampler, it doesn't run one, and giving it a sampler's share of the bar
        # would make the bar leap 40% when it completes instantly.
        w = node_weights_for(_sampler_graph())
        self.assertEqual(w["3"], 1.0)
        self.assertEqual(w["1"], 1.0)

    def test_steps_are_read_off_the_sampler_itself(self):
        wf = {"1": {"class_type": "KSampler",
                    "inputs": {"steps": 20, "latent_image": ["2", 0]}},
              "2": {"class_type": "EmptyLatentImage", "inputs": {"width": 512}}}
        w = node_weights_for(wf)
        self.assertAlmostEqual(self._share(w, "1"), SAMPLER_SHARE)

    def test_two_samplers_split_the_budget_by_their_steps(self):
        # A Wan high-noise/low-noise pair, or an LTX two-pass graph: the passes
        # divide the budget the way they divide the work.
        wf = _sampler_graph(sampler_steps=6, extras={
            "6": {"class_type": "BasicScheduler",
                  "inputs": {"steps": 2, "model": ["1", 0]}},
            "7": {"class_type": "SamplerCustomAdvanced",
                  "inputs": {"sigmas": ["6", 0], "latent_image": ["4", 0]}},
        })
        w = node_weights_for(wf)
        self.assertAlmostEqual(self._share(w, "4", "7"), SAMPLER_SHARE)
        self.assertAlmostEqual(w["4"] / w["7"], 3.0)      # 6 steps vs 2

    def test_steps_found_through_an_intermediate_node(self):
        # SplitSigmas between the scheduler and the sampler is still in range.
        wf = _sampler_graph()
        wf["4"]["inputs"]["sigmas"] = ["6", 0]
        wf["6"] = {"class_type": "SplitSigmas", "inputs": {"sigmas": ["2", 0]}}
        wf["8"] = {"class_type": "SamplerCustomAdvanced",
                   "inputs": {"sigmas": ["2", 0], "latent_image": ["5", 0]}}
        w = node_weights_for(wf)
        self.assertAlmostEqual(w["4"], w["8"])            # both found steps=8

    def test_unknown_steps_fall_back_without_breaking_the_share(self):
        wf = _sampler_graph()
        del wf["2"]["inputs"]["steps"]
        w = node_weights_for(wf)
        self.assertAlmostEqual(self._share(w, "4"), SAMPLER_SHARE)

    def test_decode_and_encode_outrank_an_ordinary_node(self):
        # Decoding a 121-frame video latent is not free; without this the bar
        # parks just past the sampler and looks stuck at the very end.
        w = node_weights_for(_sampler_graph(extras={
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["4", 0]}},
            "7": {"class_type": "SaveVideo", "inputs": {"video": ["6", 0]}},
        }))
        self.assertEqual(w["6"], 5.0)
        self.assertEqual(w["7"], 5.0)
        self.assertGreater(w["4"], w["6"])

    def test_a_graph_with_no_sampler_stays_uniform(self):
        # The old behaviour, which is the right thing to degrade to.
        wf = {"1": {"class_type": "LoadImage", "inputs": {}},
              "2": {"class_type": "ImageScale", "inputs": {"image": ["1", 0]}}}
        self.assertEqual(node_weights_for(wf), {"1": 1.0, "2": 1.0})

    def test_tolerates_junk(self):
        self.assertEqual(node_weights_for(None), {})
        self.assertEqual(node_weights_for({"3": "not a dict"}), {})
        self.assertEqual(node_weights_for({"4": {}}), {"4": 1.0})
        wf = {"1": {"class_type": "KSampler",
                    "inputs": {"steps": "twenty", "latent_image": ["2", 0]}},
              "2": {"class_type": "EmptyLatentImage", "inputs": None}}
        self.assertAlmostEqual(self._share(node_weights_for(wf), "1"),
                               SAMPLER_SHARE)


class TestWeightedAccounting(unittest.TestCase):
    """The listener side: a weight map changes where the bar sits, nothing else."""

    def _weighted(self, wf):
        weights = node_weights_for(wf)
        lis = ProgressListener("host:1", "cid", {}, len(wf), weights)
        lis.bind("p1")
        return lis

    def test_a_half_done_sampler_sits_near_the_middle(self):
        # Unweighted this graph would read 4/5 nodes done -> ~90%. The sampler
        # owning 85% of the bar is the whole point of the weighting.
        lis = self._weighted(_sampler_graph())
        lis._handle(_msg("execution_cached", nodes=["1", "2", "3", "5"], prompt_id="p1"))
        lis._handle(_msg("executing", node="4", prompt_id="p1"))
        lis._handle(_msg("progress", node="4", value=4, max=8, prompt_id="p1"))
        self.assertAlmostEqual(lis.latest()["percent"], 57.5, places=1)

    def test_cheap_nodes_barely_move_the_bar(self):
        # Three of the four cheap nodes done: unweighted that reads 60%, and the
        # render hasn't started. They share what the sampler doesn't own.
        lis = self._weighted(_sampler_graph())
        lis._handle(_msg("execution_cached", nodes=["1", "2", "3"], prompt_id="p1"))
        self.assertAlmostEqual(lis.latest()["percent"],
                               100.0 * (1 - SAMPLER_SHARE) * 3 / 4, places=1)

    def test_completion_still_reaches_a_hundred(self):
        lis = self._weighted(_sampler_graph())
        lis._handle(_msg("executing", node="4", prompt_id="p1"))
        lis._handle(_msg("executing", node=None, prompt_id="p1"))
        self.assertEqual(lis.latest()["percent"], 100.0)

    def test_node_caption_still_counts_nodes_not_weights(self):
        lis = self._weighted(_sampler_graph())
        lis._handle(_msg("execution_cached", nodes=["1", "2"], prompt_id="p1"))
        lis._handle(_msg("executing", node="3", prompt_id="p1"))
        snap = lis.latest()
        self.assertEqual((snap["node_index"], snap["node_total"]), (3, 5))

    def test_unknown_node_id_still_advances_the_bar(self):
        lis = self._weighted(_sampler_graph())
        before = lis.latest()
        lis._handle(_msg("executing", node="999", prompt_id="p1"))
        lis._handle(_msg("executing", node=None, prompt_id="p1"))
        self.assertIsNone(before)
        self.assertEqual(lis.latest()["percent"], 100.0)


class TestRealComfyMessageOrder(unittest.TestCase):
    """Replays the message order captured from a live ComfyUI 0.34.2 render.

    The order is the whole point: ComfyUI announces a node as *running* in a
    `progress_state` BEFORE it sends that node's `executing` message. Treating
    `executing` as "the previous node finished" therefore retired the node the
    message names, crediting its weight before it had run and freezing the bar
    for the whole sampler.
    """

    def _state(self, lis, finished=(), running=None, value=0.0, maximum=1.0):
        nodes = {n: {"value": 1.0, "max": 1.0, "state": "finished", "node_id": n,
                     "display_node_id": n, "parent_node_id": None,
                     "real_node_id": n} for n in finished}
        if running is not None:
            nodes[running] = {"value": value, "max": maximum, "state": "running",
                              "node_id": running, "display_node_id": running,
                              "parent_node_id": None, "real_node_id": running}
        lis._handle(_msg("progress_state", nodes=nodes, prompt_id="p1"))

    def _sampler_listener(self):
        wf = _sampler_graph()                       # 5 nodes, sampler "4" owns 85%
        lis = ProgressListener("host:1", "cid", node_titles_for(wf), len(wf),
                               node_weights_for(wf))
        lis.bind("p1")
        return lis

    def test_sampler_is_not_finished_the_moment_it_starts(self):
        lis = self._sampler_listener()
        cheap = ["1", "2", "3", "5"]
        for i, node in enumerate(cheap):
            self._state(lis, finished=cheap[:i], running=node)
            lis._handle(_msg("executing", node=node, prompt_id="p1"))
        # Sampler announced running, then its executing message.
        self._state(lis, finished=cheap, running="4")
        lis._handle(_msg("executing", node="4", prompt_id="p1"))
        at_start = lis.latest()["percent"]
        self.assertLess(at_start, 100.0 * (1 - SAMPLER_SHARE) + 1.0)

    def test_the_bar_advances_with_the_sampler_steps(self):
        lis = self._sampler_listener()
        cheap = ["1", "2", "3", "5"]
        self._state(lis, finished=cheap, running="4")
        lis._handle(_msg("executing", node="4", prompt_id="p1"))
        seen = [lis.latest()["percent"]]
        for step in range(1, 9):
            lis._handle(_msg("progress", node="4", value=step, max=8, prompt_id="p1"))
            seen.append(lis.latest()["percent"])
        self.assertEqual(seen, sorted(seen))            # never goes backwards
        self.assertLess(seen[0], 20.0)                  # starts near the slice's foot
        self.assertGreater(seen[-1], 90.0)              # and reaches its head
        self.assertGreater(len(set(seen)), 5)           # actually moved, step by step

    def test_executing_still_retires_a_different_node(self):
        # An older ComfyUI that sends no progress_state at all must still work:
        # there, `executing B` is the only signal that A has finished.
        lis = _listener(total=4)
        lis._handle(_msg("executing", node="1", prompt_id="p1"))
        lis._handle(_msg("executing", node="2", prompt_id="p1"))
        self.assertEqual(lis.latest()["percent"], 25.0)


if __name__ == "__main__":
    unittest.main()
