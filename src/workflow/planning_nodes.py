"""研究规划与检索任务生成节点。"""

from __future__ import annotations

import json
from typing import Any

from src.config.settings import get_workflow_config
from src.llm.chat import build_chat_model
from src.llm.prompt_loader import load_prompt, render_prompt_template
from src.llm.structured_outputs import (
    ResearchPlanWithSearchTasksOutput,
    SearchTaskPlanOutput,
)
from src.schemas.state import ResearchState
from src.workflow.node_utils import increase_search_step


def _to_json(value: Any) -> str:
    """稳定序列化为 JSON 字符串。"""

    return json.dumps(value, ensure_ascii=False, indent=2)


def _validate_research_plan(plan: ResearchPlanWithSearchTasksOutput) -> None:
    """校验研究计划内部 ID 一致性。"""

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
    """校验检索任务引用的 question_id。"""

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

    model = build_chat_model("research_planner").with_structured_output(
        ResearchPlanWithSearchTasksOutput
    )
    plan = model.invoke(
        [
            ("system", prompt["system_prompt"]),
            ("human", user_prompt),
        ]
    )
    _validate_research_plan(plan)

    sub_questions = {item.question_id: item.model_dump() for item in plan.sub_questions}
    _validate_search_tasks(sub_questions, plan)

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
        "required_source_types": plan.required_source_types,
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
        "previous_search_tasks": state.get("search_tasks", []),
        "search_tasks": [task.model_dump() for task in plan.search_tasks],
        "planning_mode": "initial",
        "search_dispatch_mode": "initial",
        "search_attempt": state.get("search_steps", 0) + 1,
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
    previous_search_tasks = state.get("search_tasks", [])
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

    model = build_chat_model("search_task_planner").with_structured_output(
        SearchTaskPlanOutput
    )
    task_plan = model.invoke(
        [
            ("system", prompt["system_prompt"]),
            ("human", user_prompt),
        ]
    )
    _validate_search_tasks(sub_questions, task_plan)
    search_task_ids_by_question_id = _build_search_task_index(task_plan)

    entity_index = {
        **state["entity_index"],
        "search_task_ids_by_question_id": search_task_ids_by_question_id,
    }

    return {
        "previous_search_tasks": previous_search_tasks,
        "search_tasks": [task.model_dump() for task in task_plan.search_tasks],
        "entity_index": entity_index,
        "active_question_ids": task_plan.active_question_ids_after_dispatch,
        "search_attempt": attempt,
        "planning_mode": "iteration",
        "search_dispatch_mode": "iteration",
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
