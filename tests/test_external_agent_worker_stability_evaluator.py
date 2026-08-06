"""外部 Agent Worker 稳定性 evaluator 测试。"""

from __future__ import annotations

import json
import unittest

from src.evaluators.external_agent_worker_stability import (
    evaluate_external_agent_worker_stability_from_outputs,
    external_agent_worker_stability_evaluator,
)


class ExternalAgentWorkerStabilityEvaluatorTest(unittest.TestCase):
    """验证 External Agent Worker Stability Evaluator。"""

    def test_evaluate_codex_success_from_strategy_context(self) -> None:
        """strategy_iteration 中的 external_worker_result 可被识别。"""

        result = evaluate_external_agent_worker_stability_from_outputs(
            {
                "search_iteration_context": {
                    "external_worker_result": {
                        "worker": "codex",
                        "enabled": True,
                        "ok": True,
                        "text": "建议补充官方文档和 GitHub 证据。",
                        "error": None,
                        "session_id": "session-1",
                    }
                }
            }
        )

        self.assertEqual(result.overall_stability_score, 1.0)
        self.assertTrue(result.codex.ok)
        self.assertEqual(result.codex.failure_type, "none")
        self.assertTrue(result.codex.session_id_present)

    def test_classifies_claude_missing_command(self) -> None:
        """Claude 命令缺失应被识别。"""

        result = evaluate_external_agent_worker_stability_from_outputs(
            {
                "external_worker_outputs": [
                    {
                        "worker": "claude_code",
                        "enabled": True,
                        "ok": False,
                        "text": "",
                        "error": "找不到 Claude Code 命令：claude",
                    }
                ]
            }
        )

        self.assertFalse(result.claude_code.ok)
        self.assertEqual(result.claude_code.failure_type, "missing_command")
        self.assertIn("claude_code", result.blocking_issues[0])

    def test_classifies_codex_missing_dependency_from_tool_output(self) -> None:
        """tool_outputs 中的 Codex 结果也应能识别。"""

        result = evaluate_external_agent_worker_stability_from_outputs(
            {
                "tool_outputs": [
                    {
                        "tool_name": "call_codex_worker",
                        "ok": True,
                        "output": json.dumps(
                            {
                                "worker": "codex",
                                "enabled": True,
                                "ok": False,
                                "text": "",
                                "error": "缺少 acp Python SDK：No module named 'acp'",
                                "session_id": None,
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            }
        )

        self.assertFalse(result.codex.ok)
        self.assertEqual(result.codex.failure_type, "missing_dependency")

    def test_langsmith_feedback_shape(self) -> None:
        """evaluator 应返回 LangSmith feedback 格式。"""

        feedback = external_agent_worker_stability_evaluator(
            inputs={"question": "测试外部 worker 稳定性"},
            reference_outputs={},
            outputs={"external_worker_outputs": []},
        )

        self.assertEqual(feedback["key"], "external_agent_worker_stability")
        self.assertIn("score", feedback)
        self.assertIn("comment", feedback)
        self.assertEqual(json.loads(feedback["comment"])["question"], "测试外部 worker 稳定性")


if __name__ == "__main__":
    unittest.main()
