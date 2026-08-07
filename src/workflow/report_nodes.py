"""报告生成、降级准备与质量 Review 节点。"""

from __future__ import annotations

import json
from typing import Any

from src.artifacts.evidence_content import load_evidence_content
from src.config.settings import get_workflow_config
from src.llm.chat import build_chat_model
from src.llm.prompt_loader import load_prompt, render_prompt_template
from src.llm.structured_outputs import ResearchReportOutput, ResearchReportReviewOutput
from src.schemas.state import ResearchState
from src.workflow.node_utils import record_node


EVIDENCE_CATALOG_MAX_CHARS = 500


def _format_list(items: list[str]) -> str:
    """格式化 Markdown 列表。"""

    if not items:
        return "- 无"
    return "\n".join(f"- {item}" for item in items)


def _evidence_display_text(evidence: dict[str, Any], *, max_chars: int | None = None) -> str:
    """报告展示用正文：优先 content_path 落盘内容，再回退短摘要。"""

    content = load_evidence_content(evidence.get("content_path"))
    if content.strip():
        text = content.strip()
    else:
        snippet = evidence.get("snippet")
        text = snippet.strip() if isinstance(snippet, str) and snippet.strip() else ""
    if max_chars is not None and len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


def _build_evidence_summary(state: ResearchState) -> str:
    """生成证据矩阵 Markdown 摘要。"""

    evidence_matrix = state.get("evidence_matrix", {})
    evidence_items = state.get("evidence_items", {})
    sub_questions = state.get("sub_questions", {})
    if not evidence_matrix:
        return "暂无证据矩阵。"

    sections = []
    for question_id, matrix_item in evidence_matrix.items():
        question_text = sub_questions.get(question_id, {}).get("question") or question_id
        priority = sub_questions.get(question_id, {}).get("priority") or "unknown"
        sections.append(f"### {question_id}｜{question_text}")
        sections.append(f"优先级：{priority}")
        sections.append(
            f"覆盖来源类型：{', '.join(matrix_item['covered_source_types']) or '无'}"
        )
        sections.append("")
        if not matrix_item["evidence_ids"]:
            sections.append("- 暂无证据")
        for evidence_id in matrix_item["evidence_ids"]:
            evidence = evidence_items[evidence_id]
            sections.append(
                "- "
                f"[{evidence_id}] {evidence['title']} "
                f"({evidence['source_type']} / {evidence['source_name']})："
                f"{_evidence_display_text(evidence, max_chars=EVIDENCE_CATALOG_MAX_CHARS)}"
            )
        sections.append("")

    return "\n".join(sections).strip()


def _build_question_status_summary(state: ResearchState) -> str:
    """生成子问题证据状态摘要。"""

    statuses = state.get("evidence_sufficiency_result", {}).get("question_status") or {}
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


def _build_sub_questions_summary(state: ResearchState) -> str:
    """子问题目录摘要。"""

    sub_questions = state.get("sub_questions", {})
    if not sub_questions:
        return "无子问题。"
    lines = []
    for question_id, item in sub_questions.items():
        lines.append(
            f"- {question_id}（{item.get('priority', 'unknown')}）：{item.get('question', '')}"
        )
    return "\n".join(lines)


def _build_conflicts_summary(state: ResearchState) -> str:
    """冲突列表摘要。"""

    conflicts = state.get("conflicts", {})
    if not conflicts:
        return "无语义冲突。"
    lines = []
    for conflict in conflicts.values():
        lines.append(
            f"- {conflict['conflict_id']} / {conflict['question_id']}："
            f"{conflict['conflict_summary']}；"
            f"preferred={conflict.get('preferred_evidence_id')}"
        )
    return "\n".join(lines)


def _build_evidence_sufficiency_summary(state: ResearchState) -> str:
    """证据充足性 JSON 摘要。"""

    result = state.get("evidence_sufficiency_result")
    if not result:
        return "未计算"
    compact = {
        "sufficient": result.get("sufficient"),
        "overall_score": result.get("overall_score"),
        "threshold": result.get("threshold"),
        "high_priority_all_met": result.get("high_priority_all_met"),
        "insufficient_question_ids": result.get("insufficient_question_ids", []),
        "degradation_reason": result.get("degradation_reason"),
    }
    return json.dumps(compact, ensure_ascii=False)


def _build_safety_revision_guidance(state: ResearchState) -> str:
    """Safety 再入时的保守改写指引。"""

    safety_result = state.get("safety_review_result")
    if not safety_result:
        return "无"
    if safety_result.get("safety_pass") is not False and not safety_result.get(
        "downgrade_required"
    ):
        return "无"
    risks = safety_result.get("detected_risks") or []
    reason = safety_result.get("downgrade_reason") or "安全审查要求保守输出"
    risk_text = "、".join(risks) if risks else "未列出具体风险标签"
    return (
        f"原因：{reason}；风险：{risk_text}。"
        "请删除或弱化高风险确定性建议，明确不可执行边界，保留证据引用。"
    )


def _normalize_cited_evidence_ids(
    cited_evidence_ids: list[str],
    evidence_items: dict[str, Any],
    limitations: list[str],
) -> tuple[list[str], list[str]]:
    """过滤非法引用 ID，并追加 limitation 说明。"""

    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for evidence_id in cited_evidence_ids:
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            continue
        cleaned = evidence_id.strip()
        if cleaned in seen:
            continue
        seen.add(cleaned)
        if cleaned in evidence_items:
            valid.append(cleaned)
        else:
            invalid.append(cleaned)
    updated_limitations = list(limitations)
    if invalid:
        updated_limitations.append(
            "已剔除不存在的证据引用：" + ", ".join(invalid)
        )
    return valid, updated_limitations


