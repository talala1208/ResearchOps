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
            self.assertEqual(metrics["executed_mermaid_png_path"], str(png_path))
            self.assertNotIn("executed_mermaid_path", metrics)
            self.assertEqual(metrics["discarded_candidate_count"], 0)
            self.assertEqual(metrics["final_citation_count"], 0)


if __name__ == "__main__":
    unittest.main()
