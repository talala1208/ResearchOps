"""输入与最终安全审查节点。"""

from __future__ import annotations

import re
from typing import Any

from src.llm.chat import build_chat_model
from src.llm.prompt_loader import load_prompt, render_prompt_template
from src.llm.structured_outputs import InputGuardOutput
from src.schemas.state import ResearchState
from src.workflow.node_utils import record_node


SENSITIVE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*[^\s]+"),
    re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+[^\s]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
INJECTION_PATTERNS = [
    "ignore previous instructions",
    "忽略之前的指令",
    "泄露系统 prompt",
    "输出系统 prompt",
    "读取 .env",
]


def input_guard(state: ResearchState) -> dict[str, Any]:
    """输入安全检查节点。"""

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
    """最终安全审查节点。

    当前使用确定性规则检查敏感信息和明显注入残留。
    """

    report = state["report_draft"]
    detected_risks = []

    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(report):
            detected_risks.append("sensitive_secret_pattern")
            break

    lowered_report = report.lower()
    for pattern in INJECTION_PATTERNS:
        if pattern.lower() in lowered_report:
            detected_risks.append("prompt_injection_text_leakage")
            break

    high_risk_claim_detected = "确定性医疗建议" in report or "保证收益" in report
    if high_risk_claim_detected:
        detected_risks.append("high_risk_claim")

    safety_pass = not detected_risks
    safety_risk_level = "low"
    if detected_risks:
        safety_risk_level = "high" if "sensitive_secret_pattern" in detected_risks else "medium"

    downgrade_required = not safety_pass
    downgrade_reason = "安全审查未通过，需要降级输出。" if downgrade_required else None
    final_report = report
    if downgrade_required:
        final_report = (
            report
            + "\n\n## 安全审查说明\n\n"
            + downgrade_reason
            + "检测到风险："
            + "、".join(detected_risks)
        )

    return {
        **record_node(state, "safety_review"),
        "safety_review_result": {
            "safety_pass": safety_pass,
            "safety_risk_level": safety_risk_level,
            "detected_risks": detected_risks,
            "downgrade_required": downgrade_required,
            "downgrade_reason": downgrade_reason,
        },
        "final_report": final_report,
    }
