import os
import sys
import queue
import threading
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app


class TestListWorkflowNames(unittest.TestCase):
    def test_lists_nested_names_with_forward_slashes(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "flux").mkdir()
            (base / "top.json").write_text("{}")
            (base / "flux" / "standard.json").write_text("{}")
            (base / "flux" / "turbo.json").write_text("{}")

            names = app.list_workflow_names(base)

        self.assertEqual(names, ["flux/standard", "flux/turbo", "top"])

    def test_missing_dir_returns_empty(self):
        self.assertEqual(app.list_workflow_names(Path("/no/such/dir")), [])


class TestRunGenerationTraversalGuard(unittest.TestCase):
    def _run(self, workflow_name, workflow_dir):
        job_id = "test-job"
        app.jobs[job_id] = {
            "status": "pending",
            "queue": queue.Queue(),
            "images": [],
            "cancel": threading.Event(),
            "server": "127.0.0.1:8000",
            "prompt_id": None,
        }
        app.run_generation(
            job_id, "a cat", [], "127.0.0.1:8000", "unix", workflow_name,
            workflow_dir=workflow_dir,
        )
        status = app.jobs[job_id]["status"]
        del app.jobs[job_id]
        return status

    def test_escape_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "generation"
            base.mkdir()
            # A real file living *outside* the workflow dir that "../" would reach.
            (Path(d) / "secret.json").write_text("{}")
            status = self._run("../secret", base)
        # The traversal guard makes resolution fail before any network call.
        self.assertEqual(status, "error")

    def test_image2image_escape_name_is_rejected(self):
        # The image2image dir is confined the same way as the other families.
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "image2image"
            base.mkdir()
            (Path(d) / "secret.json").write_text("{}")
            status = self._run("../secret", base)
        self.assertEqual(status, "error")


class TestImage2ImageWiring(unittest.TestCase):
    def test_listing_resolves_under_workflow_dir(self):
        # list_image2image_workflows() reads the image2image/ subdir using the
        # same recursive name listing as the other families.
        self.assertEqual(app.COMFY_IMAGE2IMAGE_DIR, app.COMFY_WORKFLOW_DIR / "image2image")

    def test_listing_finds_nested_names(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "zit-i2i.json").write_text("{}")
            self.assertEqual(app.list_workflow_names(base), ["zit-i2i"])


if __name__ == "__main__":
    unittest.main()
