"""运行 ResearchOps LangSmith Pairwise A/B Test。

用法：
    uv run python src/dataset/run_pairwise_ab_test.py \
      --experiment-a "Experiment A Name or ID" \
      --experiment-b "Experiment B Name or ID"

说明：
    先分别用 LangSmith evaluate 跑出两个 experiment，再用本脚本做 pairwise 对比。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langsmith import evaluate  # noqa: E402

from src.evaluators.research_report_pairwise import (  # noqa: E402
    research_report_pairwise_preference,
)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="运行 ResearchOps Pairwise A/B Test。")
    parser.add_argument("--experiment-a", required=True, help="实验 A 的名称或 ID。")
    parser.add_argument("--experiment-b", required=True, help="实验 B 的名称或 ID。")
    return parser.parse_args()


def main() -> None:
    """脚本入口。"""

    args = parse_args()
    evaluate(
        (args.experiment_a, args.experiment_b),
        evaluators=[research_report_pairwise_preference],
    )


if __name__ == "__main__":
    main()
