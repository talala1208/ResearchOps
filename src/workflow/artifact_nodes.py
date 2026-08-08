"""本地运行产物保存节点。"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.runnables.graph_mermaid import draw_mermaid_png

from src.config.settings import get_project_root, get_workflow_config
from src.schemas.state import ResearchState


NODE_CLASSES = {
    "safety": {
        "input_guard",
        "sanitize_and_cluster",
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
        "sanitize_and_cluster",
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

# 与 edges.py 主图拓扑保持一致；用于还原 fan-out / fan-in / 循环。
WORKFLOW_NODE_ORDER = [
    "input_guard",
    "plan_research",
    "web_search_sub_agent",
    "web_search_hitl_request",
    "web_search_result_ready",
    "local_document_search_tool",
    "sanitize_and_cluster",
    "evaluate_evidence_quality",
    "request_human_review",
    "build_evidence_matrix",
    "check_evidence_sufficiency",
    "check_step_budget",
    "strategy_iteration",
    "prepare_degraded_report",
    "generate_research_report",
    "review_research_report",
    "safety_review",
    "persist_outputs",
]

WORKFLOW_EDGES: list[tuple[str, str]] = [
    ("__start__", "input_guard"),
    ("input_guard", "plan_research"),
    ("input_guard", "prepare_degraded_report"),
    ("plan_research", "web_search_sub_agent"),
    ("plan_research", "local_document_search_tool"),
    ("web_search_sub_agent", "web_search_hitl_request"),
    ("web_search_sub_agent", "web_search_result_ready"),
    ("web_search_hitl_request", "web_search_result_ready"),
    ("web_search_result_ready", "sanitize_and_cluster"),
    ("local_document_search_tool", "sanitize_and_cluster"),
    ("sanitize_and_cluster", "evaluate_evidence_quality"),
    ("evaluate_evidence_quality", "request_human_review"),
    ("evaluate_evidence_quality", "build_evidence_matrix"),
    ("request_human_review", "build_evidence_matrix"),
    ("build_evidence_matrix", "check_evidence_sufficiency"),
    ("check_evidence_sufficiency", "generate_research_report"),
    ("check_evidence_sufficiency", "check_step_budget"),
    ("check_step_budget", "strategy_iteration"),
    ("check_step_budget", "prepare_degraded_report"),
    ("strategy_iteration", "plan_research"),
    ("prepare_degraded_report", "generate_research_report"),
    ("generate_research_report", "review_research_report"),
    ("review_research_report", "generate_research_report"),
    ("review_research_report", "safety_review"),
    ("safety_review", "prepare_degraded_report"),
    ("safety_review", "persist_outputs"),
    ("persist_outputs", "__end__"),
]

FANOUT_EDGES = {
    ("plan_research", "web_search_sub_agent"),
    ("plan_research", "local_document_search_tool"),
}

JOIN_EDGES = {
    ("web_search_result_ready", "sanitize_and_cluster"),
    ("local_document_search_tool", "sanitize_and_cluster"),
}

LOOP_EDGES = {
    ("strategy_iteration", "plan_research"),
    ("review_research_report", "generate_research_report"),
    ("safety_review", "prepare_degraded_report"),
}

OUTGOING: dict[str, tuple[str, ...]] = {}
for _src, _dst in WORKFLOW_EDGES:
    OUTGOING.setdefault(_src, ())
    if _dst not in OUTGOING[_src]:
        OUTGOING[_src] = (*OUTGOING[_src], _dst)

CLASS_DEFS = """
classDef safety fill:#ffe4e6,stroke:#e11d48,color:#111827;
classDef planner fill:#dbeafe,stroke:#2563eb,color:#111827;
classDef tool fill:#dcfce7,stroke:#16a34a,color:#111827;
classDef evidence fill:#fef3c7,stroke:#d97706,color:#111827;
classDef hitl fill:#f3e8ff,stroke:#9333ea,color:#111827;
classDef control fill:#fed7aa,stroke:#ea580c,color:#111827;
classDef output fill:#e5e7eb,stroke:#4b5563,color:#111827;
classDef skipped fill:#f9fafb,stroke:#9ca3af,color:#9ca3af,stroke-dasharray: 5 5;
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


def _nearest_successor(
    executed_nodes: list[str],
    index: int,
    candidates: tuple[str, ...],
) -> str | None:
    """在当前位置之后找最近的合法后继节点。"""

    best_name: str | None = None
    best_index: int | None = None
    for candidate in candidates:
        for offset, name in enumerate(executed_nodes[index + 1 :], start=index + 1):
            if name != candidate:
                continue
            if best_index is None or offset < best_index:
                best_name = candidate
                best_index = offset
            break
    return best_name


