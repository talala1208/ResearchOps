"""本地运行产物保存节点。"""

from __future__ import annotations

from typing import Any

from src.schemas.state import ResearchState
from src.workflow.node_utils import record_node


def persist_outputs(state: ResearchState) -> dict[str, Any]:
    """本地运行产物持久化占位节点。

    当前只写 state 字段，不实际写文件。后续再接入 Markdown、Mermaid、
    PNG 和 metrics JSON 的保存逻辑。
    """

    executed_nodes = state.get("executed_nodes", [])

    return {
        **record_node(state, "persist_outputs"),
        "final_report_path": None,
        "executed_mermaid": None,
        "executed_mermaid_png_path": None,
        "output_artifacts": {},
        "evaluation_metrics": {
            "total_steps": len(executed_nodes) + 1,
            "search_steps": state.get("search_steps", 0),
            "max_search_steps": state.get("max_search_steps", 3),
            "review_revision_count": state.get("review_revision_count", 0),
            "max_review_revisions": state.get("max_review_revisions", 1),
            "safety_revision_count": state.get("safety_revision_count", 0),
            "max_safety_revisions": state.get("max_safety_revisions", 1),
            "total_latency_ms": 0,
            "total_tokens": 0,
            "evidence_count": len(state.get("evidence_items", {})),
            "final_citation_count": 0,
            "conflict_count": len(state.get("conflicts", {})),
            "degraded": state.get("degraded", False),
            "degradation_reason": state.get("degradation_reason"),
            "review_score": state.get("review_result", {}).get("report_score"),
            "safety_risk_level": state.get("safety_review_result", {}).get(
                "safety_risk_level"
            ),
            "source_contribution": {},
            "web_hitl_trigger_count_by_source": {},
        },
    }
