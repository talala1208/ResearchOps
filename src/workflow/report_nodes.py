"""报告生成、降级准备与质量 Review 节点。"""

from __future__ import annotations

from typing import Any

from src.config.settings import get_workflow_config
from src.schemas.state import ResearchState
from src.workflow.node_utils import record_node



def _format_list(items: list[str]) -> str:
    """格式化 Markdown 列表。"""

    if not items:
        return "- 无"
    return "\n".join(f"- {item}" for item in items)


def _build_evidence_summary(state: ResearchState) -> str:
    """生成证据矩阵 Markdown 摘要。"""

    evidence_matrix = state.get("evidence_matrix", {})
    evidence_items = state.get("evidence_items", {})
    if not evidence_matrix:
        return "暂无证据矩阵。"

    sections = []
    for question_id, matrix_item in evidence_matrix.items():
        sections.append(f"### {question_id}｜{matrix_item['question']}")
        sections.append(f"优先级：{matrix_item['priority']}")
        sections.append(f"覆盖来源类型：{', '.join(matrix_item['covered_source_types']) or '无'}")
        sections.append("")
        if not matrix_item["evidence_ids"]:
            sections.append("- 暂无证据")
        for evidence_id in matrix_item["evidence_ids"]:
            evidence = evidence_items[evidence_id]
            sections.append(
                "- "
                f"[{evidence_id}] {evidence['title']} "
                f"({evidence['source_type']} / {evidence['source_name']})："
                f"{evidence['snippet']}"
            )
        sections.append("")

    return "\n".join(sections).strip()


def _build_question_status_summary(state: ResearchState) -> str:
    """生成子问题证据状态摘要。"""

    statuses = state.get("question_evidence_status", {})
    if not statuses:
        return "暂无证据充足性判断。"

    lines = []
    for question_id, status in statuses.items():
        result = "满足" if status["minimum_standard_met"] else "不足"
        missing_reason = status.get("missing_reason") or "无"
        lines.append(
            f"- {question_id}：{result}，加权分 {status['weighted_score']}，"
            f"证据 {status['collected_evidence_count']}/{status['required_evidence_count']}，"
            f"缺口：{missing_reason}"
        )
    return "\n".join(lines)


def prepare_degraded_report(state: ResearchState) -> dict[str, Any]:
    """降级报告准备节点。

    该节点只负责设置降级状态，不负责判断预算，也不负责写报告正文。
    """

    input_guard_result = state.get("input_guard_result", {})
    safety_result = state.get("safety_review_result", {})
    safety_revision_count = state.get("safety_revision_count", 0)
    if safety_result and safety_result.get("safety_pass") is False:
        safety_revision_count += 1

    input_guard_reason = None
    if input_guard_result:
        detected_risks = input_guard_result.get("detected_risks", [])
        if input_guard_result.get("downgrade_reason"):
            input_guard_reason = input_guard_result["downgrade_reason"]
        elif input_guard_result.get("safety_pass") is False:
            input_guard_reason = "输入安全检查未通过"
        elif input_guard_result.get("downgrade_required") is True:
            input_guard_reason = "输入安全检查要求降级输出"

        if input_guard_reason and detected_risks:
            input_guard_reason = f"{input_guard_reason}；风险标签：{', '.join(detected_risks)}"

    reason_candidates = [
        input_guard_reason,
        state.get("step_budget_reason"),
        state.get("degradation_reason"),
        state.get("evidence_sufficiency_result", {}).get("degradation_reason"),
        safety_result.get("downgrade_reason") if safety_result else None,
    ]
    reason = next((reason for reason in reason_candidates if reason), None)
    if reason is None:
        reason = "降级输出：证据不足、检索预算不足、Review 未完全通过或安全审查要求保守输出"

    return {
        **record_node(state, "prepare_degraded_report"),
        "degraded": True,
        "degradation_reason": reason,
        "safety_revision_count": safety_revision_count,
    }


