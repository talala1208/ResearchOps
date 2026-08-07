"""证据充足性判断测试。"""

from __future__ import annotations

import unittest

from src.workflow.evidence_nodes import check_evidence_sufficiency


class EvidenceSufficiencyTest(unittest.TestCase):
    """验证整体加权分达到阈值时可以结束检索。"""

    def test_overall_score_over_threshold_is_sufficient_even_if_high_not_strictly_met(self) -> None:
        """high 子问题未完全满足不应在总分达标时强制判不足。"""

        state = {
            "sub_questions": {
                "Q1": {
                    "question_id": "Q1",
                    "question": "核心问题",
                    "priority": "high",
                    "required_source_types": ["official_docs"],
                },
                "Q2": {
                    "question_id": "Q2",
                    "question": "辅助问题",
                    "priority": "medium",
                    "required_source_types": ["blog"],
                },
            },
            "minimum_evidence_standard": {
                "Q1": {
                    "question_id": "Q1",
                    "min_total_evidence": 1,
                    "min_high_quality_sources": 1,
                    "must_include_source_types": ["official_docs", "github"],
                    "allow_degraded_answer": True,
                },
                "Q2": {
                    "question_id": "Q2",
                    "min_total_evidence": 1,
                    "min_high_quality_sources": 0,
                    "must_include_source_types": ["blog"],
                    "allow_degraded_answer": True,
                },
            },
            "evidence_matrix": {
                "Q1": {
                    "question_id": "Q1",
                    "covered_source_types": ["official_docs"],
                    "evidence_ids": ["E1"],
                    "high_quality_evidence_ids": ["E1"],
                },
                "Q2": {
                    "question_id": "Q2",
                    "covered_source_types": ["blog"],
                    "evidence_ids": ["E2"],
                    "high_quality_evidence_ids": [],
                },
            },
        }

        result = check_evidence_sufficiency(state)
        sufficiency = result["evidence_sufficiency_result"]

        self.assertNotIn("evidence_sufficient", result)
        self.assertNotIn("question_evidence_status", result)
        self.assertGreaterEqual(sufficiency["overall_score"], 0.75)
        self.assertTrue(sufficiency["sufficient"])
        self.assertIsNone(result["degradation_reason"])
        self.assertFalse(sufficiency["high_priority_all_met"])


if __name__ == "__main__":
    unittest.main()
