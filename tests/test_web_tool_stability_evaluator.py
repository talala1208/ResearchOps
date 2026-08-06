"""Web 工具稳定性 evaluator 测试。"""

from __future__ import annotations

import json
import unittest

from src.evaluators.web_tool_stability import (
    evaluate_web_tool_stability_from_outputs,
    web_tool_stability_evaluator,
)


class WebToolStabilityEvaluatorTest(unittest.TestCase):
    """验证 Web Tool Stability Evaluator。"""

    def test_evaluate_all_tools_valid(self) -> None:
        """四类工具都返回有效数据时应得到满分。"""

        result = evaluate_web_tool_stability_from_outputs(
            {
                "web_tool_evaluation_records": [
                    {
                        "tool_name": "serp_api_search",
                        "ok": True,
                        "valid_result_count": 1,
                        "content_length": 0,
                        "failure_type": None,
                        "error": None,
                    },
                    {
                        "tool_name": "tavily_mcp_search",
                        "ok": True,
                        "valid_result_count": 1,
                        "content_length": 0,
                        "failure_type": None,
                        "error": None,
                    },
                    {
                        "tool_name": "context7_mcp_query",
                        "ok": True,
                        "valid_result_count": 1,
                        "content_length": 0,
                        "failure_type": None,
                        "error": None,
                    },
                    {
                        "tool_name": "playwright_mcp_fetch_page",
                        "ok": True,
                        "valid_result_count": 1,
                        "content_length": 800,
                        "failure_type": "none",
                        "error": None,
                    },
                ]
            }
        )

        self.assertEqual(result.overall_stability_score, 1.0)
        self.assertTrue(result.serpapi.ok)
        self.assertTrue(result.tavily.ok)
        self.assertTrue(result.context7.ok)
        self.assertTrue(result.playwright.ok)

    def test_classifies_playwright_403(self) -> None:
        """Playwright 403 应被识别为阻塞问题。"""

        result = evaluate_web_tool_stability_from_outputs(
            {
                "web_tool_evaluation_records": [
                    {
                        "tool_name": "playwright_mcp_fetch_page",
                        "ok": False,
                        "valid_result_count": 0,
                        "content_length": 0,
                        "failure_type": "403",
                        "error": "HTTP 403 Access Denied",
                    }
                ]
            }
        )

        self.assertFalse(result.playwright.ok)
        self.assertEqual(result.playwright.failure_type, "403")
        self.assertIn("Playwright", result.blocking_issues[-1])

    def test_langsmith_feedback_shape(self) -> None:
        """evaluator 应返回 LangSmith feedback 格式。"""

        feedback = web_tool_stability_evaluator(
            inputs={"question": "测试 Web 工具稳定性"},
            reference_outputs={},
            outputs={"web_tool_evaluation_records": []},
        )

        self.assertEqual(feedback["key"], "web_tool_stability")
        self.assertEqual(feedback["value"], "not_applicable")
        self.assertIn("comment", feedback)
        self.assertEqual(json.loads(feedback["comment"])["question"], "测试 Web 工具稳定性")


if __name__ == "__main__":
    unittest.main()
