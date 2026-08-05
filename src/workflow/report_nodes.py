"""报告生成、降级准备与质量 Review 节点。"""

from __future__ import annotations

from typing import Any

from src.schemas.state import ResearchState
from src.workflow.node_utils import record_node


def prepare_degraded_report(state: ResearchState) -> dict[str, Any]:
    """降级报告准备占位节点。

    该节点只负责设置降级状态，不负责判断预算，也不负责写报告正文。
    """

    safety_result = state.get("safety_review_result", {})
    safety_revision_count = state.get("safety_revision_count", 0)
    if safety_result and safety_result.get("safety_pass") is False:
        safety_revision_count += 1

    reason = state.get("degradation_reason") or "占位降级：证据不足、检索预算不足或安全审查未通过"

    return {
        **record_node(state, "prepare_degraded_report"),
        "degraded": True,
        "degradation_reason": reason,
        "safety_revision_count": safety_revision_count,
    }


def generate_research_report(state: ResearchState) -> dict[str, Any]:
    """研究报告生成占位节点。"""

    user_query = state.get("user_query", "")
    degraded = state.get("degraded", False)
    degradation_note = "\n\n降级原因：" + state.get("degradation_reason", "") if degraded else ""
    report_draft = f"# 研究报告草稿\n\n研究主题：{user_query}{degradation_note}\n\n当前为空 Graph 占位输出。"

    review_revision_count = state.get("review_revision_count", 0)
    previous_review = state.get("review_result", {})
    if previous_review and previous_review.get("passed") is False:
        review_revision_count += 1

    return {
        **record_node(state, "generate_research_report"),
        "report_draft": report_draft,
        "degraded": degraded,
        "degradation_reason": state.get("degradation_reason"),
        "review_revision_count": review_revision_count,
    }


def review_research_report(state: ResearchState) -> dict[str, Any]:
    """报告复核占位节点。"""

    return {
        **record_node(state, "review_research_report"),
        "review_result": {
            "passed": True,
            "report_score": 1.0,
            "source_coverage_score": 1.0,
            "citation_completeness_score": 1.0,
            "groundedness_score": 1.0,
            "boundary_score": 1.0,
            "over_inference_risk": 0.0,
            "revision_suggestions": [],
        },
    }
