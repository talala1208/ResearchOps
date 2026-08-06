"""ResearchOps Pairwise A/B evaluator 测试。"""

from __future__ import annotations

import unittest

from src.evaluators.research_report_pairwise import (
    _extract_question,
    _extract_report,
    _preference_to_scores,
)


class ResearchReportPairwiseTest(unittest.TestCase):
    """验证 Pairwise evaluator 的纯规则辅助函数。"""

    def test_preference_to_scores(self) -> None:
        """偏好值应转换为 LangSmith pairwise scores。"""

        self.assertEqual(_preference_to_scores(1), [1, 0])
        self.assertEqual(_preference_to_scores(2), [0, 1])
        self.assertEqual(_preference_to_scores(0), [0, 0])

    def test_extract_question_supports_question_and_user_query(self) -> None:
        """兼容 question 和 user_query 两种 dataset input 字段。"""

        self.assertEqual(_extract_question({"question": "问题"}), "问题")
        self.assertEqual(_extract_question({"user_query": "查询"}), "查询")

    def test_extract_report_supports_nested_output(self) -> None:
        """兼容 graph 输出和普通链输出。"""

        self.assertEqual(_extract_report({"final_report": "最终报告"}), "最终报告")
        self.assertEqual(
            _extract_report({"output": {"final_report": "嵌套报告"}}),
            "嵌套报告",
        )
        self.assertEqual(_extract_report({}), "N/A")


if __name__ == "__main__":
    unittest.main()
