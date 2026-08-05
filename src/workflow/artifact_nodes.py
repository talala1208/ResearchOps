"""本地运行产物保存节点。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.settings import get_project_root
from src.schemas.state import ResearchState
from src.workflow.node_utils import record_node


def _build_run_id() -> str:
    """生成本地运行 ID。"""

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _source_contribution(state: ResearchState) -> dict[str, int]:
    """统计进入最终报告的证据来源贡献。"""

    contribution: dict[str, int] = {}
    evidence_items = state.get("evidence_items", {})
    used_evidence_ids = state.get("entity_index", {}).get("used_evidence_ids", [])
    for evidence_id in used_evidence_ids:
        evidence = evidence_items[evidence_id]
        source_type = evidence["source_type"]
        contribution[source_type] = contribution.get(source_type, 0) + 1
    return contribution


def persist_outputs(state: ResearchState) -> dict[str, Any]:
    """保存最终报告和运行指标到 `outputs/`。

    当前不在主 Graph 内生成实际运行 Mermaid / PNG；该能力后续由后台静默
    产物逻辑接入。
    """

    executed_nodes_before = state.get("executed_nodes", [])
    executed_nodes = [*executed_nodes_before, "persist_outputs"]
    project_root = get_project_root()
    run_id = _build_run_id()
    reports_dir = project_root / "outputs" / "reports"
    runs_dir = project_root / "outputs" / "runs"
    reports_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)

    report_path = reports_dir / f"{run_id}.md"
    metrics_path = runs_dir / f"{run_id}_metrics.json"

    final_report = state["final_report"]
    report_path.write_text(final_report, encoding="utf-8")

    source_contribution = _source_contribution(state)
    web_hitl_trigger_count_by_source = {
        "web_search": len(state.get("web_hitl_decisions", [])),
    }
    metrics = {
        "run_id": run_id,
        "total_steps": len(executed_nodes),
        "executed_nodes": executed_nodes,
        "search_steps": state.get("search_steps", 0),
        "max_search_steps": state.get("max_search_steps", 3),
        "review_revision_count": state.get("review_revision_count", 0),
        "max_review_revisions": state.get("max_review_revisions", 1),
        "safety_revision_count": state.get("safety_revision_count", 0),
        "max_safety_revisions": state.get("max_safety_revisions", 1),
        "total_latency_ms": 0,
        "total_tokens": 0,
        "evidence_count": len(state.get("evidence_items", {})),
        "final_citation_count": len(state.get("entity_index", {}).get("used_evidence_ids", [])),
        "conflict_count": len(state.get("conflicts", {})),
        "degraded": state.get("degraded", False),
        "degradation_reason": state.get("degradation_reason"),
        "review_score": state.get("review_result", {}).get("report_score"),
        "safety_risk_level": state.get("safety_review_result", {}).get("safety_risk_level"),
        "source_contribution": source_contribution,
        "web_hitl_trigger_count_by_source": web_hitl_trigger_count_by_source,
    }
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "executed_nodes": executed_nodes,
        "final_report_path": str(report_path),
        "executed_mermaid": None,
        "executed_mermaid_png_path": None,
        "output_artifacts": {
            "final_report": str(report_path),
            "metrics": str(metrics_path),
        },
        "evaluation_metrics": metrics,
    }
