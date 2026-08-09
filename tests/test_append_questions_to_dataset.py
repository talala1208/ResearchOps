"""LangSmith Dataset 追加问题脚本测试。"""

from __future__ import annotations

import unittest

from src.dataset.append_questions_to_dataset import build_inputs, examples


class AppendQuestionsToDatasetTest(unittest.TestCase):
    """验证问题转换逻辑。"""

    def test_build_inputs_uses_first_item_from_tuple(self) -> None:
        """兼容 (question, answer) 二元组，但只取 question。"""

        inputs = build_inputs([("问题一", "答案占位"), ("问题二", "")])

        self.assertEqual(inputs, [{"question": "问题一"}, {"question": "问题二"}])

    def test_build_inputs_supports_custom_input_key(self) -> None:
        """支持指定 inputs 字段名。"""

        inputs = build_inputs(["问题一"], input_key="user_query")

        self.assertEqual(inputs, [{"user_query": "问题一"}])

    def test_formal_examples_match_scene_requirements(self) -> None:
        """正式评测集应保持预定的场景数量和可验证元数据。"""

        scene_counts: dict[str, int] = {}
        for example in examples:
            self.assertEqual(set(example["inputs"]), {"user_query"})
            self.assertTrue(example["inputs"]["user_query"].strip())

            metadata = example["metadata"]
            self.assertIn(metadata["expected_outcome"], {"standard_report", "degraded_report"})
            scene = metadata["scene"]
            scene_counts[scene] = scene_counts.get(scene, 0) + 1

        self.assertEqual(len(examples), 19)
        self.assertEqual(
            scene_counts,
            {
                "formal_general_research": 5,
                "formal_url_research": 3,
                "formal_local_web_research": 3,
                "formal_complex_research": 3,
                "formal_dangerous_direct_degradation": 5,
            },
        )


if __name__ == "__main__":
    unittest.main()
