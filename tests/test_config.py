import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import _norm_workflow_default


class TestNormWorkflowDefault(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(_norm_workflow_default(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_norm_workflow_default(""))

    def test_plain_name_unchanged(self):
        self.assertEqual(_norm_workflow_default("my-workflow"), "my-workflow")

    def test_strips_json_suffix(self):
        self.assertEqual(_norm_workflow_default("my-workflow.json"), "my-workflow")

    def test_nested_forward_slashes_unchanged(self):
        self.assertEqual(_norm_workflow_default("flux/my-workflow"), "flux/my-workflow")

    def test_nested_with_json_suffix(self):
        self.assertEqual(_norm_workflow_default("flux/my-workflow.json"), "flux/my-workflow")

    def test_backslashes_converted_to_forward_slashes(self):
        self.assertEqual(_norm_workflow_default("flux\\my-workflow"), "flux/my-workflow")

    def test_backslashes_with_json_suffix(self):
        self.assertEqual(_norm_workflow_default("flux\\my-workflow.json"), "flux/my-workflow")


class TestDockerfilePackaging(unittest.TestCase):
    """Every local module the app imports must be COPYed into the image.

    The Dockerfile lists modules one COPY line at a time rather than copying the
    directory, so adding a module and forgetting the line builds an image that
    dies on import — and nothing catches it until the container test, minutes into
    a Docker build. This is the same check for the price of a few milliseconds."""

    def test_every_local_module_is_copied(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local = {f[:-3] for f in os.listdir(root)
                 if f.endswith(".py") and not f.startswith("_")}

        with open(os.path.join(root, "Dockerfile"), encoding="utf-8") as fh:
            dockerfile = fh.read()

        with open(os.path.join(root, "app.py"), encoding="utf-8") as fh:
            app_src = fh.read()

        # Modules app.py imports directly, restricted to ones that live here.
        imported = set()
        for line in app_src.splitlines():
            line = line.strip()
            for prefix in ("import ", "from "):
                if line.startswith(prefix):
                    name = line[len(prefix):].split()[0].split(".")[0]
                    if name in local:
                        imported.add(name)

        missing = sorted(m for m in imported
                         if f"COPY {m}.py ." not in dockerfile)
        self.assertEqual(missing, [], f"not COPYed in the Dockerfile: {missing}")


if __name__ == "__main__":
    unittest.main()
