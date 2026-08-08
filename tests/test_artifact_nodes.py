"""运行产物节点 smoke 测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.workflow import artifact_nodes


class PersistOutputsSmokeTest(unittest.TestCase):
    """验证 persist_outputs 静默保存报告、指标和实际运行 PNG。"""

    def test_persist_outputs_saves_png_without_mermaid_file(self) -> None:
        """实际运行链路只保存 PNG，不保存 mmd 文件。"""

        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            state = {
                "final_report": "# Smoke\n\n实际运行链路 PNG 保存测试。\n",
                "executed_nodes": [
                    "input_guard",
                    "plan_research",
                    "generate_research_report",
                    "safety_review",
                ],
                "entity_index": {"used_evidence_ids": []},
                "evidence_items": {},
                "conflicts": {},
                "web_hitl_decisions": [],
            }

            with (
                patch.object(
                    artifact_nodes, "get_project_root", return_value=project_root
                ),
                patch.object(
                    artifact_nodes,
                    "get_workflow_config",
                    return_value=SimpleNamespace(
                        max_search_steps=3,
                        max_review_revisions=1,
                        max_safety_revisions=1,
                    ),
                ),
                patch.object(
                    artifact_nodes, "draw_mermaid_png", return_value=b"png-bytes"
                ),
            ):
                result = artifact_nodes.persist_outputs(state)

            artifacts = result["output_artifacts"]
            report_path = Path(artifacts["final_report"])
            metrics_path = Path(artifacts["metrics"])
            png_path = Path(artifacts["executed_mermaid_png"])

            self.assertTrue(report_path.exists())
            self.assertTrue(metrics_path.exists())
            self.assertTrue(png_path.exists())
            self.assertEqual(png_path.read_bytes(), b"png-bytes")
            self.assertNotIn("executed_mermaid", artifacts)
            self.assertFalse(
                list((project_root / "outputs" / "runs").glob("*_executed.mmd"))
            )

            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            self.assertNotIn("total_steps", metrics)
            self.assertNotIn("executed_mermaid_png_path", metrics)
            self.assertNotIn("executed_mermaid_path", metrics)
            self.assertEqual(metrics["discarded_candidate_count"], 0)
            self.assertEqual(metrics["final_citation_count"], 0)
            self.assertNotIn("total_latency_ms", metrics)
            self.assertNotIn("total_tokens", metrics)
            self.assertNotIn("final_report_path", result)
            self.assertNotIn("executed_mermaid", result)
            self.assertNotIn("executed_mermaid_png_path", result)

    def test_mermaid_api_failure_retries_once(self) -> None:
        """mermaid.ink 首次失败时应重试一次并返回第二次结果。"""

        with patch.object(
            artifact_nodes,
            "draw_mermaid_png",
            side_effect=[ValueError("Status code: 404"), b"png-after-retry"],
        ) as draw:
            result = artifact_nodes._draw_mermaid_png_with_one_retry("graph TD")

        self.assertEqual(result, b"png-after-retry")
        self.assertEqual(draw.call_count, 2)
        for call in draw.call_args_list:
            self.assertEqual(call.kwargs["max_retries"], 0)

    def test_mermaid_api_second_failure_is_exposed(self) -> None:
        """mermaid.ink 连续失败时应在一次重试后显式抛错。"""

        with patch.object(
            artifact_nodes,
            "draw_mermaid_png",
            side_effect=ValueError("mermaid.ink unavailable"),
        ) as draw:
            with self.assertRaisesRegex(ValueError, "unavailable"):
                artifact_nodes._draw_mermaid_png_with_one_retry("graph TD")

        self.assertEqual(draw.call_count, 2)

    def test_run_id_is_unique_and_contains_microseconds(self) -> None:
        """连续运行应生成不同且包含微秒与随机后缀的 ID。"""

        first = artifact_nodes._build_run_id()
        second = artifact_nodes._build_run_id()

        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^\d{8}T\d{12}Z_[0-9a-f]{8}$")


class ExecutedMermaidTopologyTest(unittest.TestCase):
    """验证实际运行图还原 fan-out / fan-in / 循环与未执行路径。"""

    def test_standard_path_marks_fanout_join_and_skipped_branches(self) -> None:
        """标准路径应标出并行、汇聚，并保留未执行分支为虚线。"""

        executed = [
            "input_guard",
            "plan_research",
            "local_document_search_tool",
            "web_search_sub_agent",
            "web_search_result_ready",
            "sanitize_and_cluster",
            "evaluate_evidence_quality",
            "build_evidence_matrix",
            "check_evidence_sufficiency",
            "generate_research_report",
            "review_research_report",
            "safety_review",
            "persist_outputs",
        ]
        mermaid = artifact_nodes._build_executed_mermaid(executed)

        self.assertIn("subgraph parallel_retrieval [并行检索 fan-out / fan-in]", mermaid)
        self.assertIn("plan_research -->|并行| web_search_sub_agent", mermaid)
        self.assertIn("plan_research -->|并行| local_document_search_tool", mermaid)
        self.assertIn(
            "web_search_result_ready -->|汇聚| sanitize_and_cluster",
            mermaid,
        )
        self.assertIn(
            "local_document_search_tool -->|汇聚| sanitize_and_cluster",
            mermaid,
        )
        self.assertIn(
            "web_search_sub_agent -.->|未执行| web_search_hitl_request",
            mermaid,
        )
        self.assertIn(
            "check_evidence_sufficiency -.->|未执行| check_step_budget",
            mermaid,
        )
        self.assertIn(
            "strategy_iteration -.->|循环/未执行| plan_research",
            mermaid,
        )
        self.assertIn("class web_search_hitl_request skipped;", mermaid)
        self.assertIn("class check_step_budget skipped;", mermaid)
        self.assertIn("class plan_research planner;", mermaid)

    def test_loop_path_marks_iteration_and_visit_counts(self) -> None:
        """策略迭代与 Review 回环应标注循环，并显示节点执行次数。"""

        executed = [
            "input_guard",
            "plan_research",
            "web_search_sub_agent",
            "web_search_result_ready",
            "local_document_search_tool",
            "sanitize_and_cluster",
            "evaluate_evidence_quality",
            "build_evidence_matrix",
            "check_evidence_sufficiency",
            "check_step_budget",
            "strategy_iteration",
            "plan_research",
            "web_search_sub_agent",
            "web_search_result_ready",
            "local_document_search_tool",
            "sanitize_and_cluster",
            "evaluate_evidence_quality",
            "build_evidence_matrix",
            "check_evidence_sufficiency",
            "generate_research_report",
            "review_research_report",
            "generate_research_report",
            "review_research_report",
            "safety_review",
            "persist_outputs",
        ]
        mermaid = artifact_nodes._build_executed_mermaid(executed)

        self.assertIn('plan_research["plan_research ×2"]', mermaid)
        self.assertIn(
            'generate_research_report["generate_research_report ×2"]',
            mermaid,
        )
        self.assertIn("strategy_iteration -->|循环| plan_research", mermaid)
        self.assertIn(
            "review_research_report -->|循环| generate_research_report",
            mermaid,
        )
        self.assertIn("review_research_report --> safety_review", mermaid)

    def test_infer_taken_edges_for_input_guard_degraded_path(self) -> None:
        """Input Guard 阻断时应走降级边，而不是规划与检索。"""

        executed = [
            "input_guard",
            "prepare_degraded_report",
            "generate_research_report",
            "review_research_report",
            "safety_review",
            "persist_outputs",
        ]
        taken = artifact_nodes._infer_taken_edges(executed)

        self.assertIn(("input_guard", "prepare_degraded_report"), taken)
        self.assertNotIn(("input_guard", "plan_research"), taken)
        self.assertNotIn(("plan_research", "web_search_sub_agent"), taken)


if __name__ == "__main__":
    unittest.main()
