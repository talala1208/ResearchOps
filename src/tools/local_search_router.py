"""本地资料检索工具路由。

规划器只输出伞类型 `local`；本模块对本轮全部伞任务只调用一次
`local_document_search` 模型，按 task_id 选定具体本地工具并同轮产出入参。
"""

from __future__ import annotations

import json
from typing import Any

from src.llm.chat import build_chat_model
from src.llm.prompt_loader import load_prompt, render_prompt_template
from src.llm.structured_outputs import (
    LocalSearchBatchRouteOutput,
    LocalSearchTaskRouteOutput,
    LocalSearchToolName,
    LocalStructuredSearchQueryOutput,
)


ALLOWED_LOCAL_TOOLS: frozenset[LocalSearchToolName] = frozenset(
    {"local_document", "local_rag", "structured_mock"}
)


def _normalize_route_tools(tools: list[LocalSearchToolName]) -> list[LocalSearchToolName]:
    """去重并校验本地工具列表，保持首次出现顺序。"""

    normalized: list[LocalSearchToolName] = []
    for tool in tools:
        if tool not in ALLOWED_LOCAL_TOOLS:
            raise ValueError(f"本地工具路由返回了不支持的 tool：{tool}")
        if tool not in normalized:
            normalized.append(tool)
    if not normalized:
        raise ValueError("本地工具路由必须至少选择 1 个 tool")
    return normalized


def _normalize_optional_query(
    tools: list[LocalSearchToolName],
    tool_name: LocalSearchToolName,
    query: str | None,
    field_name: str,
) -> str | None:
    """校验选中工具时 query 必填，未选中时必须为 null。"""

    needs_query = tool_name in tools
    if needs_query:
        if query is None or not query.strip():
            raise ValueError(f"tools 含 {tool_name} 时，{field_name} 不能为空")
        return query.strip()
    if query is not None and query.strip():
        raise ValueError(f"tools 不含 {tool_name} 时，{field_name} 必须为 null")
    return None


def _normalize_structured_search(
    tools: list[LocalSearchToolName],
    structured_search: LocalStructuredSearchQueryOutput | None,
) -> LocalStructuredSearchQueryOutput | None:
    """校验 structured_search 与 tools 的一致性。"""

    needs_structured = "structured_mock" in tools
    if needs_structured:
        if structured_search is None:
            raise ValueError(
                "tools 含 structured_mock 时，structured_search 不能为空"
            )
        terms = [term.strip() for term in structured_search.search_terms if term.strip()]
        if not terms:
            raise ValueError("structured_search.search_terms 不能为空")
        return LocalStructuredSearchQueryOutput(
            search_terms=terms,
            sql_search_statement=structured_search.sql_search_statement.strip(),
            reasoning=structured_search.reasoning.strip(),
        )
    if structured_search is not None:
        raise ValueError("tools 不含 structured_mock 时，structured_search 必须为 null")
    return None


def _normalize_task_route(route: LocalSearchTaskRouteOutput) -> LocalSearchTaskRouteOutput:
    """规范化并校验单任务路由。"""

    task_id = route.task_id.strip()
    if not task_id:
        raise ValueError("task_routes.task_id 不能为空")
    tools = _normalize_route_tools(route.tools)
    return LocalSearchTaskRouteOutput(
        task_id=task_id,
        tools=tools,
        reasoning=route.reasoning.strip(),
        local_rag_query=_normalize_optional_query(
            tools, "local_rag", route.local_rag_query, "local_rag_query"
        ),
        local_document_query=_normalize_optional_query(
            tools,
            "local_document",
            route.local_document_query,
            "local_document_query",
        ),
        structured_search=_normalize_structured_search(tools, route.structured_search),
    )


def _normalize_batch_route(
    result: LocalSearchBatchRouteOutput,
    expected_task_ids: set[str],
) -> LocalSearchBatchRouteOutput:
    """校验批次覆盖全部输入 task_id，且无多余项。"""

    normalized_routes: list[LocalSearchTaskRouteOutput] = []
    seen: set[str] = set()
    for route in result.task_routes:
        normalized = _normalize_task_route(route)
        if normalized.task_id in seen:
            raise ValueError(f"本地工具路由重复覆盖 task_id：{normalized.task_id}")
        if normalized.task_id not in expected_task_ids:
            raise ValueError(f"本地工具路由返回了未知 task_id：{normalized.task_id}")
        seen.add(normalized.task_id)
        normalized_routes.append(normalized)

    missing = expected_task_ids - seen
    if missing:
        raise ValueError(
            f"本地工具路由缺少 task_id：{sorted(missing)}"
        )
    return LocalSearchBatchRouteOutput(task_routes=normalized_routes)


def _format_tasks_for_prompt(tasks: list[dict[str, Any]]) -> str:
    """将伞任务列表序列化为 Prompt 可读 JSON。"""

    payload = [
        {
            "task_id": task["task_id"],
            "question_id": task["question_id"],
            "query": task["query"],
        }
        for task in tasks
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def route_local_search_tools(tasks: list[dict[str, Any]]) -> LocalSearchBatchRouteOutput:
    """为本轮全部伞类型 local 任务选择工具并规划入参（只调用一次 LLM）。"""

    if not tasks:
        raise ValueError("本地工具路由 tasks 不能为空")

    expected_task_ids = {str(task["task_id"]) for task in tasks}
    if len(expected_task_ids) != len(tasks):
        raise ValueError("本地工具路由输入 tasks 的 task_id 必须唯一")

    prompt = load_prompt("local_search_router.yml")
    user_prompt = render_prompt_template(
        prompt["user_prompt_template"],
        {"local_tasks_json": _format_tasks_for_prompt(tasks)},
    )
    model = build_chat_model("local_document_search").with_structured_output(
        LocalSearchBatchRouteOutput
    )
    result = model.invoke(
        [
            ("system", prompt["system_prompt"]),
            ("human", user_prompt),
        ]
    )
    return _normalize_batch_route(result, expected_task_ids)
