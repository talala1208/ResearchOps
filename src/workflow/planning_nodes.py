"""研究规划与检索任务生成节点。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

from src.config.settings import get_workflow_config
from src.llm.chat import build_chat_model
from src.llm.prompt_loader import load_prompt, render_prompt_template
from src.llm.structured_outputs import (
    ResearchPlanWithSearchTasksOutput,
    SearchTaskPlanOutput,
)
from src.schemas.state import ResearchState
from src.workflow.node_utils import increase_search_step


T = TypeVar("T")
PLANNER_RETRY_ERROR_MAX_CHARS = 1500
SEARCH_PROVIDER_LOCAL = "local_document_search"
SEARCH_PROVIDER_WEB = "web_search"


def _to_json(value: Any) -> str:
    """稳定序列化为 JSON 字符串。"""

    return json.dumps(value, ensure_ascii=False, indent=2)


def _coerce_search_provider(value: Any) -> str:
    """统一 search_provider：local / local_* → local_document_search，其它 → web_search。"""

    if not isinstance(value, str):
        return SEARCH_PROVIDER_WEB
    normalized = value.strip().lower()
    if normalized == "local" or normalized.startswith("local_"):
        return SEARCH_PROVIDER_LOCAL
    if normalized == SEARCH_PROVIDER_WEB:
        return SEARCH_PROVIDER_WEB
    return SEARCH_PROVIDER_WEB


def _coerce_search_task_items(
    tasks: Any,
    *,
    default_attempt: int = 1,
) -> list[dict[str, Any]]:
    """归一 search_tasks：补 attempt，并映射 search_provider。"""

    if not isinstance(tasks, list):
        return []
    coerced_tasks: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        attempt = task.get("attempt", default_attempt)
        if not isinstance(attempt, int) or attempt < 1:
            attempt = default_attempt
        coerced_tasks.append(
            {
                **task,
                "attempt": attempt,
                "search_provider": _coerce_search_provider(task.get("search_provider")),
            }
        )
    return coerced_tasks


def _coerce_search_task_plan_dict(
    payload: dict[str, Any],
    *,
    default_attempt: int = 1,
) -> dict[str, Any]:
    """将迭代规划 LLM 输出归一到 SearchTaskPlanOutput。"""

    if not isinstance(payload, dict):
        raise ValueError("检索任务规划 LLM 输出必须是 JSON 对象")

    data = dict(payload)
    nested = data.pop("search_task_plan", None)
    if isinstance(nested, dict):
        for key, value in nested.items():
            if key not in data:
                data[key] = value

    data["search_tasks"] = _coerce_search_task_items(
        data.get("search_tasks") or [],
        default_attempt=default_attempt,
    )
    if "active_question_ids_after_dispatch" not in data:
        data["active_question_ids_after_dispatch"] = []
    elif not isinstance(data["active_question_ids_after_dispatch"], list):
        data["active_question_ids_after_dispatch"] = []
    return data


def _format_planner_retry_error(error: BaseException) -> str:
    """截断校验错误摘要，供重试 Prompt 使用。"""

    summary = str(error).strip() or type(error).__name__
    if len(summary) > PLANNER_RETRY_ERROR_MAX_CHARS:
        return summary[:PLANNER_RETRY_ERROR_MAX_CHARS] + "…"
    return summary


def _append_validation_retry_hint(user_prompt: str, error: BaseException) -> str:
    """把上次校验失败原因追加到 user prompt。"""

    return (
        f"{user_prompt}\n\n"
        "上次输出未通过校验，请根据以下错误修正后重新输出合法 JSON 对象，不要解释：\n"
        f"{_format_planner_retry_error(error)}"
    )


def _invoke_planner_json(
    *,
    model_role: str,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    """调用规划类 LLM，要求返回 JSON 对象。"""

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


def _invoke_planner_with_validation_retry(
    *,
    model_role: str,
    system_prompt: str,
    user_prompt: str,
    parse_and_validate: Callable[[dict[str, Any]], T],
) -> T:
    """规划 LLM 调用：校验失败时带错误摘要重试 1 次。"""

    try:
        payload = _invoke_planner_json(
            model_role=model_role,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return parse_and_validate(payload)
    except Exception as first_error:
        retry_prompt = _append_validation_retry_hint(user_prompt, first_error)
        payload = _invoke_planner_json(
            model_role=model_role,
            system_prompt=system_prompt,
            user_prompt=retry_prompt,
        )
        return parse_and_validate(payload)


def _dedupe_by_question_id(items: list[T]) -> list[T]:
    """按 question_id 去重，后写覆盖先写（修复 LLM 偶发重复条目）。"""

    by_id: dict[str, T] = {}
    order: list[str] = []
    for item in items:
        question_id = getattr(item, "question_id", None)
        if not isinstance(question_id, str) or not question_id.strip():
            raise ValueError("研究计划条目缺少合法 question_id")
        if question_id not in by_id:
            order.append(question_id)
        by_id[question_id] = item
    return [by_id[question_id] for question_id in order]


def _source_types_by_question_id(payload: dict[str, Any]) -> dict[str, list[str]]:
    """从标准与任务中汇总每个 question_id 的来源类型。"""

    by_question: dict[str, list[str]] = {}
    for item in payload.get("minimum_evidence_standard") or []:
        if not isinstance(item, dict):
            continue
        question_id = item.get("question_id")
        if not isinstance(question_id, str):
            continue
        sources = item.get("must_include_source_types") or item.get(
            "required_source_types"
        ) or []
        if isinstance(sources, list):
            by_question.setdefault(question_id, [])
            for source in sources:
                if isinstance(source, str) and source not in by_question[question_id]:
                    by_question[question_id].append(source)
    for task in payload.get("search_tasks") or []:
        if not isinstance(task, dict):
            continue
        question_id = task.get("question_id")
        source_type = task.get("source_type")
        if isinstance(question_id, str) and isinstance(source_type, str):
            by_question.setdefault(question_id, [])
            if source_type not in by_question[question_id]:
                by_question[question_id].append(source_type)
    return by_question


def _coerce_authority_level(value: Any) -> str:
    """将权威等级归一为 high/medium/low。"""

    if not isinstance(value, str):
        return "medium"
    normalized = value.strip().lower()
    aliases = {
        "high": "high",
        "medium": "medium",
        "low": "low",
        "official": "high",
        "official_docs": "high",
        "权威": "high",
        "官方": "high",
        "community": "low",
        "blog": "low",
        "user": "low",
        "社区": "low",
    }
    return aliases.get(normalized, "medium")


def _coerce_research_plan_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """将 LLM 常见漂移结构归一到 ResearchPlanWithSearchTasksOutput。

    已覆盖：
    - 顶层包一层 research_plan，并用 topic 代替 research_goal
    - sub_questions 缺少 required_source_types
    - expected_evidence / minimum_evidence_standard 字段名漂移或缺省
    - required_authority_level 写成 official/community 等别名
    - search_tasks 缺少 attempt；search_provider 统一映射
    """

    if not isinstance(payload, dict):
        raise ValueError("研究计划 LLM 输出必须是 JSON 对象")

    data = dict(payload)
    nested = data.pop("research_plan", None)
    if isinstance(nested, dict):
        for key, value in nested.items():
            if key not in data:
                data[key] = value
        if "research_goal" not in data:
            data["research_goal"] = (
                nested.get("research_goal")
                or nested.get("topic")
                or nested.get("goal")
            )

    if "research_goal" not in data or not data.get("research_goal"):
        topic = data.pop("topic", None)
        if isinstance(topic, str) and topic.strip():
            data["research_goal"] = topic

    source_by_question = _source_types_by_question_id(data)

    coerced_sub_questions: list[dict[str, Any]] = []
    for item in data.get("sub_questions") or []:
        if not isinstance(item, dict):
            continue
        question_id = item.get("question_id")
        sources = item.get("required_source_types")
        if not isinstance(sources, list) or not sources:
            sources = list(source_by_question.get(question_id, [])) or ["blog"]
        coerced_sub_questions.append(
            {
                **item,
                "required_source_types": sources,
            }
        )
    data["sub_questions"] = coerced_sub_questions

    coerced_expected: list[dict[str, Any]] = []
    for item in data.get("expected_evidence") or []:
        if not isinstance(item, dict):
            continue
        question_id = item.get("question_id")
        sources = item.get("required_source_types")
        if not isinstance(sources, list) or not sources:
            sources = list(source_by_question.get(question_id, [])) or ["blog"]
        minimum_count = item.get("minimum_count")
        if not isinstance(minimum_count, int) or minimum_count < 1:
            minimum_count = 1
        authority = _coerce_authority_level(item.get("required_authority_level"))
        coerced_expected.append(
            {
                **item,
                "minimum_count": minimum_count,
                "required_source_types": sources,
                "required_authority_level": authority,
            }
        )
    data["expected_evidence"] = coerced_expected

    coerced_standards: list[dict[str, Any]] = []
    for item in data.get("minimum_evidence_standard") or []:
        if not isinstance(item, dict):
            continue
        question_id = item.get("question_id")
        min_total = item.get("min_total_evidence", item.get("minimum_count"))
        if not isinstance(min_total, int) or min_total < 1:
            min_total = 1
        min_high = item.get("min_high_quality_sources", 0)
        if not isinstance(min_high, int) or min_high < 0:
            min_high = 0
        must_include = item.get("must_include_source_types") or item.get(
            "required_source_types"
        )
        if not isinstance(must_include, list) or not must_include:
            must_include = list(source_by_question.get(question_id, [])) or ["blog"]
        allow_degraded = item.get("allow_degraded_answer")
        if not isinstance(allow_degraded, bool):
            allow_degraded = True
        coerced_standards.append(
            {
                **item,
                "min_total_evidence": min_total,
                "min_high_quality_sources": min_high,
                "must_include_source_types": must_include,
                "allow_degraded_answer": allow_degraded,
            }
        )
    data["minimum_evidence_standard"] = coerced_standards

    coerced_tasks = _coerce_search_task_items(data.get("search_tasks") or [])
    data["search_tasks"] = coerced_tasks

    required_source_types = data.get("required_source_types")
    if not isinstance(required_source_types, list) or not required_source_types:
        collected: list[str] = []
        for item in coerced_sub_questions:
            for source in item.get("required_source_types") or []:
                if isinstance(source, str) and source not in collected:
                    collected.append(source)
        for task in coerced_tasks:
            source = task.get("source_type")
            if isinstance(source, str) and source not in collected:
                collected.append(source)
        data["required_source_types"] = collected or ["blog"]

    if "active_question_ids_after_dispatch" not in data:
        data["active_question_ids_after_dispatch"] = [
            item["question_id"]
            for item in coerced_sub_questions
            if isinstance(item.get("question_id"), str)
        ]

    return data


def _parse_message_json_content(content: Any) -> dict[str, Any]:
    """从聊天消息 content 解析 JSON 对象。"""

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
        raise ValueError(f"无法解析研究计划输出类型：{type(content).__name__}")
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
        raise ValueError("研究计划 LLM 输出必须是 JSON 对象")
    return parsed


def _build_research_plan_from_payload(
    payload: dict[str, Any],
) -> ResearchPlanWithSearchTasksOutput:
    """coerce → validate → normalize → 计划与任务 ID 校验。"""

    coerced = _coerce_research_plan_dict(payload)
    plan = ResearchPlanWithSearchTasksOutput.model_validate(coerced)
    plan = _normalize_research_plan(plan)
    _validate_research_plan(plan)
    sub_questions = {item.question_id: item.model_dump() for item in plan.sub_questions}
    _validate_search_tasks(sub_questions, plan)
    return plan


def _build_search_task_plan_from_payload(
    payload: dict[str, Any],
    *,
    sub_questions: dict[str, Any],
    default_attempt: int = 1,
) -> SearchTaskPlanOutput:
    """coerce → validate → 任务 question_id 校验。"""

    coerced = _coerce_search_task_plan_dict(
        payload,
        default_attempt=default_attempt,
    )
    task_plan = SearchTaskPlanOutput.model_validate(coerced)
    _validate_search_tasks(sub_questions, task_plan)
    return task_plan


def _invoke_research_plan(
    *,
    system_prompt: str,
    user_prompt: str,
) -> ResearchPlanWithSearchTasksOutput:
    """调用初始规划 LLM；校验失败时带错误摘要重试 1 次。"""

    return _invoke_planner_with_validation_retry(
        model_role="research_planner",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        parse_and_validate=_build_research_plan_from_payload,
    )


def _invoke_search_task_plan(
    *,
    system_prompt: str,
    user_prompt: str,
    sub_questions: dict[str, Any],
    default_attempt: int,
) -> SearchTaskPlanOutput:
    """调用迭代规划 LLM；复用 coerce，校验失败时带错误摘要重试 1 次。"""

    def _parse(payload: dict[str, Any]) -> SearchTaskPlanOutput:
        return _build_search_task_plan_from_payload(
            payload,
            sub_questions=sub_questions,
            default_attempt=default_attempt,
        )

    return _invoke_planner_with_validation_retry(
        model_role="search_task_planner",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        parse_and_validate=_parse,
    )


def _normalize_research_plan(
    plan: ResearchPlanWithSearchTasksOutput,
) -> ResearchPlanWithSearchTasksOutput:
    """规范化规划输出：对 expected / standard 列表按 question_id 去重。"""

    return plan.model_copy(
        update={
            "expected_evidence": _dedupe_by_question_id(plan.expected_evidence),
            "minimum_evidence_standard": _dedupe_by_question_id(
                plan.minimum_evidence_standard
            ),
        }
    )


def _validate_research_plan(plan: ResearchPlanWithSearchTasksOutput) -> None:
    """校验研究计划内部 ID 一致性。

    `expected_evidence` / `minimum_evidence_standard` 应先经 `_normalize_research_plan`
    去重；此处仍拒绝 sub_questions 重复，并要求三组 ID 集合完全一致。
    """

    question_ids = [item.question_id for item in plan.sub_questions]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError(f"研究计划中存在重复 question_id：{question_ids}")

    expected_ids = [item.question_id for item in plan.expected_evidence]
    if len(expected_ids) != len(set(expected_ids)):
        raise ValueError(f"expected_evidence 中存在重复 question_id：{expected_ids}")

    standard_ids = [item.question_id for item in plan.minimum_evidence_standard]
    if len(standard_ids) != len(set(standard_ids)):
        raise ValueError(f"minimum_evidence_standard 中存在重复 question_id：{standard_ids}")

    question_id_set = set(question_ids)
    expected_id_set = set(expected_ids)
    standard_id_set = set(standard_ids)

    if expected_id_set != question_id_set:
        missing = sorted(question_id_set - expected_id_set)
        extra = sorted(expected_id_set - question_id_set)
        raise ValueError(
            "expected_evidence 的 question_id 必须与 sub_questions 完全一致；"
            f"sub_questions={sorted(question_id_set)}；"
            f"expected_evidence={sorted(expected_id_set)}；"
            f"missing={missing}；extra={extra}"
        )
    if standard_id_set != question_id_set:
        missing = sorted(question_id_set - standard_id_set)
        extra = sorted(standard_id_set - question_id_set)
        raise ValueError(
            "minimum_evidence_standard 的 question_id 必须与 sub_questions 完全一致；"
            f"sub_questions={sorted(question_id_set)}；"
            f"minimum_evidence_standard={sorted(standard_id_set)}；"
            f"missing={missing}；extra={extra}"
        )


def _validate_search_tasks(
    sub_questions: dict[str, Any],
    task_plan: SearchTaskPlanOutput,
) -> None:
    """校验检索任务非空，且引用的 question_id 合法。"""

    if not task_plan.search_tasks:
        raise ValueError("search_tasks 不能为空")

    task_ids = [task.task_id for task in task_plan.search_tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("检索任务中存在重复 task_id")

    for task in task_plan.search_tasks:
        if task.question_id not in sub_questions:
            raise ValueError(f"检索任务引用了不存在的 question_id：{task.question_id}")


def _build_search_task_index(task_plan: SearchTaskPlanOutput) -> dict[str, list[str]]:
    """构建 question_id 到 task_id 的索引。"""

    search_task_ids_by_question_id: dict[str, list[str]] = {}
    for task in task_plan.search_tasks:
        search_task_ids_by_question_id.setdefault(task.question_id, []).append(task.task_id)
    return search_task_ids_by_question_id


def _run_initial_planning(state: ResearchState) -> dict[str, Any]:
    """初始模式：一次性生成研究计划和首轮检索任务。"""

    user_query = state["user_query"]
    prompt = load_prompt("research_planner.yml")
    user_prompt = render_prompt_template(
        prompt["user_prompt_template"],
        {"user_query": user_query},
    )

    plan = _invoke_research_plan(
        system_prompt=prompt["system_prompt"],
        user_prompt=user_prompt,
    )

    sub_questions = {item.question_id: item.model_dump() for item in plan.sub_questions}
    expected_evidence = {
        item.question_id: item.model_dump() for item in plan.expected_evidence
    }
    minimum_evidence_standard = {
        item.question_id: item.model_dump() for item in plan.minimum_evidence_standard
    }
    question_ids = list(sub_questions.keys())
    workflow_config = get_workflow_config()
    search_task_ids_by_question_id = _build_search_task_index(plan)

    return {
        "research_goal": plan.research_goal,
        "sub_questions": sub_questions,
        "expected_evidence": expected_evidence,
        "minimum_evidence_standard": minimum_evidence_standard,
        "entity_index": {
            "question_ids": question_ids,
            "evidence_ids_by_question_id": {},
            "conflict_ids_by_question_id": {},
            "search_task_ids_by_question_id": search_task_ids_by_question_id,
            "used_evidence_ids": [],
        },
        "active_question_ids": plan.active_question_ids_after_dispatch or question_ids,
        "search_tasks": [task.model_dump() for task in plan.search_tasks],
        "planning_mode": "initial",
        "search_iteration_context": {},
        "search_steps": state.get("search_steps", 0),
        "max_search_steps": state.get(
            "max_search_steps", workflow_config.max_search_steps
        ),
        "review_revision_count": state.get("review_revision_count", 0),
        "max_review_revisions": state.get(
            "max_review_revisions", workflow_config.max_review_revisions
        ),
        "safety_revision_count": state.get("safety_revision_count", 0),
        "max_safety_revisions": state.get(
            "max_safety_revisions", workflow_config.max_safety_revisions
        ),
    }


def _run_iteration_planning(state: ResearchState) -> dict[str, Any]:
    """迭代模式：只为证据不足的子问题生成下一轮检索任务。"""

    sub_questions = state["sub_questions"]
    minimum_evidence_standard = state["minimum_evidence_standard"]
    active_question_ids = state.get("active_question_ids", list(sub_questions.keys()))
    search_iteration_context = state.get("search_iteration_context", {})
    attempt = state.get("search_steps", 0) + 1

    prompt = load_prompt("search_task_planner.yml")
    user_prompt = render_prompt_template(
        prompt["user_prompt_template"],
        {
            "research_goal": state["research_goal"],
            "sub_questions_json": _to_json(sub_questions),
            "minimum_evidence_standard_json": _to_json(minimum_evidence_standard),
            "active_question_ids_json": _to_json(active_question_ids),
            "attempt": attempt,
            "dispatch_mode": "iteration",
            "search_iteration_context_json": _to_json(search_iteration_context),
        },
    )

    task_plan = _invoke_search_task_plan(
        system_prompt=prompt["system_prompt"],
        user_prompt=user_prompt,
        sub_questions=sub_questions,
        default_attempt=attempt,
    )
    search_task_ids_by_question_id = _build_search_task_index(task_plan)

    entity_index = {
        **state["entity_index"],
        "search_task_ids_by_question_id": search_task_ids_by_question_id,
    }

    return {
        "search_tasks": [task.model_dump() for task in task_plan.search_tasks],
        "entity_index": entity_index,
        "active_question_ids": task_plan.active_question_ids_after_dispatch,
        "planning_mode": "iteration",
    }


def plan_research(state: ResearchState) -> dict[str, Any]:
    """研究规划节点，根据 planning_mode 选择初始或迭代 Prompt。"""

    planning_mode = state.get("planning_mode", "initial")
    if planning_mode == "iteration":
        updates = _run_iteration_planning(state)
    else:
        updates = _run_initial_planning(state)

    return {
        **updates,
        **increase_search_step(state, "plan_research"),
    }
