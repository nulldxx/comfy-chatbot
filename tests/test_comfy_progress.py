import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comfy_progress import ProgressListener, node_titles_for


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


if __name__ == "__main__":
    unittest.main()
