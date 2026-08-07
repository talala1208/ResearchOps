"""导出当前 LangGraph 编排图为带颜色的 PNG 文件。

用法：
    uv run python scripts/export_graph_mermaid.py
    python scripts/export_graph_mermaid.py

输出：
    outputs/runs/researchops_graph.png
"""

from __future__ import annotations

import sys
from pathlib import Path

from langchain_core.runnables.graph_mermaid import draw_mermaid_png

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.workflow.graph import graph  # noqa: E402


PNG_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "runs" / "researchops_graph.png"

NODE_CLASSES = {
    "safety": [
        "input_guard",
        "sanitize_and_cluster",
        "safety_review",
    ],
    "planner": [
        "plan_research",
    ],
    "tool": [
        "web_search_sub_agent",
        "web_search_result_ready",
        "local_document_search_tool",
    ],
    "evidence": [
        "sanitize_and_cluster",
        "evaluate_evidence_quality",
        "build_evidence_matrix",
        "check_evidence_sufficiency",
    ],
    "hitl": [
        "web_search_hitl_request",
        "request_human_review",
    ],
    "control": [
        "check_step_budget",
        "strategy_iteration",
    ],
    "output": [
        "prepare_degraded_report",
        "generate_research_report",
        "review_research_report",
        "persist_outputs",
    ],
}

CLASS_DEFS = """
classDef safety fill:#ffe4e6,stroke:#e11d48,color:#111827;
classDef planner fill:#dbeafe,stroke:#2563eb,color:#111827;
classDef tool fill:#dcfce7,stroke:#16a34a,color:#111827;
classDef evidence fill:#fef3c7,stroke:#d97706,color:#111827;
classDef hitl fill:#f3e8ff,stroke:#9333ea,color:#111827;
classDef control fill:#ffedd5,stroke:#ea580c,color:#111827;
classDef output fill:#e5e7eb,stroke:#4b5563,color:#111827;
""".strip()


def build_colored_mermaid() -> str:
    """生成带节点分类颜色的 Mermaid 文本。"""

    mermaid = graph.get_graph().draw_mermaid()
    mermaid = mermaid.replace(
        "classDef last fill:#bfb6fc",
        "classDef last fill-opacity:0",
    )
    class_lines = []
    for class_name, node_names in NODE_CLASSES.items():
        class_lines.append(f"class {','.join(node_names)} {class_name};")
    return f"{mermaid}\n{CLASS_DEFS}\n" + "\n".join(class_lines) + "\n"


def main() -> None:
    """保存 Graph 的 PNG 图片。"""

    PNG_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    mermaid = build_colored_mermaid()
    png_bytes = draw_mermaid_png(mermaid_syntax=mermaid, background_color="white")
    PNG_OUTPUT_PATH.write_bytes(png_bytes)
    print(f"已保存 PNG 图：{PNG_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
