"""输入与最终安全审查节点。"""

from __future__ import annotations

from typing import Any

from src.llm.chat import build_chat_model
from src.llm.prompt_loader import load_prompt, render_prompt_template
from src.llm.structured_outputs import InputGuardOutput
from src.schemas.state import ResearchState
from src.workflow.node_utils import record_node


def input_guard(state: ResearchState) -> dict[str, Any]:
    """输入安全检查节点。

    读取：`user_query`
    写入：`input_guard_result`
    """

    user_query = state["user_query"]
    prompt = load_prompt("input_guard.yml")
    user_prompt = render_prompt_template(
        prompt["user_prompt_template"],
        {"user_query": user_query},
    )

    model = build_chat_model("input_guard").with_structured_output(InputGuardOutput)
    result = model.invoke(
        [
            ("system", prompt["system_prompt"]),
            ("human", user_prompt),
        ]
    )

    return {
        **record_node(state, "input_guard"),
        "input_guard_result": result.model_dump(),
    }


def safety_review(state: ResearchState) -> dict[str, Any]:
    """最终安全审查占位节点。"""

    return {
        **record_node(state, "safety_review"),
        "safety_review_result": {
            "safety_pass": True,
            "safety_risk_level": "low",
            "detected_risks": [],
            "downgrade_required": False,
            "downgrade_reason": None,
        },
        "final_report": state.get("report_draft", ""),
    }
