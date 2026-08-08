"""输入与最终安全审查节点。"""

from __future__ import annotations

import re
from typing import Any

from src.llm.chat import build_chat_model
from src.llm.prompt_loader import load_prompt, render_prompt_template
from src.llm.structured_outputs import InputGuardOutput
from src.schemas.state import ResearchState
from src.workflow.node_utils import record_node
from src.workflow.report_nodes import append_cited_evidence_appendix


SECRET_VALUE_PATTERNS = (
    re.compile(r"(?P<value>sk-[A-Za-z0-9_-]{12,})"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*(?P<value>[^\s,;]+)"),
    re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+(?P<value>[^\s,;]+)"),
)
PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----\s+"
    r"(?P<body>[A-Za-z0-9+/=\s]{32,}?)\s+"
    r"-----END [A-Z ]*PRIVATE KEY-----",
    re.MULTILINE,
)
PLACEHOLDER_MARKERS = (
    "your_api_key",
    "your-api-key",
    "your_token",
    "your-token",
    "api_key_here",
    "token_here",
    "example_key",
    "example_token",
    "test_key",
    "test_token",
    "demo_key",
    "demo_token",
    "placeholder",
    "redacted",
)
INJECTION_PATTERNS = [
    "ignore previous instructions",
    "忽略之前的指令",
    "泄露系统 prompt",
    "输出系统 prompt",
    "读取 .env",
]


def _looks_like_secret_placeholder(value: str) -> bool:
    """判断密钥候选是否为文档示例中的明确占位符。"""

    cleaned = value.strip("`'\"()[]{}.,:").strip()
    lowered = cleaned.lower()
    if not lowered:
        return True
    if value.strip().startswith("<") and value.strip().endswith(">"):
        return True
    if value.strip().startswith("${") and value.strip().endswith("}"):
        return True
    if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
        return True
    return re.fullmatch(r"[xX*._-]+", cleaned) is not None


def _contains_sensitive_secret(report: str) -> bool:
    """检测具有真实凭据形态的密钥，忽略明确示例占位符。"""

    if PRIVATE_KEY_BLOCK_PATTERN.search(report):
        return True
    for pattern in SECRET_VALUE_PATTERNS:
        for match in pattern.finditer(report):
            value = match.group("value")
            if _looks_like_secret_placeholder(value):
                continue
            cleaned = value.strip("`'\"()[]{}.,:")
            if len(cleaned) >= 12:
                return True
    return False


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

    if _contains_sensitive_secret(report):
        detected_risks.append("sensitive_secret_pattern")

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
    safety_state_updates: dict[str, Any] = {}
    if downgrade_required:
        final_report = (
            "# 安全降级报告\n\n"
            "原报告未通过安全审查，危险正文已被移除，未写入最终产物。\n\n"
            "## 安全审查说明\n\n"
            f"{downgrade_reason}\n\n"
            "检测到风险："
            + "、".join(detected_risks)
            + "\n"
        )
        evidence_items = state.get("evidence_items", {})
        safety_state_updates = {
            "degraded": True,
            "degradation_reason": downgrade_reason,
            "entity_index": {
                **state.get("entity_index", {}),
                "used_evidence_ids": [],
            },
            "evidence_items": {
                evidence_id: {
                    **evidence,
                    "used_in_final_report": False,
                }
                for evidence_id, evidence in evidence_items.items()
            },
        }
    else:
        final_report = append_cited_evidence_appendix(final_report, state)

    return {
        **record_node(state, "safety_review"),
        "safety_review_result": {
            "safety_pass": safety_pass,
            "safety_risk_level": safety_risk_level,
            "detected_risks": detected_risks,
            "downgrade_required": downgrade_required,
            "downgrade_reason": downgrade_reason,
        },
        **safety_state_updates,
        "final_report": final_report,
        "report_draft": "",
    }
