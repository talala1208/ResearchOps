"""研究报告 LLM 节点离线 mock 测试。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.llm.structured_outputs import ResearchReportOutput, ResearchReportReviewOutput
from src.workflow.report_nodes import generate_research_report, review_research_report


def _base_state(**overrides: object) -> dict:
    state = {
        "user_query": "比较两款 AI 编程助手",
        "research_goal": "比较能力与限制",
        "degraded": False,
        "degradation_reason": None,
        "review_revision_count": 0,
        "sub_questions": {
            "Q1": {
                "question_id": "Q1",
                "question": "核心能力差异",
                "priority": "high",
                "required_source_types": ["blog"],
            }
        },
        "evidence_matrix": {
            "Q1": {
                "question_id": "Q1",
                "evidence_ids": ["E1"],
                "high_quality_evidence_ids": ["E1"],
                "covered_source_types": ["blog"],
                "conflict_ids": [],
            }
        },
        "evidence_items": {
            "E1": {
                "evidence_id": "E1",
                "question_id": "Q1",
                "source_type": "blog",
                "source_name": "serpapi",
                "url_or_path": "https://example.com/e1",
                "title": "能力对比",
                "snippet": "助手 A 支持本地索引",
                "published_at": None,
                "collected_by": "web_search_sub_agent",
                "freshness_score": 0.8,
                "relevance_score": 0.9,
                "answer_coverage_score": 0.8,
                "source_confidence_score": 0.7,
                "reliability_score": 0.8,
                "score_reason": None,
                "used_in_final_report": False,
            }
        },
        "conflicts": {},
        "entity_index": {
            "question_ids": ["Q1"],
            "evidence_ids_by_question_id": {"Q1": ["E1"]},
            "conflict_ids_by_question_id": {"Q1": []},
            "search_task_ids_by_question_id": {"Q1": []},
            "used_evidence_ids": [],
        },
        "evidence_sufficiency_result": {
            "sufficient": True,
            "overall_score": 0.9,
            "threshold": 0.75,
            "high_priority_all_met": True,
            "question_status": {},
            "insufficient_question_ids": [],
            "degradation_recommended": False,
            "degradation_reason": None,
        },
        "executed_nodes": [],
    }
    state.update(overrides)
    return state


class ResearchReportNodesTest(unittest.TestCase):
    """验证报告生成消费 Review，并校验引用。"""

    def test_generate_consumes_revision_suggestions_and_filters_invalid_ids(self) -> None:
        captured_prompts: list[str] = []

        class FakeModel:
            def with_structured_output(self, schema):  # noqa: ANN001
                return self

            def invoke(self, messages):  # noqa: ANN001
                captured_prompts.append(messages[1]["content"])
                return ResearchReportOutput(
                    report_markdown=(
                        "# 报告\n\n结论引用 [E1]。\n\n## 不确定性与边界\n\n- 样本有限"
                    ),
                    cited_evidence_ids=["E1", "E999"],
                    limitations=["样本有限"],
                )

        state = _base_state(
            review_result={
                "passed": False,
                "report_score": 0.4,
                "source_coverage_score": 0.4,
                "citation_completeness_score": 0.2,
                "groundedness_score": 0.5,
                "boundary_score": 0.5,
                "over_inference_risk": 0.4,
                "revision_suggestions": ["请补充 E1 引用并写清边界"],
            }
        )

        with patch(
            "src.workflow.report_nodes.build_chat_model",
            return_value=FakeModel(),
        ):
            result = generate_research_report(state)

        self.assertIn("请补充 E1 引用并写清边界", captured_prompts[0])
        self.assertEqual(result["review_revision_count"], 1)
        self.assertEqual(result["entity_index"]["used_evidence_ids"], ["E1"])
        self.assertTrue(result["evidence_items"]["E1"]["used_in_final_report"])
        self.assertIn("已剔除不存在的证据引用：E999", result["report_draft"])

    def test_review_degraded_passes_without_llm(self) -> None:
        with patch("src.workflow.report_nodes.build_chat_model") as mock_build:
            result = review_research_report(
                _base_state(degraded=True, report_draft="# 降级报告")
            )
            mock_build.assert_not_called()

        self.assertTrue(result["review_result"]["passed"])
        self.assertEqual(result["review_result"]["revision_suggestions"], [])

    def test_review_uses_llm_and_enforces_pass_score(self) -> None:
        class FakeModel:
            def with_structured_output(self, schema):  # noqa: ANN001
                return self

            def invoke(self, messages):  # noqa: ANN001
                return ResearchReportReviewOutput(
                    passed=True,
                    report_score=0.5,
                    source_coverage_score=0.5,
                    citation_completeness_score=0.5,
                    groundedness_score=0.5,
                    boundary_score=0.5,
                    over_inference_risk=0.2,
                    revision_suggestions=["补充引用"],
                )

        mock_config = MagicMock()
        mock_config.review_pass_score = 0.75

        with (
            patch(
                "src.workflow.report_nodes.build_chat_model",
                return_value=FakeModel(),
            ),
            patch(
                "src.workflow.report_nodes.get_workflow_config",
                return_value=mock_config,
            ),
        ):
            result = review_research_report(
                _base_state(report_draft="# 报告\n引用不足")
            )

        self.assertFalse(result["review_result"]["passed"])
        self.assertEqual(
            result["review_result"]["revision_suggestions"],
            ["补充引用"],
        )


if __name__ == "__main__":
    unittest.main()