def generate_research_report(state: ResearchState) -> dict[str, Any]:
    """生成 Markdown 研究报告。"""

    user_query = state.get("user_query", "")
    research_goal = state.get("research_goal", user_query)
    degraded = state.get("degraded", False)
    evidence_sufficiency_result = state.get("evidence_sufficiency_result", {})
    conflicts = state.get("conflicts", {})
    hitl_decisions = state.get("hitl_decisions", [])
    web_hitl_decisions = state.get("web_hitl_decisions", [])
    used_evidence_ids = state.get("entity_index", {}).get("used_evidence_ids", [])

    review_revision_count = state.get("review_revision_count", 0)
    previous_review = state.get("review_result", {})
    if previous_review and previous_review.get("passed") is False:
        review_revision_count += 1

    title = "# ResearchOps 研究报告"
    status_line = "降级报告" if degraded else "正常报告"
    input_guard_result = state.get("input_guard_result", {})
    stopped_by_input_guard = bool(
        input_guard_result
        and (
            input_guard_result.get("safety_pass") is False
            or input_guard_result.get("downgrade_required") is True
        )
    )
    degradation_block = ""
    if degraded:
        guard_note = ""
        if stopped_by_input_guard:
            guard_note = "\n\n本次运行在输入安全检查阶段已停止，未进入研究规划、检索和证据评估流程。"
        degradation_block = f"\n## 降级说明\n\n{state.get('degradation_reason')}{guard_note}\n"

    conflict_lines = []
    for conflict in conflicts.values():
        conflict_lines.append(
            f"- {conflict['conflict_id']} / {conflict['question_id']}："
            f"{conflict['conflict_summary']}，HITL 分数 {conflict['hitl_need_score']}"
        )

    report_draft = f"""{title}

## 基本信息

- 报告状态：{status_line}
- 用户问题：{user_query}
- 研究目标：{research_goal}
- 证据充足分：{evidence_sufficiency_result.get('overall_score', '未计算')}
- 证据充足阈值：{evidence_sufficiency_result.get('threshold', '未计算')}
- 使用证据数：{len(used_evidence_ids)}
{degradation_block}
## 结论摘要

当前报告基于已收集证据生成。由于当前仍处于 MVP 阶段，结论以证据整理和风险提示为主，不给出超出证据范围的确定性判断。

## 子问题证据状态

{_build_question_status_summary(state)}

## 证据矩阵

{_build_evidence_summary(state)}

## 冲突与 HITL

{_format_list(conflict_lines)}

## HITL 记录

- Web HITL 记录数：{len(web_hitl_decisions)}
- 证据冲突 HITL 记录数：{len(hitl_decisions)}

## 不确定性与边界

- 外部网页、本地文档和结构化数据均按不可信资料处理。
- 证据不足或来源冲突时，不自动补充没有证据支撑的结论。
- 当前替代数据源结果只用于跑通工程链路，不代表真实市场结论。
""".strip()

    return {
        **record_node(state, "generate_research_report"),
        "report_draft": report_draft,
        "degraded": degraded,
        "degradation_reason": state.get("degradation_reason"),
        "review_revision_count": review_revision_count,
    }


def review_research_report(state: ResearchState) -> dict[str, Any]:
    """对研究报告做确定性质量 Review。"""

    workflow_config = get_workflow_config()
    review_pass_score = workflow_config.review_pass_score

    report_draft = state["report_draft"]
    evidence_sufficiency_result = state.get("evidence_sufficiency_result", {})
    evidence_items = state.get("evidence_items", {})
    used_evidence_ids = state.get("entity_index", {}).get("used_evidence_ids", [])
    degraded = state.get("degraded", False)

    source_coverage_score = float(evidence_sufficiency_result.get("overall_score", 0.0))
    citation_completeness_score = 1.0 if used_evidence_ids else 0.0
    groundedness_score = 1.0 if evidence_items or degraded else 0.0
    boundary_score = 1.0 if "不确定性与边界" in report_draft else 0.5
    over_inference_risk = 0.1 if boundary_score >= 1.0 else 0.4
    report_score = round(
        source_coverage_score * 0.35
        + citation_completeness_score * 0.25
        + groundedness_score * 0.25
        + boundary_score * 0.15,
        4,
    )

    revision_suggestions = []
    if citation_completeness_score < 1.0:
        revision_suggestions.append("报告缺少可引用证据，需要补充 evidence_id 引用。")
    if source_coverage_score < review_pass_score:
        revision_suggestions.append("证据覆盖不足，需要补充高优先级子问题的证据。")
    if boundary_score < 1.0:
        revision_suggestions.append("报告需要补充不确定性与边界说明。")

    passed = report_score >= review_pass_score or degraded

    return {
        **record_node(state, "review_research_report"),
        "review_result": {
            "passed": passed,
            "report_score": report_score,
            "source_coverage_score": source_coverage_score,
            "citation_completeness_score": citation_completeness_score,
            "groundedness_score": groundedness_score,
            "boundary_score": boundary_score,
            "over_inference_risk": over_inference_risk,
            "revision_suggestions": revision_suggestions,
        },
    }
