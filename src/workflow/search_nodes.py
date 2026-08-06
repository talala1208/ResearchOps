"""检索工具与工具输出清洗节点。

当前实现为替代数据源版本：不联网、不读取真实文件、不调用外部工具，
只根据 `search_tasks` 生成可预测的候选结果，供后续节点先跑通数据流。
"""

from __future__ import annotations

import json
import os
from typing import Any

from src.schemas.state import ResearchState
from src.tools.local_document_search import query_local_documents_for_task
from src.tools.structured_data import query_structured_products_for_task
from src.tools.online_mcp_tools import devtools_mcp_inspect_page
from src.tools.web_search_subagent import run_web_search_subagent_for_task
from src.workflow.node_utils import record_node


WEB_SOURCE_TYPES = {
    "official_docs",
    "pricing_page",
    "changelog",
    "blog",
    "community",
    "github",
    "product_directory",
    "traffic_data",
}
LOCAL_SOURCE_TYPES = {"local_document"}
STRUCTURED_SOURCE_TYPES = {"structured_mock"}


def _hitl_devtools_enabled() -> bool:
    """判断 Web HITL 是否启用 DevTools 登录界面确认。"""

    return os.getenv("ENABLE_HITL_DEVTOOLS", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _inspect_login_page_with_devtools(url: str) -> dict[str, Any]:
    """用 DevTools MCP 打开并观察登录 / 权限页面。"""

    if not _hitl_devtools_enabled():
        return {
            "ok": False,
            "provider": "devtools_mcp",
            "skipped": True,
            "reason": "ENABLE_HITL_DEVTOOLS=false，跳过 DevTools 登录界面确认。",
        }
    raw_result = devtools_mcp_inspect_page.invoke({"url": url})
    return json.loads(raw_result)


def _tasks_by_source_type(state: ResearchState, source_types: set[str]) -> list[dict[str, Any]]:
    """按 source_type 过滤检索任务。"""

    return [
        task
        for task in state.get("search_tasks", [])
        if task.get("source_type") in source_types
    ]


def _build_placeholder_result(
    *,
    task: dict[str, Any],
    collected_by: str,
    source_name: str,
    title_prefix: str,
    url_or_path_prefix: str,
) -> dict[str, Any]:
    """构造替代检索结果。"""

    task_id = task["task_id"]
    question_id = task["question_id"]
    source_type = task["source_type"]
    query = task["query"]

    return {
        "result_id": f"R_{collected_by}_{task_id}",
        "task_id": task_id,
        "question_id": question_id,
        "source_type": source_type,
        "source_name": source_name,
        "url_or_path": f"{url_or_path_prefix}/{task_id}",
        "title": f"{title_prefix}：{query}",
        "snippet": f"替代数据源结果：围绕 `{query}` 为子问题 `{question_id}` 提供一条 {source_type} 类型候选证据。",
        "published_at": None,
        "collected_by": collected_by,
        "is_placeholder": True,
        "requires_login": False,
        "blocked_reason": None,
    }


def web_search_sub_agent(state: ResearchState) -> dict[str, Any]:
    """Web Search SubAgent 节点。

    该节点调用 `web_search_subagent_tool`。这个 tool 内部采用
    确定性 Web 工具调度和单次 LLM 汇总，不做自由循环搜索。
    """

    tasks = _tasks_by_source_type(state, WEB_SOURCE_TYPES)
    results = []
    web_hitl_required = False
    web_hitl_reason = None
    sub_questions = state.get("sub_questions", {})
    expected_evidence = state.get("expected_evidence", {})

    for task in tasks:
        task_for_subagent = dict(task)
        question_id = task["question_id"]
        if question_id in sub_questions:
            task_for_subagent["question"] = sub_questions[question_id]["question"]
        if question_id in expected_evidence:
            task_for_subagent["expected_evidence"] = expected_evidence[question_id]

        subagent_result = run_web_search_subagent_for_task(task_for_subagent)
        web_hitl_required = web_hitl_required or bool(
            subagent_result["web_hitl_required"]
        )
        web_hitl_reason = subagent_result.get("hitl_reason") or web_hitl_reason

        for index, item in enumerate(subagent_result["results"], start=1):
            results.append(
                {
                    "result_id": f"R_web_{task['task_id']}_{index}",
                    "task_id": task["task_id"],
                    "question_id": task["question_id"],
                    "source_type": task["source_type"],
                    "source_name": item["source_name"],
                    "url_or_path": item["url_or_path"],
                    "title": item["title"],
                    "snippet": item["snippet"],
                    "published_at": item.get("published_at"),
                    "collected_by": "web_search_subagent",
                    "is_placeholder": True,
                    "requires_login": bool(item.get("requires_login", False)),
                    "blocked_reason": item.get("blocked_reason"),
                    "relevance_score": item.get("relevance_score"),
                    "answer_coverage_score": item.get("answer_coverage_score"),
                    "source_confidence_score": item.get("source_confidence_score"),
                    "freshness_score": item.get("freshness_score"),
                    "score_reason": item.get("score_reason"),
                }
            )

    return {
        **record_node(state, "web_search_sub_agent"),
        "web_search_results": results,
        "web_hitl_required": web_hitl_required,
        "web_hitl_reason": web_hitl_reason,
    }


def web_search_hitl_request(state: ResearchState) -> dict[str, Any]:
    """Web Search 登录 / 反爬 HITL 处理。

    DevTools 只在 HITL 场景使用，用于打开、确认和展示登录 / 权限页面。
    当前节点不默认通过 HITL；它只记录待人工接管的页面观察结果。
    """

    web_results = state.get("web_search_results", [])
    decisions = []
    for result in web_results:
        if not result.get("requires_login"):
            continue
        url = result["url_or_path"]
        devtools_observation = _inspect_login_page_with_devtools(url)
        decisions.append(
            {
                "hitl_type": "web_search_login_handoff",
                "result_id": result["result_id"],
                "task_id": result["task_id"],
                "question_id": result["question_id"],
                "url": url,
                "completed": False,
                "decision": "requires_user_login_or_confirmation",
                "reason": result.get("blocked_reason") or state.get("web_hitl_reason"),
                "devtools_observation": devtools_observation,
            }
        )

    return {
        **record_node(state, "web_search_hitl_request"),
        "web_hitl_decisions": decisions,
        "web_hitl_required": bool(decisions),
    }


def local_document_search_tool(state: ResearchState) -> dict[str, Any]:
    """Local Document Search 代码实现。

    读取：`search_tasks`
    写入：`local_document_results`

    该节点只读 `LOCAL_DOCUMENTS_BASE_PATH` 下的 Markdown 文件，不调用 LLM。
    """

    tasks = _tasks_by_source_type(state, LOCAL_SOURCE_TYPES)
    results = []
    for task in tasks:
        try:
            task_results = query_local_documents_for_task(task)
        except Exception as exc:  # noqa: BLE001 - 工具失败要显式暴露给后续节点
            task_results = [
                {
                    **_build_placeholder_result(
                        task=task,
                        collected_by="local_document_search",
                        source_name="local_markdown_documents",
                        title_prefix="本地文档搜索失败",
                        url_or_path_prefix="placeholder://local_document/error",
                    ),
                    "blocked_reason": str(exc),
                    "local_payload": {"error": str(exc)},
                }
            ]
        results.extend(task_results)

    return {
        **record_node(state, "local_document_search_tool"),
        "local_document_results": results,
    }


def query_structured_data(state: ResearchState) -> dict[str, Any]:
    """结构化数据查询工具节点。

    读取：`search_tasks`
    写入：`structured_data_results`
    """

    tasks = _tasks_by_source_type(state, STRUCTURED_SOURCE_TYPES)
    results = []
    for task in tasks:
        task_results = query_structured_products_for_task(task)
        if task_results:
            results.extend(task_results)
            continue
        results.append(
            {
                **_build_placeholder_result(
                    task=task,
                    collected_by="query_structured_data",
                    source_name="local_mock_ai_products",
                    title_prefix="结构化 mock 查询无匹配",
                    url_or_path_prefix="placeholder://structured_mock/no_match",
                ),
                "structured_payload": {
                    "matched_product_count": 0,
                    "query": task["query"],
                    "reason": "本地结构化 mock 数据无匹配记录",
                },
            }
        )

    return {
        **record_node(state, "query_structured_data"),
        "structured_data_results": results,
    }


def tool_output_sanitizer(state: ResearchState) -> dict[str, Any]:
    """工具输出清洗占位节点。"""

    raw_results = []
    raw_results.extend(state.get("web_search_results", []))
    raw_results.extend(state.get("local_document_results", []))
    raw_results.extend(state.get("structured_data_results", []))

    sanitized_results = [
        {
            **result,
            "trust_boundary": "untrusted_tool_output",
            "sanitized": True,
        }
        for result in raw_results
    ]

    return {
        **record_node(state, "tool_output_sanitizer"),
        "raw_search_results": raw_results,
        "sanitized_results": sanitized_results,
    }
