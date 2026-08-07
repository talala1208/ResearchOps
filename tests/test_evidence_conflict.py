"""证据准入过滤与极简语义冲突测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.workflow.evidence_nodes import (
    _has_numeric_conflict,
    _has_polarity_conflict,
    _is_admissible_candidate,
    evaluate_evidence_quality,
    sanitize_and_cluster,
)


def _base_candidate(**overrides: object) -> dict:
    candidate = {
        "question_id": "Q1",
        "source_type": "blog",
        "source_name": "serpapi",
        "url_or_path": "https://example.com/a",
        "title": "标题",
        "snippet": "摘要",
        "published_at": None,
        "collected_by": "web_search_sub_agent",
        "requires_login": False,
        "blocked_reason": None,
    }
    candidate.update(overrides)
    return candidate


class EvidenceAdmissionConflictTest(unittest.TestCase):
    """验证候选准入与语义冲突启发式。"""

    def test_is_admissible_rejects_login_blocked_placeholder_and_no_match(self) -> None:
        self.assertFalse(
            _is_admissible_candidate(_base_candidate(requires_login=True))
        )
        self.assertFalse(
            _is_admissible_candidate(_base_candidate(blocked_reason="timeout"))
        )
        self.assertFalse(
            _is_admissible_candidate(
                _base_candidate(url_or_path="placeholder://local_document/error")
            )
        )
        self.assertFalse(
            _is_admissible_candidate(
                _base_candidate(
                    structured_payload={"matched_product_count": 0},
                )
            )
        )
        self.assertTrue(_is_admissible_candidate(_base_candidate()))

    def test_sanitize_discards_inadmissible_candidates(self) -> None:
        result = sanitize_and_cluster(
            {
                "web_search_results": [
                    _base_candidate(requires_login=True),
                    _base_candidate(
                        url_or_path="https://example.com/ok",
                        title="ok",
                        snippet="可用证据",
                    ),
                ],
                "local_document_results": [
                    _base_candidate(
                        url_or_path="placeholder://structured_mock/no_match",
                        source_type="structured_mock",
                    )
                ],
                "executed_nodes": [],
            }
        )

        self.assertEqual(result["discarded_candidate_count"], 2)
        self.assertEqual(len(result["evidence_clusters"]), 1)
        self.assertEqual(result["evidence_clusters"][0]["candidate_count"], 1)

    def test_polarity_and_numeric_conflict_helpers(self) -> None:
        self.assertTrue(
            _has_polarity_conflict("该功能支持 SSO", "该功能不支持 SSO")
        )
        self.assertFalse(
            _has_polarity_conflict("该功能支持 SSO", "文档提到 SSO 集成")
        )
        self.assertTrue(
            _has_numeric_conflict("价格: 100 元", "价格: 200 元")
        )
        self.assertFalse(
            _has_numeric_conflict("价格: 100 元", "价格: 105 元")
        )

    def test_evaluate_builds_conflict_only_on_semantic_signal(self) -> None:
        state = {
            "sub_questions": {
                "Q1": {
                    "question_id": "Q1",
                    "question": "是否支持 SSO",
                    "priority": "high",
                    "required_source_types": ["blog"],
                }
            },
            "entity_index": {
                "question_ids": ["Q1"],
                "evidence_ids_by_question_id": {"Q1": []},
                "conflict_ids_by_question_id": {"Q1": []},
                "search_task_ids_by_question_id": {"Q1": []},
                "used_evidence_ids": [],
            },
            "evidence_clusters": [
                {
                    "cluster_id": "CL_Q1",
                    "question_id": "Q1",
                    "candidate_count": 2,
                    "candidates": [
                        _base_candidate(
                            title="A",
                            snippet="该产品支持 SSO",
                            url_or_path="https://example.com/a",
                            source_type="blog",
                        ),
                        _base_candidate(
                            title="B",
                            snippet="该产品不支持 SSO",
                            url_or_path="https://example.com/b",
                            source_type="official_docs",
                        ),
                    ],
                }
            ],
            "executed_nodes": [],
        }
        with patch(
            "src.workflow.evidence_nodes.get_workflow_config"
        ) as mock_config:
            mock_config.return_value.hitl_conflict_threshold = 9.0
            result = evaluate_evidence_quality(state)

        self.assertEqual(len(result["evidence_items"]), 2)
        self.assertEqual(len(result["conflicts"]), 1)
        conflict = next(iter(result["conflicts"].values()))
        self.assertIn("对立极性", conflict["conflict_summary"])
        self.assertFalse(conflict["hitl_triggered"])

    def test_multi_source_without_semantic_signal_has_no_conflict(self) -> None:
        state = {
            "sub_questions": {
                "Q1": {
                    "question_id": "Q1",
                    "question": "产品能力",
                    "priority": "medium",
                    "required_source_types": ["blog", "github"],
                }
            },
            "entity_index": {
                "question_ids": ["Q1"],
                "evidence_ids_by_question_id": {"Q1": []},
                "conflict_ids_by_question_id": {"Q1": []},
                "search_task_ids_by_question_id": {"Q1": []},
                "used_evidence_ids": [],
            },
            "evidence_clusters": [
                {
                    "cluster_id": "CL_Q1",
                    "question_id": "Q1",
                    "candidate_count": 2,
                    "candidates": [
                        _base_candidate(
                            title="博客介绍",
                            snippet="介绍产品能力概览",
                            url_or_path="https://example.com/blog",
                            source_type="blog",
                        ),
                        _base_candidate(
                            title="仓库 README",
                            snippet="开源仓库说明安装步骤",
                            url_or_path="https://github.com/example/repo",
                            source_type="github",
                        ),
                    ],
                }
            ],
            "executed_nodes": [],
        }
        with patch(
            "src.workflow.evidence_nodes.get_workflow_config"
        ) as mock_config:
            mock_config.return_value.hitl_conflict_threshold = 9.0
            result = evaluate_evidence_quality(state)

        self.assertEqual(len(result["evidence_items"]), 2)
        self.assertEqual(result["conflicts"], {})


if __name__ == "__main__":
    unittest.main()
