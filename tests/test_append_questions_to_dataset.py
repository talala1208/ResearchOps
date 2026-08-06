"""LangSmith Dataset 追加问题脚本测试。"""

from __future__ import annotations

import unittest

from src.dataset.append_questions_to_dataset import build_inputs


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


if __name__ == "__main__":
    unittest.main()
