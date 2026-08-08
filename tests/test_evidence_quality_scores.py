"""证据质量候选分融合与证据写入测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.workflow.evidence_nodes import (
    _score_result,
    _stable_unique_results,
    evaluate_evidence_quality,
    sanitize_and_cluster,
)
from src.workflow.report_nodes import _evidence_display_text


class EvidenceQualityScoreTest(unittest.TestCase):
    """验证证据评分、落盘与清洗聚类。"""

    def test_score_result_uses_web_candidate_scores(self) -> None:
        """Serp 桶保留四维分，不持久化 authority_score。"""

        scores = _score_result(
            {
                "source_type": "blog",
                "source_name": "serp",
                "published_at": "2026-01-01",
                "snippet": "候选证据",
                "relevance_score": 0.9,
                "answer_coverage_score": 0.8,
                "source_confidence_score": 0.7,
                "freshness_score": 0.6,
            }
        )

        self.assertEqual(scores["relevance_score"], 0.9)
        self.assertEqual(scores["answer_coverage_score"], 0.8)
        self.assertEqual(scores["source_confidence_score"], 0.7)
        self.assertEqual(scores["freshness_score"], 0.6)
        self.assertEqual(scores["score_bucket"], "serp")
        self.assertNotIn("authority_score", scores)
        # authority(blog=0.65)*0.2 + 0.7*0.15 + 0.6*0.15 + 0.9*0.3 + 0.8*0.2
        self.assertAlmostEqual(scores["reliability_score"], 0.755)

    def test_score_result_keeps_rule_based_default_for_non_web_sources(self) -> None:
        """本地文档没有候选分时仍使用规则分。"""

        scores = _score_result(
            {
                "source_type": "local_document",
                "source_name": "local_markdown",
                "published_at": None,
                "snippet": "本地经验证据",
            }
        )

        self.assertEqual(scores["source_confidence_score"], 0.7)
        self.assertEqual(scores["relevance_score"], 0.75)
        self.assertEqual(scores["answer_coverage_score"], 0.75)
        self.assertEqual(scores["score_bucket"], "other")
        self.assertNotIn("authority_score", scores)

    def test_context7_uses_authority_weighted_bucket(self) -> None:
        """Context7 分桶以表权威加权，不持久化 authority_score。"""

        scores = _score_result(
            {
                "source_type": "official_docs",
                "source_name": "context7",
                "published_at": None,
                "snippet": "",
                "docs_result": "docs",
                "source_confidence_score": 0.95,
                "relevance_score": 0.5,
                "answer_coverage_score": 0.5,
                "freshness_score": 0.7,
            }
        )

        self.assertEqual(scores["score_bucket"], "context7")
        self.assertNotIn("authority_score", scores)
        # 0.95*0.35 + 0.95*0.2 + 0.7*0.1 + 0.5*0.2 + 0.5*0.15
        self.assertAlmostEqual(scores["reliability_score"], 0.7675)

    def test_stable_unique_results_dedupes_by_url(self) -> None:
        """同 URL 去重时保留含 body 的更完整候选。"""

        unique = _stable_unique_results(
            [
                {
                    "url_or_path": "https://example.com/a",
                    "title": "A1",
                    "snippet": "short",
                    "relevance_score": 0.9,
                },
                {
                    "url_or_path": "https://example.com/a/",
                    "title": "A2",
                    "snippet": "short",
                    "body": "full page body",
                    "relevance_score": 0.5,
                },
            ]
        )

        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0]["title"], "A2")
        self.assertEqual(unique[0]["body"], "full page body")

    def test_sanitize_and_cluster_skips_sanitized_results(self) -> None:
        """清洗聚类直接产出 evidence_clusters，不写 sanitized_results。"""

        state = {
            "web_search_results": [
                {
                    "question_id": "Q1",
                    "url_or_path": "https://example.com/a",
                    "title": "A",
                    "snippet": "s",
                }
            ],
            "local_document_results": [],
            "executed_nodes": [],
        }
        result = sanitize_and_cluster(state)
        self.assertNotIn("sanitized_results", result)
        self.assertEqual(result["web_search_results"], [])
        self.assertEqual(len(result["evidence_clusters"]), 1)
        self.assertEqual(result["evidence_clusters"][0]["question_id"], "Q1")
        self.assertEqual(
            result["evidence_clusters"][0]["candidates"][0]["trust_boundary"],
            "untrusted_tool_output",
        )

    def test_evaluate_evidence_quality_persists_long_text(self) -> None:
        """长文落盘，EvidenceItem 只保留 content_path 与短 snippet。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {
                "sub_questions": {
                    "Q1": {
                        "question_id": "Q1",
                        "question": "文档与正文",
                        "priority": "high",
                        "required_source_types": ["official_docs", "blog"],
                    }
                },
                "entity_index": {
                    "question_ids": ["Q1"],
                    "evidence_ids_by_question_id": {},
                    "conflict_ids_by_question_id": {},
                    "search_task_ids_by_question_id": {},
                    "used_evidence_ids": [],
                },
                "evidence_clusters": [
                    {
                        "cluster_id": "CL_Q1",
                        "question_id": "Q1",
                        "candidate_count": 2,
                        "candidates": [
                            {
                                "source_type": "official_docs",
                                "source_name": "context7",
                                "url_or_path": "/demo/lib",
                                "title": "Context7 /demo/lib",
                                "snippet": "",
                                "docs_result": "official long docs " * 40,
                                "resolve_result": "resolved",
                                "library_id": "/demo/lib",
                                "collected_by": "web_search_subagent",
                                "published_at": None,
                                "score_bucket": "context7",
                                "scored_by": "context7_authority",
                                "source_confidence_score": 0.95,
                                "relevance_score": 0.5,
                                "answer_coverage_score": 0.5,
                                "freshness_score": 0.7,
                                "score_reason": "context7",
                            },
                            {
                                "source_type": "blog",
                                "source_name": "serp",
                                "url_or_path": "https://example.com/page",
                                "title": "Serp Page",
                                "snippet": "short snippet",
                                "body": "full page body text " * 30,
                                "collected_by": "web_search_subagent",
                                "published_at": None,
                                "score_bucket": "serp",
                                "scored_by": "serp_llm",
                                "source_confidence_score": 0.7,
                                "relevance_score": 0.8,
                                "answer_coverage_score": 0.7,
                                "freshness_score": 0.6,
                                "score_reason": "serp",
                            },
                        ],
                    }
                ],
                "executed_nodes": [],
            }
            with patch(
                "src.artifacts.evidence_content.get_project_root",
                return_value=root,
            ):
                result = evaluate_evidence_quality(state)

            items = list(result["evidence_items"].values())
            context7 = next(item for item in items if item["source_name"] == "context7")
            serp = next(item for item in items if item["source_name"] == "serp")

            self.assertNotIn("docs_result", context7)
            self.assertNotIn("body", serp)
            self.assertNotIn("authority_score", context7)
            self.assertTrue(context7["content_path"].endswith("E1.txt"))
            self.assertTrue(serp["content_path"].endswith("E2.txt"))
            self.assertLessEqual(len(context7["snippet"]), 520)
            self.assertTrue((root / context7["content_path"]).is_file())
            self.assertIn(
                "official long docs",
                (root / context7["content_path"]).read_text(encoding="utf-8"),
            )

    def test_evidence_display_text_reads_content_path(self) -> None:
        """报告展示优先读取 content_path。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            relative = Path("outputs") / "evidence" / "E9.txt"
            absolute = root / relative
            absolute.parent.mkdir(parents=True, exist_ok=True)
            absolute.write_text("persisted body", encoding="utf-8")
            with patch(
                "src.artifacts.evidence_content.get_project_root",
                return_value=root,
            ):
                self.assertEqual(
                    _evidence_display_text(
                        {
                            "snippet": "short",
                            "content_path": relative.as_posix(),
                        }
                    ),
                    "persisted body",
                )


if __name__ == "__main__":
    unittest.main()
