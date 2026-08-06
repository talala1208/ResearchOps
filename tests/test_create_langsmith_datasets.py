"""ResearchOps LangSmith 数据集脚本测试。"""

from __future__ import annotations

import unittest

from scripts.create_langsmith_datasets import build_dataset_specs


class CreateLangSmithDatasetsTest(unittest.TestCase):
    """验证数据集定义。"""

    def test_build_dataset_specs_contains_core_datasets(self) -> None:
        """数据集应覆盖核心手动评估场景。"""

        specs = build_dataset_specs()
        names = {spec.name for spec in specs}

        self.assertIn("researchops-input-guard-v1", names)
        self.assertIn("researchops-standard-research-v1", names)
        self.assertIn("researchops-url-web-search-v1", names)
        self.assertIn("researchops-local-and-structured-v1", names)
        self.assertIn("researchops-edge-cases-v1", names)

    def test_examples_only_include_user_query_inputs(self) -> None:
        """每条 example 只包含 inputs.user_query。"""

        for spec in build_dataset_specs():
            for example in spec.examples:
                self.assertEqual(set(example), {"inputs"})
                self.assertEqual(set(example["inputs"]), {"user_query"})
                self.assertIsInstance(example["inputs"]["user_query"], str)
                self.assertTrue(example["inputs"]["user_query"])


if __name__ == "__main__":
    unittest.main()