def _infer_taken_edges(executed_nodes: list[str]) -> set[tuple[str, str]]:
    """根据实际执行序列推断本次走过的边。

    并行 fan-out 固定还原为两条出边；fan-in 在汇聚节点出现时标记两侧入边。
    条件分支取当前位置之后最近的合法后继；循环边因此可被多次命中。
    """

    taken: set[tuple[str, str]] = set()
    if not executed_nodes:
        return taken

    taken.add(("__start__", executed_nodes[0]))
    if executed_nodes[-1] == "persist_outputs":
        taken.add(("persist_outputs", "__end__"))

    for index, node_name in enumerate(executed_nodes):
        if node_name == "plan_research":
            taken.add(("plan_research", "web_search_sub_agent"))
            taken.add(("plan_research", "local_document_search_tool"))
            continue

        if node_name in {"web_search_result_ready", "local_document_search_tool"}:
            if "sanitize_and_cluster" in executed_nodes[index + 1 :]:
                taken.add((node_name, "sanitize_and_cluster"))
            continue

        successors = OUTGOING.get(node_name, ())
        if not successors:
            continue
        # join 边已在上方单独处理，避免把 sanitize 误当成普通最近后继。
        candidates = tuple(
            successor
            for successor in successors
            if (node_name, successor) not in JOIN_EDGES
        )
        if not candidates:
            continue
        nearest = _nearest_successor(executed_nodes, index, candidates)
        if nearest is not None:
            taken.add((node_name, nearest))

    return taken


def _edge_label(src: str, dst: str, *, taken: bool, loop_count: int) -> str:
    """生成边标签，区分并行、汇聚、循环与未执行路径。"""

    if (src, dst) in FANOUT_EDGES:
        return "并行" if taken else "未执行"
    if (src, dst) in JOIN_EDGES:
        return "汇聚" if taken else "未执行"
    if (src, dst) in LOOP_EDGES:
        if not taken:
            return "循环/未执行"
        if loop_count > 1:
            return f"循环×{loop_count}"
        return "循环"
    if not taken:
        return "未执行"
    return ""


def _count_loop_uses(executed_nodes: list[str], src: str, dst: str) -> int:
    """统计循环边在本次运行中被走过的次数。"""

    count = 0
    for index, node_name in enumerate(executed_nodes):
        if node_name != src:
            continue
        if src == "review_research_report":
            nearest = _nearest_successor(
                executed_nodes,
                index,
                ("generate_research_report", "safety_review"),
            )
            if nearest == dst:
                count += 1
            continue
        if src == "safety_review":
            nearest = _nearest_successor(
                executed_nodes,
                index,
                ("prepare_degraded_report", "persist_outputs"),
            )
            if nearest == dst:
                count += 1
            continue
        if _nearest_successor(executed_nodes, index, (dst,)) == dst:
            count += 1
    return count


def _node_display_label(node_name: str, executed_counts: Counter[str]) -> str:
    """生成节点显示标签；循环复用时标注执行次数。"""

    count = executed_counts.get(node_name, 0)
    if count > 1:
        return f"{node_name} ×{count}"
    return node_name


def _build_executed_mermaid(executed_nodes: list[str]) -> str:
    """按主图拓扑生成实际运行 Mermaid，保留未执行路径并还原 fan-out/fan-in。"""

    executed_counts = Counter(executed_nodes)
    executed_set = set(executed_nodes)
    taken_edges = _infer_taken_edges(executed_nodes)
    parallel_nodes = (
        "web_search_sub_agent",
        "web_search_hitl_request",
        "web_search_result_ready",
        "local_document_search_tool",
    )

    lines = [
        "---",
        "config:",
        "  flowchart:",
        "    curve: basis",
        "---",
        "flowchart TD",
        "    __start__([start])",
    ]

    for node_name in WORKFLOW_NODE_ORDER:
        if node_name in parallel_nodes:
            continue
        label = _node_display_label(node_name, executed_counts)
        lines.append(f'    {node_name}["{_mermaid_label(label)}"]')

    lines.append("    __end__([end])")
    lines.append("    subgraph parallel_retrieval [并行检索 fan-out / fan-in]")
    for node_name in parallel_nodes:
        label = _node_display_label(node_name, executed_counts)
        lines.append(f'        {node_name}["{_mermaid_label(label)}"]')
    lines.append("    end")

    for src, dst in WORKFLOW_EDGES:
        taken = (src, dst) in taken_edges
        loop_count = (
            _count_loop_uses(executed_nodes, src, dst)
            if (src, dst) in LOOP_EDGES
            else 0
        )
        label = _edge_label(src, dst, taken=taken, loop_count=loop_count)
        if taken:
            if label:
                lines.append(f"    {src} -->|{label}| {dst}")
            else:
                lines.append(f"    {src} --> {dst}")
        else:
            if label:
                lines.append(f"    {src} -.->|{label}| {dst}")
            else:
                lines.append(f"    {src} -.-> {dst}")

    lines.append(CLASS_DEFS)
    for node_name in WORKFLOW_NODE_ORDER:
        if node_name in executed_set:
            class_name = _node_class(node_name)
            if class_name is not None:
                lines.append(f"    class {node_name} {class_name};")
        else:
            lines.append(f"    class {node_name} skipped;")

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
        "final_citation_count": len(
            state.get("entity_index", {}).get("used_evidence_ids", [])
        ),
        "conflict_count": len(state.get("conflicts", {})),
        "discarded_candidate_count": state.get("discarded_candidate_count", 0),
        "degraded": state.get("degraded", False),
        "degradation_reason": state.get("degradation_reason"),
        "review_score": state.get("review_result", {}).get("report_score"),
        "safety_risk_level": state.get("safety_review_result", {}).get(
            "safety_risk_level"
        ),
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
