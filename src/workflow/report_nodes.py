"""报告生成、降级准备与质量 Review 节点。"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from src.artifacts.evidence_content import load_evidence_content
from src.config.settings import get_workflow_config
from src.llm.chat import build_chat_model
from src.llm.prompt_loader import load_prompt, render_prompt_template
from src.llm.structured_outputs import ResearchReportOutput, ResearchReportReviewOutput
from src.schemas.state import ResearchState
from src.workflow.node_utils import record_node


DEFAULT_REPORT_EVIDENCE_SNIPPET_MAX_CHARS = 300
DEFAULT_REPORT_EVIDENCE_BODY_MAX_CHARS = 2500
DEFAULT_REPORT_EVIDENCE_BODIES_PER_QUESTION = 2
DEFAULT_REPORT_EVIDENCE_BODIES_TOTAL_CHARS = 28000
MIN_LONG_TEXT_CHARS = 400


def _read_positive_int_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    """读取正整数环境变量并夹紧到区间。"""

    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _report_evidence_snippet_max_chars() -> int:
    return _read_positive_int_env(
        "REPORT_EVIDENCE_SNIPPET_MAX_CHARS",
        default=DEFAULT_REPORT_EVIDENCE_SNIPPET_MAX_CHARS,
        minimum=100,
        maximum=1000,
    )


def _report_evidence_body_max_chars() -> int:
    return _read_positive_int_env(
        "REPORT_EVIDENCE_BODY_MAX_CHARS",
        default=DEFAULT_REPORT_EVIDENCE_BODY_MAX_CHARS,
        minimum=500,
        maximum=8000,
    )


def _report_evidence_bodies_per_question() -> int:
    return _read_positive_int_env(
        "REPORT_EVIDENCE_BODIES_PER_QUESTION",
        default=DEFAULT_REPORT_EVIDENCE_BODIES_PER_QUESTION,
        minimum=1,
        maximum=5,
    )


def _report_evidence_bodies_total_chars() -> int:
    return _read_positive_int_env(
        "REPORT_EVIDENCE_BODIES_TOTAL_CHARS",
        default=DEFAULT_REPORT_EVIDENCE_BODIES_TOTAL_CHARS,
        minimum=5000,
        maximum=100000,
    )


def _format_list(items: list[str]) -> str:
    """格式化 Markdown 列表。"""

    if not items:
        return "- 无"
    return "\n".join(f"- {item}" for item in items)


def _truncate_text(text: str, max_chars: int) -> str:
    """截断文本并加省略标记。"""

    compact = text.strip()
    if max_chars <= 0 or len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "…"


def _evidence_short_blurb(evidence: dict[str, Any], *, max_chars: int) -> str:
    """目录短摘要：优先 snippet，避免为每条都读全文。"""

    snippet = evidence.get("snippet")
    if isinstance(snippet, str) and snippet.strip():
        return _truncate_text(snippet, max_chars)
    content = load_evidence_content(evidence.get("content_path"))
    if content.strip():
        return _truncate_text(content, max_chars)
    return ""


def _evidence_display_text(evidence: dict[str, Any], *, max_chars: int | None = None) -> str:
    """兼容展示：优先落盘长文，再回退短摘要。"""

    content = _evidence_long_text(evidence)
    if content:
        text = content
    else:
        snippet = evidence.get("snippet")
        text = snippet.strip() if isinstance(snippet, str) and snippet.strip() else ""
    if max_chars is not None:
        return _truncate_text(text, max_chars)
    return text


def _evidence_long_text(evidence: dict[str, Any]) -> str:
    """可读长文：仅来自 content_path 落盘内容。"""

    return load_evidence_content(evidence.get("content_path")).strip()


def _reliability(evidence: dict[str, Any]) -> float:
    try:
        return float(evidence.get("reliability_score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _select_evidence_bodies(
    state: ResearchState,
    *,
    per_question: int,
    body_max_chars: int,
    total_max_chars: int,
) -> list[tuple[str, str, str]]:
    """按题精选长文，再按总预算封顶。

    返回 [(question_id, evidence_id, truncated_body), ...]，已按可靠性优先。
    """

    evidence_matrix = state.get("evidence_matrix", {})
    evidence_items = state.get("evidence_items", {})
    ranked: list[tuple[float, str, str, str]] = []

    for question_id, matrix_item in evidence_matrix.items():
        candidates: list[tuple[float, str, str]] = []
        for evidence_id in matrix_item.get("evidence_ids") or []:
            evidence = evidence_items.get(evidence_id)
            if not isinstance(evidence, dict):
                continue
            long_text = _evidence_long_text(evidence)
            if len(long_text) < MIN_LONG_TEXT_CHARS:
                continue
            candidates.append((_reliability(evidence), evidence_id, long_text))
        candidates.sort(key=lambda item: item[0], reverse=True)
        for score, evidence_id, long_text in candidates[:per_question]:
            ranked.append((score, question_id, evidence_id, long_text))

    ranked.sort(key=lambda item: item[0], reverse=True)
    selected: list[tuple[str, str, str]] = []
    used_chars = 0
    for _score, question_id, evidence_id, long_text in ranked:
        remaining = total_max_chars - used_chars
        if remaining < MIN_LONG_TEXT_CHARS:
            break
        truncated = _truncate_text(long_text, min(body_max_chars, remaining))
        if len(truncated) < MIN_LONG_TEXT_CHARS:
            break
        selected.append((question_id, evidence_id, truncated))
        used_chars += len(truncated)
    return selected


def _build_evidence_summary(state: ResearchState) -> str:
    """生成报告用证据上下文：全量短目录 + 按题精选长文（总预算封顶）。"""

    evidence_matrix = state.get("evidence_matrix", {})
    evidence_items = state.get("evidence_items", {})
    sub_questions = state.get("sub_questions", {})
    if not evidence_matrix:
        return "暂无证据矩阵。"

    snippet_max = _report_evidence_snippet_max_chars()
    body_max = _report_evidence_body_max_chars()
    per_question = _report_evidence_bodies_per_question()
    total_max = _report_evidence_bodies_total_chars()

    catalog_sections: list[str] = ["## 证据短目录", ""]
    for question_id, matrix_item in evidence_matrix.items():
        question_text = sub_questions.get(question_id, {}).get("question") or question_id
        priority = sub_questions.get(question_id, {}).get("priority") or "unknown"
        catalog_sections.append(f"### {question_id}｜{question_text}")
        catalog_sections.append(f"优先级：{priority}")
        catalog_sections.append(
            f"覆盖来源类型：{', '.join(matrix_item['covered_source_types']) or '无'}"
        )
        catalog_sections.append("")
        if not matrix_item["evidence_ids"]:
            catalog_sections.append("- 暂无证据")
            catalog_sections.append("")
            continue
        for evidence_id in matrix_item["evidence_ids"]:
            evidence = evidence_items[evidence_id]
            url = evidence.get("url_or_path") or evidence.get("url") or ""
            blurb = _evidence_short_blurb(evidence, max_chars=snippet_max)
            catalog_sections.append(
                "- "
                f"[{evidence_id}] {evidence['title']} "
                f"({evidence['source_type']} / {evidence['source_name']})"
                + (f" | {url}" if url else "")
                + (f"：{blurb}" if blurb else "")
            )
        catalog_sections.append("")

    selected_bodies = _select_evidence_bodies(
        state,
        per_question=per_question,
        body_max_chars=body_max,
        total_max_chars=total_max,
    )
    body_sections: list[str] = [
        "## 精选证据正文",
        (
            f"规则：每题最多 {per_question} 条有落盘长文的高可靠性证据；"
            f"单条 ≤ {body_max} 字；长文合计 ≤ {total_max} 字。"
        ),
        "",
    ]
    if not selected_bodies:
        body_sections.append("无可用落盘长文；请主要依据短目录中的摘要撰写，并在 limitations 披露证据粒度不足。")
    else:
        for question_id, evidence_id, body in selected_bodies:
            evidence = evidence_items[evidence_id]
            body_sections.append(
                f"### [{evidence_id}] {evidence.get('title', evidence_id)} "
                f"（{question_id} / reliability={_reliability(evidence):.3f}）"
            )
            body_sections.append(body)
            body_sections.append("")

    return "\n".join([*catalog_sections, *body_sections]).strip()


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


def _parse_message_json_content(content: Any) -> dict[str, Any]:
    """从聊天消息 content 解析 JSON 对象，兼容 text blocks。"""

    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
        content = "\n".join(text_parts)
    if not isinstance(content, str):
        raise ValueError(f"无法解析报告 LLM 输出类型：{type(content).__name__}")
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("报告 LLM 输出必须是 JSON 对象")
    return parsed


def _first_non_empty_str(payload: dict[str, Any], keys: list[str]) -> str | None:
    """取首个非空字符串字段。"""

    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _coerce_string_list(value: Any) -> list[str]:
    """把常见 list/str 形态归一成字符串列表。"""

    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                items.append(item.strip())
            elif isinstance(item, dict):
                evidence_id = item.get("evidence_id") or item.get("id")
                if isinstance(evidence_id, str) and evidence_id.strip():
                    items.append(evidence_id.strip())
        return items
    return []


def _coerce_research_report_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """归一化报告生成 LLM 常见字段漂移。"""

    if not isinstance(payload, dict):
        raise ValueError("研究报告 LLM 输出必须是 JSON 对象")

    data = dict(payload)
    for wrapper_key in ("ResearchReportOutput", "output", "result", "data"):
        nested = data.get(wrapper_key)
        if isinstance(nested, dict):
            data = {**nested, **{k: v for k, v in data.items() if k != wrapper_key}}
            break

    report_markdown = _first_non_empty_str(
        data,
        [
            "report_markdown",
            "report",
            "markdown",
            "content",
            "body",
            "report_md",
            "research_report",
        ],
    )
    if report_markdown is None and isinstance(data.get("report"), dict):
        report_markdown = _first_non_empty_str(
            data["report"],
            ["report_markdown", "markdown", "content", "body", "text"],
        )

    cited = data.get("cited_evidence_ids")
    if cited is None:
        cited = data.get("citations")
    if cited is None:
        cited = data.get("evidence_ids")
    if cited is None:
        cited = data.get("cited_ids")

    limitations = data.get("limitations")
    if limitations is None:
        limitations = data.get("limits")
    if limitations is None:
        limitations = data.get("boundaries")

    coerced: dict[str, Any] = {
        "cited_evidence_ids": _coerce_string_list(cited),
        "limitations": _coerce_string_list(limitations),
    }
    if report_markdown is not None:
        coerced["report_markdown"] = report_markdown
    return coerced


def _coerce_research_report_review_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """归一化报告 Review LLM 常见字段漂移。"""

    if not isinstance(payload, dict):
        raise ValueError("报告 Review LLM 输出必须是 JSON 对象")

    data = dict(payload)
    for wrapper_key in ("ResearchReportReviewOutput", "output", "result", "data"):
        nested = data.get(wrapper_key)
        if isinstance(nested, dict):
            data = {**nested, **{k: v for k, v in data.items() if k != wrapper_key}}
            break

    suggestions = data.get("revision_suggestions")
    if suggestions is None:
        suggestions = data.get("suggestions")
    if suggestions is None:
        suggestions = data.get("revisions")

    coerced: dict[str, Any] = dict(data)
    coerced["revision_suggestions"] = _coerce_string_list(suggestions)
    return coerced


def _invoke_structured_json(
    *,
    model_role: str,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    """调用 LLM 并解析为 JSON 对象（json_object，兼容 content blocks）。"""

    model = build_chat_model(model_role).bind(
        response_format={"type": "json_object"}
    )
    response = model.invoke(
        [
            ("system", system_prompt),
            ("human", user_prompt),
        ]
    )
    return _parse_message_json_content(getattr(response, "content", response))


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


_LOCAL_EVIDENCE_SOURCE_TYPES = frozenset({"local_document", "structured_mock"})
_CITED_EVIDENCE_SECTION = "## 引用证据"


def _sort_evidence_ids(evidence_ids: list[str]) -> list[str]:
    """按 E 编号自然排序。"""

    def sort_key(evidence_id: str) -> tuple[int, int | str]:
        match = re.fullmatch(r"E(\d+)", evidence_id.strip())
        if match:
            return (0, int(match.group(1)))
        return (1, evidence_id)

    return sorted(evidence_ids, key=sort_key)


def _escape_markdown_link_text(text: str) -> str:
    """转义 Markdown 链接文本中的方括号。"""

    return text.replace("[", "\\[").replace("]", "\\]")


def _web_evidence_url(evidence: dict[str, Any]) -> str | None:
    """提取可用于超链接的 http(s) URL。"""

    for key in ("url_or_path", "url"):
        value = evidence.get(key)
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned.startswith(("http://", "https://")):
                return cleaned
    return None


def _format_cited_evidence_line(
    evidence_id: str,
    evidence: dict[str, Any],
    *,
    as_web: bool,
) -> str:
    """格式化单条引用证据：只含代号与 title。"""

    title = str(evidence.get("title") or evidence_id).strip() or evidence_id
    if as_web:
        url = _web_evidence_url(evidence)
        if url:
            return f"- [{evidence_id}][{_escape_markdown_link_text(title)}]({url})"
    return f"- [{evidence_id}] {title}"


def build_cited_evidence_appendix(state: ResearchState | dict[str, Any]) -> str:
    """按实际引用生成报告末尾证据列表（代码侧，不经 LLM）。"""

    evidence_items = state.get("evidence_items") or {}
    if not isinstance(evidence_items, dict) or not evidence_items:
        return ""

    used_ids = state.get("entity_index", {}).get("used_evidence_ids") or []
    if not isinstance(used_ids, list):
        used_ids = []
    cited_ids = _sort_evidence_ids(
        [
            evidence_id
            for evidence_id in used_ids
            if isinstance(evidence_id, str) and evidence_id in evidence_items
        ]
    )
    if not cited_ids:
        return ""

    web_lines: list[str] = []
    local_lines: list[str] = []
    for evidence_id in cited_ids:
        evidence = evidence_items[evidence_id]
        source_type = str(evidence.get("source_type") or "")
        if source_type in _LOCAL_EVIDENCE_SOURCE_TYPES:
            local_lines.append(
                _format_cited_evidence_line(evidence_id, evidence, as_web=False)
            )
        else:
            web_lines.append(
                _format_cited_evidence_line(evidence_id, evidence, as_web=True)
            )

    sections = [_CITED_EVIDENCE_SECTION, ""]
    if web_lines:
        sections.extend(["### Web 证据", "", *web_lines, ""])
    if local_lines:
        sections.extend(["### Local 证据", "", *local_lines, ""])
    return "\n".join(sections).rstrip() + "\n"


def append_cited_evidence_appendix(
    report_markdown: str,
    state: ResearchState | dict[str, Any],
) -> str:
    """把引用证据附录追加到报告末尾；已存在同名章节时不重复追加。"""

    report = (report_markdown or "").rstrip()
    if not report:
        return report_markdown or ""
    if re.search(rf"(?m)^{_CITED_EVIDENCE_SECTION}\s*$", report):
        return report + "\n"
    appendix = build_cited_evidence_appendix(state)
    if not appendix:
        return report + "\n"
    return report + "\n\n" + appendix


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
    payload = _invoke_structured_json(
        model_role="research_report",
        system_prompt=prompt["system_prompt"],
        user_prompt=user_prompt,
    )
    output = ResearchReportOutput.model_validate(
        _coerce_research_report_dict(payload)
    )

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
            "user_query": state.get("user_query") or "",
            "research_goal": state.get("research_goal") or "",
            "review_pass_score": str(workflow_config.review_pass_score),
            "evidence_sufficiency_summary": _build_evidence_sufficiency_summary(state),
            "available_evidence_ids": ", ".join(sorted(evidence_items.keys())) or "无",
            "report_draft": state["report_draft"],
        },
    )
    payload = _invoke_structured_json(
        model_role="research_report_review",
        system_prompt=prompt["system_prompt"],
        user_prompt=user_prompt,
    )
    output = ResearchReportReviewOutput.model_validate(
        _coerce_research_report_review_dict(payload)
    )

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
