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

    def test_validate_search_tasks_rejects_empty_list(self) -> None:
        """search_tasks 为空时应失败。"""

        from src.llm.structured_outputs import SearchTaskPlanOutput

        with self.assertRaisesRegex(ValueError, "search_tasks 不能为空"):
            planning_nodes._validate_search_tasks(
                {"Q1": {"question_id": "Q1"}},
                SearchTaskPlanOutput(
                    search_tasks=[],
                    active_question_ids_after_dispatch=["Q1"],
                ),
            )

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

    def test_coerce_search_provider_maps_local_aliases(self) -> None:
        """local / local_* 映射到 local_document_search，其它到 web_search。"""

        self.assertEqual(
            planning_nodes._coerce_search_provider("local"),
            "local_document_search",
        )
        self.assertEqual(
            planning_nodes._coerce_search_provider("local_rag"),
            "local_document_search",
        )
        self.assertEqual(
            planning_nodes._coerce_search_provider("local_document_search"),
            "local_document_search",
        )
        self.assertEqual(
            planning_nodes._coerce_search_provider("web_search"),
            "web_search",
        )
        self.assertEqual(
            planning_nodes._coerce_search_provider("serp"),
            "web_search",
        )
        self.assertEqual(
            planning_nodes._coerce_search_provider(None),
            "web_search",
        )

    def test_coerce_search_task_plan_reuses_provider_mapping(self) -> None:
        """迭代规划 coerce 应复用 search_provider 映射并补 attempt。"""

        coerced = planning_nodes._coerce_search_task_plan_dict(
            {
                "search_tasks": [
                    {
                        "task_id": "T1",
                        "question_id": "Q1",
                        "query": "本地笔记",
                        "source_type": "local",
                        "search_provider": "local",
                    }
                ]
            },
            default_attempt=3,
        )
        task = coerced["search_tasks"][0]
        self.assertEqual(task["search_provider"], "local_document_search")
        self.assertEqual(task["attempt"], 3)
        self.assertEqual(coerced["active_question_ids_after_dispatch"], [])

    def test_planner_validation_retry_once_with_error_summary(self) -> None:
        """校验失败时应把错误摘要追加到 user prompt 并只重试一次。"""

        calls: list[str] = []

        def fake_invoke_json(*, model_role, system_prompt, user_prompt):  # noqa: ANN001
            calls.append(user_prompt)
            if len(calls) == 1:
                return {
                    "search_tasks": [
                        {
                            "task_id": "T1",
                            "question_id": "Q_MISSING",
                            "query": "bad",
                            "source_type": "blog",
                            "search_provider": "web_search",
                            "attempt": 2,
                        }
                    ],
                    "active_question_ids_after_dispatch": ["Q1"],
                }
            return {
                "search_tasks": [
                    {
                        "task_id": "T1",
                        "question_id": "Q1",
                        "query": "retry query",
                        "source_type": "blog",
                        "search_provider": "web_search",
                        "attempt": 2,
                    }
                ],
                "active_question_ids_after_dispatch": ["Q1"],
            }

        with patch.object(
            planning_nodes,
            "_invoke_planner_json",
            side_effect=fake_invoke_json,
        ):
            plan = planning_nodes._invoke_search_task_plan(
                system_prompt="sys",
                user_prompt="原始 user prompt",
                sub_questions={
                    "Q1": {
                        "question_id": "Q1",
                        "question": "能力",
                        "priority": "high",
                        "required_source_types": ["blog"],
                    }
                },
                default_attempt=2,
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], "原始 user prompt")
        self.assertIn("上次输出未通过校验", calls[1])
        self.assertIn("原始 user prompt", calls[1])
        self.assertEqual(plan.search_tasks[0].query, "retry query")

    def test_planner_validation_retry_raises_second_failure(self) -> None:
        """第二次仍失败时应抛出第二次错误。"""

        def always_bad(*, model_role, system_prompt, user_prompt):  # noqa: ANN001
            return {"search_tasks": [{"task_id": "T1"}]}

        with patch.object(
            planning_nodes,
            "_invoke_planner_json",
            side_effect=always_bad,
        ):
            with self.assertRaises(Exception):
                planning_nodes._invoke_search_task_plan(
                    system_prompt="sys",
                    user_prompt="user",
                    sub_questions={"Q1": {"question_id": "Q1"}},
                    default_attempt=1,
                )


if __name__ == "__main__":
    unittest.main()
