"""研究报告 LLM 节点离线 mock 测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.llm.structured_outputs import ResearchReportOutput
from src.workflow import report_nodes
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


class ResearchReportEvidenceCatalogTest(unittest.TestCase):
    """验证报告证据分层供给。"""

    def test_catalog_includes_short_dir_and_selected_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            long_path = Path(tmp) / "E2.txt"
            long_body = ("正式产品文档正文。" * 80).strip()
            long_path.write_text(long_body, encoding="utf-8")
            state = _base_state(
                evidence_matrix={
                    "Q1": {
                        "question_id": "Q1",
                        "evidence_ids": ["E1", "E2", "E3"],
                        "high_quality_evidence_ids": ["E2"],
                        "covered_source_types": ["blog", "official_docs"],
                        "conflict_ids": [],
                    }
                },
                evidence_items={
                    "E1": {
                        "evidence_id": "E1",
                        "question_id": "Q1",
                        "source_type": "blog",
                        "source_name": "serpapi",
                        "url_or_path": "https://example.com/e1",
                        "title": "短摘要证据",
                        "snippet": "只有 snippet",
                        "reliability_score": 0.9,
                        "used_in_final_report": False,
                    },
                    "E2": {
                        "evidence_id": "E2",
                        "question_id": "Q1",
                        "source_type": "official_docs",
                        "source_name": "context7",
                        "url_or_path": "/docs/e2",
                        "title": "长文高分",
                        "snippet": "短摘要",
                        "content_path": str(long_path),
                        "reliability_score": 0.95,
                        "used_in_final_report": False,
                    },
                    "E3": {
                        "evidence_id": "E3",
                        "question_id": "Q1",
                        "source_type": "blog",
                        "source_name": "tavily",
                        "url_or_path": "https://example.com/e3",
                        "title": "长文低分",
                        "snippet": "另一摘要",
                        "content_path": str(long_path),
                        "reliability_score": 0.5,
                        "used_in_final_report": False,
                    },
                },
            )
            with patch.dict(
                "os.environ",
                {
                    "REPORT_EVIDENCE_BODIES_PER_QUESTION": "1",
                    "REPORT_EVIDENCE_BODY_MAX_CHARS": "800",
                    "REPORT_EVIDENCE_BODIES_TOTAL_CHARS": "20000",
                    "REPORT_EVIDENCE_SNIPPET_MAX_CHARS": "200",
                },
            ):
                catalog = report_nodes._build_evidence_summary(state)

        self.assertIn("## 证据短目录", catalog)
        self.assertIn("[E1] 短摘要证据", catalog)
        self.assertIn("[E2] 长文高分", catalog)
        self.assertIn("## 精选证据正文", catalog)
        self.assertIn("[E2]", catalog)
        self.assertIn("正式产品文档正文", catalog)
        # 每题只精选 1 条，应选高分 E2 而非 E3
        self.assertNotIn("长文低分", catalog.split("## 精选证据正文", 1)[1])

    def test_total_budget_caps_selected_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for index in range(1, 4):
                path = Path(tmp) / f"E{index}.txt"
                path.write_text(("正文块ABCDEFGH。" * 100).strip(), encoding="utf-8")
                paths.append(path)
            state = _base_state(
                sub_questions={
                    "Q1": {
                        "question_id": "Q1",
                        "question": "题1",
                        "priority": "high",
                        "required_source_types": ["blog"],
                    },
                    "Q2": {
                        "question_id": "Q2",
                        "question": "题2",
                        "priority": "high",
                        "required_source_types": ["blog"],
                    },
                },
                evidence_matrix={
                    "Q1": {
                        "question_id": "Q1",
                        "evidence_ids": ["E1"],
                        "high_quality_evidence_ids": ["E1"],
                        "covered_source_types": ["blog"],
                        "conflict_ids": [],
                    },
                    "Q2": {
                        "question_id": "Q2",
                        "evidence_ids": ["E2", "E3"],
                        "high_quality_evidence_ids": ["E2"],
                        "covered_source_types": ["blog"],
                        "conflict_ids": [],
                    },
                },
                evidence_items={
                    "E1": {
                        "evidence_id": "E1",
                        "question_id": "Q1",
                        "source_type": "blog",
                        "source_name": "serpapi",
                        "url_or_path": "https://example.com/e1",
                        "title": "E1",
                        "snippet": "s1",
                        "content_path": str(paths[0]),
                        "reliability_score": 0.99,
                        "used_in_final_report": False,
                    },
                    "E2": {
                        "evidence_id": "E2",
                        "question_id": "Q2",
                        "source_type": "blog",
                        "source_name": "serpapi",
                        "url_or_path": "https://example.com/e2",
                        "title": "E2",
                        "snippet": "s2",
                        "content_path": str(paths[1]),
                        "reliability_score": 0.8,
                        "used_in_final_report": False,
                    },
                    "E3": {
                        "evidence_id": "E3",
                        "question_id": "Q2",
                        "source_type": "blog",
                        "source_name": "serpapi",
                        "url_or_path": "https://example.com/e3",
                        "title": "E3",
                        "snippet": "s3",
                        "content_path": str(paths[2]),
                        "reliability_score": 0.7,
                        "used_in_final_report": False,
                    },
                },
            )
            selected = report_nodes._select_evidence_bodies(
                state,
                per_question=2,
                body_max_chars=600,
                total_max_chars=900,
            )
        self.assertGreaterEqual(len(selected), 1)
        self.assertLessEqual(len(selected), 2)
        self.assertEqual(selected[0][1], "E1")
        self.assertLessEqual(sum(len(body) for *_rest, body in selected), 900)


class ResearchReportNodesTest(unittest.TestCase):
    """验证报告生成消费 Review，并校验引用。"""

    def test_coerce_maps_report_aliases(self) -> None:
        from src.workflow.report_nodes import _coerce_research_report_dict

        coerced = _coerce_research_report_dict(
            {
                "markdown": "# 报告\n引用 [E1]",
                "citations": ["E1"],
                "limits": ["样本有限"],
            }
        )
        parsed = ResearchReportOutput.model_validate(coerced)
        self.assertEqual(parsed.report_markdown, "# 报告\n引用 [E1]")
        self.assertEqual(parsed.cited_evidence_ids, ["E1"])
        self.assertEqual(parsed.limitations, ["样本有限"])

    def test_generate_consumes_revision_suggestions_and_filters_invalid_ids(self) -> None:
        captured_prompts: list[str] = []

        class FakeResponse:
            content = {
                "report_markdown": (
                    "# 报告\n\n结论引用 [E1]。\n\n## 不确定性与边界\n\n- 样本有限"
                ),
                "cited_evidence_ids": ["E1", "E999"],
                "limitations": ["样本有限"],
            }

        class FakeModel:
            def bind(self, **kwargs):  # noqa: ANN003
                return self

            def invoke(self, messages):  # noqa: ANN001
                captured_prompts.append(messages[1][1])
                return FakeResponse()

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
        self.assertIn("## 证据短目录", captured_prompts[0])
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
        class FakeResponse:
            content = {
                "passed": True,
                "report_score": 0.5,
                "source_coverage_score": 0.5,
                "citation_completeness_score": 0.5,
                "groundedness_score": 0.5,
                "boundary_score": 0.5,
                "over_inference_risk": 0.2,
                "revision_suggestions": ["补充引用"],
            }

        class FakeModel:
            def bind(self, **kwargs):  # noqa: ANN003
                return self

            def invoke(self, messages):  # noqa: ANN001
                captured_prompts.append(messages[1][1])
                return FakeResponse()

        mock_config = MagicMock()
        mock_config.review_pass_score = 0.75
        captured_prompts: list[str] = []

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
        self.assertIn("比较两款 AI 编程助手", captured_prompts[0])
        self.assertIn("用户最初的研究问题", captured_prompts[0])


class CitedEvidenceAppendixTest(unittest.TestCase):
    """验证报告末尾引用证据附录由代码生成。"""

    def test_appendix_splits_web_and_local_with_web_links(self) -> None:
        state = _base_state(
            evidence_items={
                "E1": {
                    "evidence_id": "E1",
                    "question_id": "Q1",
                    "source_type": "blog",
                    "source_name": "serpapi",
                    "url_or_path": "https://example.com/e1",
                    "title": "Web 能力对比",
                    "snippet": "snippet",
                    "published_at": None,
                    "collected_by": "web_search_sub_agent",
                    "freshness_score": 0.8,
                    "relevance_score": 0.9,
                    "answer_coverage_score": 0.8,
                    "source_confidence_score": 0.7,
                    "reliability_score": 0.8,
                    "score_reason": None,
                    "used_in_final_report": True,
                },
                "E2": {
                    "evidence_id": "E2",
                    "question_id": "Q1",
                    "source_type": "local_document",
                    "source_name": "local_markdown_documents",
                    "url_or_path": "/tmp/docs/note.md",
                    "title": "本地笔记",
                    "snippet": "local",
                    "published_at": None,
                    "collected_by": "local_document_search_tool",
                    "freshness_score": 0.5,
                    "relevance_score": 0.7,
                    "answer_coverage_score": 0.6,
                    "source_confidence_score": 0.6,
                    "reliability_score": 0.6,
                    "score_reason": None,
                    "used_in_final_report": True,
                },
                "E10": {
                    "evidence_id": "E10",
                    "question_id": "Q1",
                    "source_type": "official_docs",
                    "source_name": "context7",
                    "url_or_path": "/websites/cursor",
                    "title": "Context7 文档",
                    "snippet": "docs",
                    "published_at": None,
                    "collected_by": "web_search_sub_agent",
                    "freshness_score": 0.5,
                    "relevance_score": 0.5,
                    "answer_coverage_score": 0.5,
                    "source_confidence_score": 0.9,
                    "reliability_score": 0.7,
                    "score_reason": None,
                    "used_in_final_report": True,
                },
            },
            entity_index={
                "question_ids": ["Q1"],
                "evidence_ids_by_question_id": {"Q1": ["E1", "E2", "E10"]},
                "conflict_ids_by_question_id": {"Q1": []},
                "search_task_ids_by_question_id": {"Q1": []},
                "used_evidence_ids": ["E10", "E2", "E1"],
            },
        )

        appendix = report_nodes.build_cited_evidence_appendix(state)
        self.assertIn("## 引用证据", appendix)
        self.assertIn("### Web 证据", appendix)
        self.assertIn("### Local 证据", appendix)
        self.assertIn(
            "- [E1][Web 能力对比](https://example.com/e1)",
            appendix,
        )
        self.assertIn("- [E10] Context7 文档", appendix)
        self.assertIn("- [E2] 本地笔记", appendix)
        self.assertLess(appendix.index("[E1]"), appendix.index("[E10]"))

        final = report_nodes.append_cited_evidence_appendix("# 报告正文\n", state)
        self.assertTrue(final.startswith("# 报告正文"))
        self.assertIn("## 引用证据", final)
        once = report_nodes.append_cited_evidence_appendix(final, state)
        self.assertEqual(once.count("## 引用证据"), 1)


if __name__ == "__main__":
    unittest.main()
