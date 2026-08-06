"""证据质量候选分融合测试。"""

from __future__ import annotations

import unittest

from src.workflow.evidence_nodes import _score_result


class EvidenceQualityScoreTest(unittest.TestCase):
    """验证 evaluate_evidence_quality 会优先使用 Web Search 候选分。"""

    def test_score_result_uses_web_candidate_scores(self) -> None:
        """Web Search 已给出的候选分应进入最终证据治理评分。"""

        scores = _score_result(
            {
                "source_type": "blog",
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
        self.assertAlmostEqual(scores["reliability_score"], 0.765)

    def test_score_result_keeps_rule_based_default_for_non_web_sources(self) -> None:
        """本地文档和结构化数据没有候选分时仍使用规则分。"""

        scores = _score_result(
            {
                "source_type": "local_document",
                "published_at": None,
                "snippet": "本地经验证据",
            }
        )

        self.assertEqual(scores["source_confidence_score"], 0.7)
        self.assertEqual(scores["relevance_score"], 0.75)
        self.assertEqual(scores["answer_coverage_score"], 0.75)


if __name__ == "__main__":
    unittest.main()
