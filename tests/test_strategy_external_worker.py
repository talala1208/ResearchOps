"""策略迭代外部 worker 测试。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.workflow import evidence_nodes


class StrategyExternalWorkerTest(unittest.TestCase):
    """验证 Claude / Codex 只在策略迭代节点中按开关调用。"""

    def _state(self) -> dict:
        """构造最小策略迭代 state。"""

        return {
            "sub_questions": {
                "Q1": {
                    "question_id": "Q1",
                    "question": "研究 LangSmith 评估能力",
                    "priority": "high",
                    "required_source_types": ["official_docs"],
                }
            },
            "insufficient_question_ids": ["Q1"],
            "question_evidence_status": {
                "Q1": {
                    "question_id": "Q1",
                    "priority": "high",
                    "priority_weight": 3,
                    "required_evidence_count": 2,
                    "collected_evidence_count": 0,
                    "high_quality_evidence_count": 0,
                    "required_source_types": ["official_docs"],
                    "covered_source_types": [],
                    "minimum_standard_met": False,
                    "weighted_score": 0.0,
                    "missing_reason": "缺少官方文档",
                }
            },
            "search_tasks": [],
            "degradation_reason": "证据不足",
        }

    def test_strategy_iteration_does_not_call_worker_by_default(self) -> None:
        """默认不自动激活外部 worker。"""

        with patch.dict("os.environ", {"ENABLE_STRATEGY_EXTERNAL_WORKER": "false"}):
            result = evidence_nodes.strategy_iteration(self._state())

        self.assertNotIn("external_worker_result", result["search_iteration_context"])

    def test_strategy_iteration_can_call_codex_worker_when_enabled(self) -> None:
        """开启后在 strategy_iteration 调用 Codex worker。"""

        worker_payload = {
            "worker": "codex",
            "enabled": True,
            "ok": True,
            "text": "优先搜索官方 docs 和 evaluation 页面。",
            "error": None,
            "session_id": "S1",
        }
        with (
            patch.dict(
                "os.environ",
                {
                    "ENABLE_STRATEGY_EXTERNAL_WORKER": "true",
                    "STRATEGY_EXTERNAL_WORKER": "codex",
                },
            ),
            patch.object(
                evidence_nodes,
                "_call_strategy_external_worker",
                return_value=worker_payload,
            ) as worker_mock,
        ):
            result = evidence_nodes.strategy_iteration(self._state())

        worker_mock.assert_called_once()
        self.assertEqual(
            result["search_iteration_context"]["external_worker_result"]["worker"],
            "codex",
        )


if __name__ == "__main__":
    unittest.main()
