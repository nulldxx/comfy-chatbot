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


if __name__ == "__main__":
    unittest.main()