def _apply_citation_usage(
    evidence_items: dict[str, Any],
    entity_index: dict[str, Any] | None,
    cited_evidence_ids: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """按实际引用更新 used_in_final_report 与 used_evidence_ids。"""

    cited_set = set(cited_evidence_ids)
    updated_items = {
        evidence_id: {
            **item,
            "used_in_final_report": evidence_id in cited_set,
        }
        for evidence_id, item in evidence_items.items()
    }
    base_index = dict(entity_index or {})
    base_index["used_evidence_ids"] = list(cited_evidence_ids)
    return updated_items, base_index


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
    """LLM 生成 Markdown 研究报告，并消费上一轮 Review 建议。"""

    user_query = state.get("user_query", "")
    research_goal = state.get("research_goal", user_query)
    degraded = state.get("degraded", False)
    evidence_items = state.get("evidence_items", {})
    previous_review = state.get("review_result", {})
    revision_suggestions = previous_review.get("revision_suggestions") or []

    review_revision_count = state.get("review_revision_count", 0)
    if previous_review and previous_review.get("passed") is False:
        review_revision_count += 1

    prompt = load_prompt("research_report.yml")
    user_prompt = render_prompt_template(
        prompt["user_prompt_template"],
        {
            "user_query": user_query,
            "research_goal": research_goal,
            "degraded": str(bool(degraded)).lower(),
            "degradation_reason": state.get("degradation_reason") or "无",
            "evidence_sufficiency_summary": _build_evidence_sufficiency_summary(state),
            "sub_questions_summary": _build_sub_questions_summary(state),
            "evidence_catalog": _build_evidence_summary(state),
            "conflicts_summary": _build_conflicts_summary(state),
            "revision_suggestions": _format_list(
                [str(item) for item in revision_suggestions]
            ),
            "safety_revision_guidance": _build_safety_revision_guidance(state),
        },
    )
    model = build_chat_model("research_report").with_structured_output(
        ResearchReportOutput
    )
    output = model.invoke(
        [
            {"role": "system", "content": prompt["system_prompt"]},
            {"role": "user", "content": user_prompt},
        ]
    )
    if not isinstance(output, ResearchReportOutput):
        output = ResearchReportOutput.model_validate(output)

    cited_evidence_ids, limitations = _normalize_cited_evidence_ids(
        output.cited_evidence_ids,
        evidence_items,
        list(output.limitations),
    )
    report_markdown = output.report_markdown.strip()
    if limitations and "不确定性与边界" not in report_markdown:
        report_markdown = (
            report_markdown
            + "\n\n## 不确定性与边界\n\n"
            + _format_list(limitations)
        )
    elif limitations:
        report_markdown = report_markdown + "\n\n" + _format_list(limitations)

    updated_items, updated_index = _apply_citation_usage(
        evidence_items,
        state.get("entity_index"),
        cited_evidence_ids,
    )

    result: dict[str, Any] = {
        **record_node(state, "generate_research_report"),
        "report_draft": report_markdown,
        "degraded": degraded,
        "degradation_reason": state.get("degradation_reason"),
        "review_revision_count": review_revision_count,
        "entity_index": updated_index,
    }
    if evidence_items:
        result["evidence_items"] = updated_items
    return result


def review_research_report(state: ResearchState) -> dict[str, Any]:
    """LLM 对研究报告做质量 Review。"""

    degraded = state.get("degraded", False)
    if degraded:
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

    workflow_config = get_workflow_config()
    prompt = load_prompt("research_report_review.yml")
    evidence_items = state.get("evidence_items", {})
    user_prompt = render_prompt_template(
        prompt["user_prompt_template"],
        {
            "review_pass_score": str(workflow_config.review_pass_score),
            "evidence_sufficiency_summary": _build_evidence_sufficiency_summary(state),
            "available_evidence_ids": ", ".join(sorted(evidence_items.keys())) or "无",
            "report_draft": state["report_draft"],
        },
    )
    model = build_chat_model("research_report_review").with_structured_output(
        ResearchReportReviewOutput
    )
    output = model.invoke(
        [
            {"role": "system", "content": prompt["system_prompt"]},
            {"role": "user", "content": user_prompt},
        ]
    )
    if not isinstance(output, ResearchReportReviewOutput):
        output = ResearchReportReviewOutput.model_validate(output)

    report_score = max(0.0, min(float(output.report_score), 1.0))
    passed = bool(output.passed) and report_score >= workflow_config.review_pass_score
    if output.passed and report_score < workflow_config.review_pass_score:
        passed = False

    return {
        **record_node(state, "review_research_report"),
        "review_result": {
            "passed": passed,
            "report_score": report_score,
            "source_coverage_score": max(
                0.0, min(float(output.source_coverage_score), 1.0)
            ),
            "citation_completeness_score": max(
                0.0, min(float(output.citation_completeness_score), 1.0)
            ),
            "groundedness_score": max(0.0, min(float(output.groundedness_score), 1.0)),
            "boundary_score": max(0.0, min(float(output.boundary_score), 1.0)),
            "over_inference_risk": max(0.0, min(float(output.over_inference_risk), 1.0)),
            "revision_suggestions": list(output.revision_suggestions),
        },
    }
