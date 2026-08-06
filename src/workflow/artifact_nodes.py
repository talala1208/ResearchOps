"""本地运行产物保存节点。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.runnables.graph_mermaid import draw_mermaid_png

from src.config.settings import get_project_root, get_workflow_config
from src.schemas.state import ResearchState


NODE_CLASSES = {
    "safety": {
        "input_guard",
        "tool_output_sanitizer",
        "safety_review",
    },
    "planner": {
        "plan_research",
    },
    "tool": {
        "web_search_sub_agent",
        "web_search_result_ready",
        "local_document_search_tool",
    },
    "evidence": {
        "deduplicate_and_cluster",
        "evaluate_evidence_quality",
        "build_evidence_matrix",
        "check_evidence_sufficiency",
    },
    "hitl": {
        "web_search_hitl_request",
        "request_human_review",
    },
    "control": {
        "check_step_budget",
        "strategy_iteration",
    },
    "output": {
        "prepare_degraded_report",
        "generate_research_report",
        "review_research_report",
        "persist_outputs",
    },
}

CLASS_DEFS = """
classDef safety fill:#ffe4e6,stroke:#e11d48,color:#111827;
classDef planner fill:#dbeafe,stroke:#2563eb,color:#111827;
classDef tool fill:#dcfce7,stroke:#16a34a,color:#111827;
classDef evidence fill:#fef3c7,stroke:#d97706,color:#111827;
classDef hitl fill:#f3e8ff,stroke:#9333ea,color:#111827;
classDef control fill:#fed7aa,stroke:#ea580c,color:#111827;
classDef output fill:#e5e7eb,stroke:#4b5563,color:#111827;
""".strip()


def _node_class(node_name: str) -> str | None:
    """返回运行节点对应的 Mermaid 样式分类。"""

    for class_name, node_names in NODE_CLASSES.items():
        if node_name in node_names:
            return class_name
    return None


def _mermaid_label(value: str) -> str:
    """转义 Mermaid 节点标签。"""

    return value.replace('"', r'\"')


def _build_executed_mermaid(executed_nodes: list[str]) -> str:
    """根据本次实际执行节点顺序生成 Mermaid。"""

    lines = [
        "---",
        "config:",
        "  flowchart:",
        "    curve: linear",
        "---",
        "flowchart TD",
        "    N0([start])",
    ]
    for index, node_name in enumerate(executed_nodes, start=1):
        lines.append(f'    N{index}["{_mermaid_label(node_name)}"]')
    end_index = len(executed_nodes) + 1
    lines.append(f"    N{end_index}([end])")

    for index in range(end_index):
        lines.append(f"    N{index} --> N{index + 1}")

    lines.append(CLASS_DEFS)
    for index, node_name in enumerate(executed_nodes, start=1):
        class_name = _node_class(node_name)
        if class_name is not None:
            lines.append(f"    class N{index} {class_name};")
    return "\n".join(lines) + "\n"


def _write_executed_graph_png(
    runs_dir: Path,
    run_id: str,
    executed_nodes: list[str],
) -> tuple[str, str]:
    """静默保存实际运行链路 PNG。"""

    mermaid = _build_executed_mermaid(executed_nodes)
    png_path = runs_dir / f"{run_id}_executed.png"
    png_bytes = draw_mermaid_png(mermaid_syntax=mermaid, background_color="white")
    png_path.write_bytes(png_bytes)
    return mermaid, str(png_path)


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
    """保存最终报告、运行指标和实际运行链路 PNG 到 `outputs/`。"""

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
    executed_mermaid, executed_mermaid_png_path = _write_executed_graph_png(
        runs_dir=runs_dir,
        run_id=run_id,
        executed_nodes=executed_nodes,
    )

    workflow_config = get_workflow_config()
    source_contribution = _source_contribution(state)
    web_hitl_trigger_count_by_source = {
        "web_search": len(state.get("web_hitl_decisions", [])),
    }
    metrics = {
        "run_id": run_id,
        "total_steps": len(executed_nodes),
        "executed_nodes": executed_nodes,
        "search_steps": state.get("search_steps", 0),
        "max_search_steps": state.get(
            "max_search_steps", workflow_config.max_search_steps
        ),
        "review_revision_count": state.get("review_revision_count", 0),
        "max_review_revisions": state.get(
            "max_review_revisions", workflow_config.max_review_revisions
        ),
        "safety_revision_count": state.get("safety_revision_count", 0),
        "max_safety_revisions": state.get(
            "max_safety_revisions", workflow_config.max_safety_revisions
        ),
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
        "executed_mermaid_png_path": executed_mermaid_png_path,
    }
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "executed_nodes": ["persist_outputs"],
        "final_report_path": str(report_path),
        "executed_mermaid": executed_mermaid,
        "executed_mermaid_png_path": executed_mermaid_png_path,
        "output_artifacts": {
            "final_report": str(report_path),
            "metrics": str(metrics_path),
            "executed_mermaid_png": executed_mermaid_png_path,
        },
        "evaluation_metrics": metrics,
    }
