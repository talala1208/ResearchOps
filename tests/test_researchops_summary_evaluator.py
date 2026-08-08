"""ResearchOps summary evaluator 测试。"""

from __future__ import annotations

import json
import unittest

from src.evaluators.researchops_summary import (
    evaluate_researchops_summary,
    researchops_summary_evaluator,
)


class ResearchOpsSummaryEvaluatorTest(unittest.TestCase):
    """验证 ResearchOps summary evaluator。"""

    def test_evaluate_researchops_summary_success(self) -> None:
        """所有 runs 成功时应得到高分。"""

        result = evaluate_researchops_summary(
            [
                {
                    "final_report": "完整报告" * 100,
                    "output_artifacts": {"final_report": "outputs/reports/a.md"},
                    "evidence_sufficient": True,
                    "evidence_sufficiency_score": 0.9,
                    "review_result": {"passed": True, "report_score": 0.85},
                    "safety_review_result": {"safety_pass": True},
                    "degraded": False,
                },
                {
                    "final_report": "完整报告" * 80,
                    "output_artifacts": {"metrics": "outputs/runs/b.json"},
                    "evidence_sufficient": True,
                    "evidence_sufficiency_score": 0.8,
                    "review_result": {"passed": True, "report_score": 0.8},
                    "safety_review_result": {"safety_pass": True},
                    "degraded": False,
                },
            ]
        )

        self.assertEqual(result.total_runs, 2)
        self.assertEqual(result.final_report_rate, 1.0)
        self.assertEqual(result.safety_pass_rate, 1.0)
        self.assertGreater(result.overall_summary_score, 0.8)

    def test_evaluate_researchops_summary_detects_blocking_issues(self) -> None:
        """失败 runs 应产生 blocking issues。"""

        result = evaluate_researchops_summary(
            [
                {
                    "final_report": "",
                    "evidence_sufficient": False,
                    "evidence_sufficiency_score": 0.4,
                    "review_result": {"passed": False, "report_score": 0.3},
                    "safety_review_result": {"safety_pass": False},
                    "degraded": True,
                    "web_hitl_required": True,
                }
            ]
        )

        self.assertLess(result.overall_summary_score, 0.5)
        self.assertTrue(result.blocking_issues)
        self.assertEqual(result.unresolved_hitl_rate, 1.0)

    def test_langsmith_summary_feedback_shape(self) -> None:
        """summary evaluator 应返回 LangSmith feedback 格式。"""

        feedback = researchops_summary_evaluator(outputs=[])

        self.assertEqual(feedback["key"], "researchops_summary_score")
        self.assertEqual(feedback["score"], 0.0)
        self.assertEqual(json.loads(feedback["comment"])["total_runs"], 0)


if __name__ == "__main__":
    unittest.main()
