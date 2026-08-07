"""研究规划节点模式路由测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.llm.structured_outputs import (
    ExpectedEvidenceOutput,
    MinimumEvidenceStandardOutput,
    ResearchPlanWithSearchTasksOutput,
    SearchTaskOutput,
    SubQuestionOutput,
)
from src.workflow import planning_nodes


def _sample_plan(*, duplicate_standard: bool = False) -> ResearchPlanWithSearchTasksOutput:
    sub_questions = [
        SubQuestionOutput(
            question_id="Q1",
            question="能力",
            priority="high",
            required_source_types=["blog"],
        ),
        SubQuestionOutput(
            question_id="Q2",
            question="定价",
            priority="medium",
            required_source_types=["pricing_page"],
        ),
        SubQuestionOutput(
            question_id="Q3",
            question="生态",
            priority="low",
            required_source_types=["github"],
        ),
        SubQuestionOutput(
            question_id="Q4",
            question="风险",
            priority="medium",
            required_source_types=["blog"],
        ),
    ]
    expected = [
        ExpectedEvidenceOutput(
            question_id=qid,
            evidence_description=f"证据 {qid}",
            minimum_count=1,
            required_source_types=["blog"],
            required_authority_level="medium",
        )
        for qid in ["Q1", "Q2", "Q3", "Q4"]
    ]
    standards = [
        MinimumEvidenceStandardOutput(
            question_id=qid,
            min_total_evidence=1,
            min_high_quality_sources=0,
            must_include_source_types=["blog"],
            allow_degraded_answer=True,
        )
        for qid in ["Q1", "Q2", "Q3", "Q4"]
    ]
    if duplicate_standard:
        standards.append(
            MinimumEvidenceStandardOutput(
                question_id="Q3",
                min_total_evidence=2,
                min_high_quality_sources=1,
                must_include_source_types=["github"],
                allow_degraded_answer=False,
            )
        )
    return ResearchPlanWithSearchTasksOutput(
        research_goal="研究目标",
        sub_questions=sub_questions,
        required_source_types=["blog", "github"],
        expected_evidence=expected,
        minimum_evidence_standard=standards,
        search_tasks=[
            SearchTaskOutput(
                task_id="T1",
                question_id="Q1",
                query="能力查询",
                source_type="blog",
                search_provider="web_search",
                attempt=1,
            )
        ],
        active_question_ids_after_dispatch=["Q1"],
    )


class PlanResearchModeTest(unittest.TestCase):
    """验证 plan_research 根据 planning_mode 选择不同规划路径。"""

    def test_plan_research_uses_initial_planning_by_default(self) -> None:
        """默认走初始规划 Prompt 路径。"""

        with (
            patch.object(
                planning_nodes,
                "_run_initial_planning",
                return_value={"planning_mode": "initial", "search_tasks": []},
            ) as initial_mock,
            patch.object(planning_nodes, "_run_iteration_planning") as iteration_mock,
        ):
            result = planning_nodes.plan_research(
                {"user_query": "研究 Cursor", "executed_nodes": [], "search_steps": 0}
            )

        initial_mock.assert_called_once()
        iteration_mock.assert_not_called()
        self.assertEqual(result["planning_mode"], "initial")
        self.assertEqual(result["search_steps"], 1)
        self.assertEqual(result["executed_nodes"], ["plan_research"])

    def test_plan_research_uses_iteration_planning_when_mode_is_iteration(self) -> None:
        """迭代模式走迭代检索 Prompt 路径。"""

        with (
            patch.object(planning_nodes, "_run_initial_planning") as initial_mock,
            patch.object(
                planning_nodes,
                "_run_iteration_planning",
                return_value={"planning_mode": "iteration", "search_tasks": []},
            ) as iteration_mock,
        ):
            result = planning_nodes.plan_research(
                {"planning_mode": "iteration", "executed_nodes": [], "search_steps": 1}
            )

        initial_mock.assert_not_called()
        iteration_mock.assert_called_once()
        self.assertEqual(result["planning_mode"], "iteration")
        self.assertEqual(result["search_steps"], 2)
        self.assertEqual(result["executed_nodes"], ["plan_research"])

    def test_normalize_dedupes_duplicate_minimum_evidence_standard(self) -> None:
        """重复的 standard question_id 应去重且后写覆盖。"""

        plan = _sample_plan(duplicate_standard=True)
        self.assertEqual(len(plan.minimum_evidence_standard), 5)

        normalized = planning_nodes._normalize_research_plan(plan)
        planning_nodes._validate_research_plan(normalized)

        self.assertEqual(len(normalized.minimum_evidence_standard), 4)
        by_id = {
            item.question_id: item for item in normalized.minimum_evidence_standard
        }
        self.assertEqual(by_id["Q3"].min_total_evidence, 2)
        self.assertEqual(by_id["Q3"].must_include_source_types, ["github"])

    def test_coerce_research_plan_unwraps_nested_and_fills_defaults(self) -> None:
        """兼容 research_plan 包装、topic、字段漂移与缺 attempt。"""

        raw = {
            "research_plan": {
                "topic": "研究 Cursor 的产品能力",
                "sub_questions": [
                    {
                        "question_id": "Q1",
                        "question": "Cursor 的核心产品能力有哪些？",
                        "priority": "high",
                    },
                    {
                        "question_id": "Q2",
                        "question": "Cursor 的定价策略是什么？",
                        "priority": "high",
                    },
                ],
                "expected_evidence": [
                    {
                        "question_id": "Q1",
                        "evidence_description": "官方功能说明",
                    },
                    {
                        "question_id": "Q2",
                        "evidence_description": "定价页",
                    },
                ],
                "minimum_evidence_standard": [
                    {
                        "question_id": "Q1",
                        "must_include_source_types": ["official_docs", "blog"],
                        "minimum_count": 2,
                    },
                    {
                        "question_id": "Q2",
                        "must_include_source_types": ["pricing_page"],
                        "minimum_count": 1,
                    },
                ],
            },
            "search_tasks": [
                {
                    "task_id": "T1",
                    "question_id": "Q1",
                    "query": "Cursor features",
                    "source_type": "official_docs",
                    "search_provider": "web_search",
                },
                {
                    "task_id": "T2",
                    "question_id": "Q2",
                    "query": "Cursor pricing",
                    "source_type": "pricing_page",
                    "search_provider": "web_search",
                },
            ],
            "active_question_ids_after_dispatch": ["Q1", "Q2"],
        }

        coerced = planning_nodes._coerce_research_plan_dict(raw)
        plan = ResearchPlanWithSearchTasksOutput.model_validate(coerced)
        plan = planning_nodes._normalize_research_plan(plan)
        planning_nodes._validate_research_plan(plan)

        self.assertEqual(plan.research_goal, "研究 Cursor 的产品能力")
        self.assertIn("official_docs", plan.sub_questions[0].required_source_types)
        self.assertEqual(plan.minimum_evidence_standard[0].min_total_evidence, 2)
        self.assertTrue(plan.minimum_evidence_standard[0].allow_degraded_answer)
        self.assertEqual(plan.search_tasks[0].attempt, 1)
        self.assertIn("pricing_page", plan.required_source_types)

    def test_coerce_authority_level_aliases(self) -> None:
        """official/community 等权威别名应映射到 high/medium/low。"""

        raw = {
            "research_goal": "比较三款观测工具",
            "sub_questions": [
                {
                    "question_id": "Q1",
                    "question": "LangSmith 能力与定价？",
                    "priority": "high",
                    "required_source_types": ["official_docs"],
                },
                {
                    "question_id": "Q2",
                    "question": "社区对比如何？",
                    "priority": "medium",
                    "required_source_types": ["community"],
                },
            ],
            "required_source_types": ["official_docs", "community"],
            "expected_evidence": [
                {
                    "question_id": "Q1",
                    "evidence_description": "官方说明",
                    "minimum_count": 1,
                    "required_source_types": ["official_docs"],
                    "required_authority_level": "official",
                },
                {
                    "question_id": "Q2",
                    "evidence_description": "社区讨论",
                    "minimum_count": 1,
                    "required_source_types": ["community"],
                    "required_authority_level": "community",
                },
            ],
            "minimum_evidence_standard": [
                {
                    "question_id": "Q1",
                    "min_total_evidence": 1,
                    "min_high_quality_sources": 1,
                    "must_include_source_types": ["official_docs"],
                    "allow_degraded_answer": False,
                },
                {
                    "question_id": "Q2",
                    "min_total_evidence": 1,
                    "min_high_quality_sources": 0,
                    "must_include_source_types": ["community"],
                    "allow_degraded_answer": True,
                },
            ],
            "search_tasks": [
                {
                    "task_id": "T1",
                    "question_id": "Q1",
                    "query": "LangSmith docs",
                    "source_type": "official_docs",
                    "search_provider": "web_search",
                    "attempt": 1,
                }
            ],
            "active_question_ids_after_dispatch": ["Q1", "Q2"],
        }

        coerced = planning_nodes._coerce_research_plan_dict(raw)
        plan = ResearchPlanWithSearchTasksOutput.model_validate(coerced)
        by_id = {item.question_id: item for item in plan.expected_evidence}
        self.assertEqual(by_id["Q1"].required_authority_level, "high")
        self.assertEqual(by_id["Q2"].required_authority_level, "low")

    def test_parse_message_json_content_supports_text_blocks(self) -> None:
        """兼容 content 为 [{type,text}] 的消息块格式。"""

        payload = planning_nodes._parse_message_json_content(
            [{"type": "text", "text": '{"research_goal": "x"}'}]
        )
        self.assertEqual(payload["research_goal"], "x")


if __name__ == "__main__":
    unittest.main()
