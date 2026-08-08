"""运行 ResearchOps Agent 端到端 smoke。

用法：
    uv run python scripts/run_smoke.py
    uv run python scripts/run_smoke.py "研究 Cursor 的产品能力、价格和目标用户"

说明：
    该脚本会真实调用 Graph，因此会触发已接入的 LLM 节点。
    检索、报告 Review 和安全修正预算统一从 .env 读取。
    输出报告会写入 outputs/reports/，运行指标会写入 outputs/runs/。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from pprint import pprint

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.workflow.graph import graph  # noqa: E402


DEFAULT_QUERY = "研究 Cursor 的产品能力、价格、竞品和目标用户"


def main() -> None:
    """运行一次端到端 smoke。"""

    user_query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY

    result = graph.invoke({"user_query": user_query})

    print("\n=== Smoke Result ===")
    output_artifacts = result.get("output_artifacts", {})
    print(f"final_report_path: {output_artifacts.get('final_report')}")
    print("\nexecuted_nodes:")
    for node_name in result.get("executed_nodes", []):
        print(f"- {node_name}")

    print("\nevaluation_metrics:")
    print(json.dumps(result.get("evaluation_metrics", {}), ensure_ascii=False, indent=2))

    print("\nreview_result:")
    pprint(result.get("review_result", {}))

    print("\nsafety_review_result:")
    pprint(result.get("safety_review_result", {}))


if __name__ == "__main__":
    main()
