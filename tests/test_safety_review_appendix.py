"""safety_review 定稿时追加引用证据附录。"""

from __future__ import annotations

import unittest

from src.workflow.guard_nodes import safety_review


class SafetyReviewAppendixTest(unittest.TestCase):
    """验证 final_report 末尾含代码生成的引用证据列表。"""

    def test_safety_review_appends_cited_evidence_appendix(self) -> None:
        state = {
            "report_draft": "# 研究报告\n\n正文引用 [E1]。\n",
            "executed_nodes": [],
            "entity_index": {
                "used_evidence_ids": ["E1", "E2"],
            },
            "evidence_items": {
                "E1": {
                    "evidence_id": "E1",
                    "question_id": "Q1",
                    "source_type": "blog",
                    "source_name": "serp",
                    "url_or_path": "https://example.com/a",
                    "title": "网页标题",
                    "snippet": "s",
                    "published_at": None,
                    "collected_by": "web_search_sub_agent",
                    "freshness_score": 0.5,
                    "relevance_score": 0.5,
                    "answer_coverage_score": 0.5,
                    "source_confidence_score": 0.5,
                    "reliability_score": 0.5,
                    "score_reason": None,
                    "used_in_final_report": True,
                },
                "E2": {
                    "evidence_id": "E2",
                    "question_id": "Q1",
                    "source_type": "local_document",
                    "source_name": "local_markdown_documents",
                    "url_or_path": "/docs/a.md",
                    "title": "本地标题",
                    "snippet": "s",
                    "published_at": None,
                    "collected_by": "local_document_search_tool",
                    "freshness_score": 0.5,
                    "relevance_score": 0.5,
                    "answer_coverage_score": 0.5,
                    "source_confidence_score": 0.5,
                    "reliability_score": 0.5,
                    "score_reason": None,
                    "used_in_final_report": True,
                },
            },
        }

        result = safety_review(state)
        final_report = result["final_report"]
        self.assertIn("## 引用证据", final_report)
        self.assertIn("### Web 证据", final_report)
        self.assertIn("- [E1][网页标题](https://example.com/a)", final_report)
        self.assertIn("### Local 证据", final_report)
        self.assertIn("- [E2] `a.md` — 本地标题", final_report)
        self.assertEqual(result["report_draft"], "")

    def test_safety_review_allows_documentation_placeholders(self) -> None:
        """文档中的明确密钥占位符不应触发安全降级。"""

        state = {
            "report_draft": (
                "配置示例：\n"
                "api_key=YOUR_API_KEY\n"
                "Authorization: Bearer <token>\n"
                "API_KEY=${API_KEY}\n"
                "api-key=xxxxxxxxxxxxxxxx\n"
            ),
            "executed_nodes": [],
            "entity_index": {"used_evidence_ids": []},
            "evidence_items": {},
        }

        result = safety_review(state)

        self.assertTrue(result["safety_review_result"]["safety_pass"])
        self.assertEqual(result["safety_review_result"]["safety_risk_level"], "low")
        self.assertEqual(result["safety_review_result"]["detected_risks"], [])
        self.assertFalse(result["safety_review_result"]["downgrade_required"])

    def test_safety_review_rejects_credential_shaped_values(self) -> None:
        """具有真实凭据长度和形态的值仍应触发安全降级。"""

        state = {
            "report_draft": (
                "api_key=q1W2e3R4t5Y6u7I8o9P0\n"
                "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature\n"
            ),
            "executed_nodes": [],
            "entity_index": {"used_evidence_ids": []},
            "evidence_items": {},
        }

        result = safety_review(state)

        self.assertFalse(result["safety_review_result"]["safety_pass"])
        self.assertEqual(result["safety_review_result"]["safety_risk_level"], "high")
        self.assertEqual(
            result["safety_review_result"]["detected_risks"],
            ["sensitive_secret_pattern"],
        )
        self.assertTrue(result["safety_review_result"]["downgrade_required"])
        self.assertNotIn("q1W2e3R4t5Y6u7I8o9P0", result["final_report"])
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", result["final_report"])
        self.assertIn("危险正文已被移除", result["final_report"])
        self.assertNotIn("## 引用证据", result["final_report"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["entity_index"]["used_evidence_ids"], [])


if __name__ == "__main__":
    unittest.main()
