from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from langsmith import evaluate

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from src.evaluators.external_agent_worker_stability import (  # noqa: E402
    external_agent_worker_stability_evaluator,
)
from src.evaluators.researchops_summary import researchops_summary_evaluator  # noqa: E402
from src.evaluators.web_tool_stability import web_tool_stability_evaluator  # noqa: E402
from src.workflow.graph import graph  # noqa: E402


def target_function(inputs: dict) -> dict:
    """运行待评测的 ResearchOps 主 Graph。"""

    return graph.invoke({"user_query": inputs["user_query"]})


def main() -> None:
    """使用 Web / 外部 Worker 稳定性 evaluator 运行 LangSmith 实验。"""

    evaluate(
        target_function,
        data="ResearchOps Agent",
        evaluators=[
            web_tool_stability_evaluator,
            external_agent_worker_stability_evaluator,
        ],
        summary_evaluators=[researchops_summary_evaluator],
    )


if __name__ == "__main__":
    main()