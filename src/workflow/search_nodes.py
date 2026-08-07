"""Web Search、本地资料检索与工具输出清洗节点。"""

from __future__ import annotations

import json
import os
from concurrent.futures import as_completed
from typing import Any

from langsmith.utils import ContextThreadPoolExecutor

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
LOCAL_SOURCE_TYPES = {"local_document", "structured_mock"}
MARKDOWN_SOURCE_TYPES = {"local_document"}
STRUCTURED_SOURCE_TYPES = {"structured_mock"}
DEFAULT_WEB_SEARCH_TASK_CONCURRENCY = 3


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


def _web_search_task_concurrency() -> int:
    """读取 Web Search 任务级并发上限，至少为 1。"""

    raw_value = os.getenv(
        "WEB_SEARCH_TASK_CONCURRENCY",
        str(DEFAULT_WEB_SEARCH_TASK_CONCURRENCY),
    )
    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_WEB_SEARCH_TASK_CONCURRENCY
    return max(1, value)


def _prepare_web_task_for_subagent(
    task: dict[str, Any],
    *,
    sub_questions: dict[str, Any],
    expected_evidence: dict[str, Any],
) -> dict[str, Any]:
    """为单个 Web 任务补充 question 与 expected_evidence。"""

    task_for_subagent = dict(task)
    question_id = task["question_id"]
    if question_id in sub_questions:
        task_for_subagent["question"] = sub_questions[question_id]["question"]
    if question_id in expected_evidence:
        task_for_subagent["expected_evidence"] = expected_evidence[question_id]
    return task_for_subagent


def _materialize_web_search_results(
    task: dict[str, Any],
    subagent_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """把单个 Web SubAgent 结果转换成节点级候选结果。"""

    results = []
    for index, item in enumerate(subagent_result["results"], start=1):
        results.append(
            {
                "result_id": f"R_web_{task['task_id']}_{index}",
                "task_id": task["task_id"],
                "question_id": task["question_id"],
                "source_type": task["source_type"],
                "source_name": item["source_name"],
                "url_or_path": item.get("url_or_path") or item.get("url"),
                "url": item.get("url") or item.get("url_or_path"),
                "title": item["title"],
                "snippet": item.get("snippet") or "",
                "body": item.get("body"),
                "published_at": item.get("published_at"),
                "collected_by": "web_search_subagent",
                "requires_login": bool(item.get("requires_login", False)),
                "blocked_reason": item.get("blocked_reason"),
                "score": item.get("score"),
                "relevance_score": item.get("relevance_score"),
                "answer_coverage_score": item.get("answer_coverage_score"),
                "source_confidence_score": item.get("source_confidence_score"),
                "freshness_score": item.get("freshness_score"),
                "score_reason": item.get("score_reason"),
                "tavily_score": item.get("tavily_score"),
                "docs_result": item.get("docs_result"),
                "resolve_result": item.get("resolve_result"),
                "library_id": item.get("library_id"),
                "playwright_hitl": item.get("playwright_hitl"),
                "score_bucket": item.get("score_bucket"),
                "scored_by": item.get("scored_by"),
            }
        )
    return results


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
        "requires_login": False,
        "blocked_reason": None,
    }


def web_search_sub_agent(state: ResearchState) -> dict[str, Any]:
    """Web Search SubAgent 节点。

    该节点对多个 Web 任务做有界并发，每个任务内部仍采用
    确定性工具调度和单次 LLM 汇总，不做自由循环搜索。
    """

    tasks = _tasks_by_source_type(state, WEB_SOURCE_TYPES)
    results = []
    web_tool_evaluation_records = []
    web_hitl_required = False
    web_hitl_reason = None
    sub_questions = state.get("sub_questions", {})
    expected_evidence = state.get("expected_evidence", {})

    prepared_tasks = [
        _prepare_web_task_for_subagent(
            task,
            sub_questions=sub_questions,
            expected_evidence=expected_evidence,
        )
        for task in tasks
    ]
    ordered_subagent_results: list[dict[str, Any] | None] = [None] * len(prepared_tasks)
    if prepared_tasks:
        max_workers = min(_web_search_task_concurrency(), len(prepared_tasks))
        with ContextThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(run_web_search_subagent_for_task, task): index
                for index, task in enumerate(prepared_tasks)
            }
            try:
                for future in as_completed(future_to_index):
                    index = future_to_index[future]
                    ordered_subagent_results[index] = future.result()
            except Exception:
                for pending_future in future_to_index:
                    pending_future.cancel()
                raise

    for task, subagent_result in zip(tasks, ordered_subagent_results, strict=True):
        if subagent_result is None:
            raise RuntimeError(f"Web Search 任务缺少结果：{task['task_id']}")
        web_tool_evaluation_records.extend(subagent_result["tool_evaluation_records"])
        web_hitl_required = web_hitl_required or bool(
            subagent_result["web_hitl_required"]
        )
        if web_hitl_reason is None and subagent_result.get("hitl_reason"):
            web_hitl_reason = subagent_result["hitl_reason"]
        results.extend(_materialize_web_search_results(task, subagent_result))

    return {
        **record_node(state, "web_search_sub_agent"),
        "web_search_results": results,
        "web_tool_evaluation_records": web_tool_evaluation_records,
        "web_hitl_required": web_hitl_required,
        "web_hitl_reason": web_hitl_reason,
    }


def web_search_hitl_request(state: ResearchState) -> dict[str, Any]:
    """Web Search Playwright 登录 / 反爬 HITL 观测占位。

    仅服务 Playwright 触发的 requires_login 结果；用 DevTools 观察登录 / 权限页。
    记录 `completed=false`，不真正暂停，不因未完成而降级；不构成安全闭环。
    """

    web_results = state.get("web_search_results", [])
    decisions = []
    for result in web_results:
        if not result.get("requires_login"):
            continue
        # 仅 Playwright 路径：显式标记或 source_name=playwright
        if not (
            result.get("playwright_hitl")
            or str(result.get("source_name") or "").lower() == "playwright"
        ):
            continue
        url = result.get("url_or_path") or result.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        devtools_observation = _inspect_login_page_with_devtools(url)
        decisions.append(
            {
                "hitl_type": "web_search_login_handoff",
                "result_id": result["result_id"],
                "task_id": result["task_id"],
                "question_id": result["question_id"],
                "url": url,
                "completed": False,
                "decision": "observation_placeholder_requires_user_login",
                "reason": result.get("blocked_reason") or state.get("web_hitl_reason"),
                "devtools_observation": devtools_observation,
            }
        )

    return {
        **record_node(state, "web_search_hitl_request"),
        "web_hitl_decisions": decisions,
        "web_hitl_required": bool(decisions),
    }


def web_search_result_ready(state: ResearchState) -> dict[str, Any]:
    """Web Search 分支完成节点。

    该节点用于让 `web_search_sub_agent -> 可选 web_search_hitl_request` 先完成，
    再和 `local_document_search_tool` 汇聚到 `sanitize_and_cluster`。
    """

    return record_node(state, "web_search_result_ready")


def _query_markdown_task(task: dict[str, Any]) -> list[dict[str, Any]]:
    """执行本地 Markdown 关键词搜索。"""

    try:
        return query_local_documents_for_task(task)
    except Exception as exc:  # noqa: BLE001 - 工具失败要显式暴露给后续节点
        return [
            {
                **_build_placeholder_result(
                    task=task,
                    collected_by="local_document_search",
                    source_name="local_markdown_documents",
                    title_prefix="本地 Markdown 搜索失败",
                    url_or_path_prefix="placeholder://local_document/error",
                ),
                "blocked_reason": str(exc),
                "local_payload": {"error": str(exc)},
            }
        ]


def _query_structured_task(task: dict[str, Any]) -> list[dict[str, Any]]:
    """执行本地结构化资料查询。"""

    task_results = query_structured_products_for_task(task)
    if task_results:
        return task_results
    return [
        {
            **_build_placeholder_result(
                task=task,
                collected_by="local_structured_search",
                source_name="local_mock_ai_products",
                title_prefix="本地结构化资料无匹配",
                url_or_path_prefix="placeholder://structured_mock/no_match",
            ),
            "structured_payload": {
                "matched_product_count": 0,
                "query": task["query"],
                "reason": "本地结构化 mock 数据无匹配记录",
            },
        }
    ]


def local_document_search_tool(state: ResearchState) -> dict[str, Any]:
    """本地资料检索节点。

    读取：`search_tasks`
    写入：`local_document_results`

    - `local_document`：搜索 `LOCAL_DOCUMENTS_BASE_PATH` 下的 Markdown 文件。
    - `structured_mock`：查询本地结构化 mock 数据。

    结构化数据也属于本地资料检索能力；后续 PDF 等本地资料类型也应
    继续并入该节点，而不是新增独立 Graph 节点。
    """

    tasks = _tasks_by_source_type(state, LOCAL_SOURCE_TYPES)
    results = []
    for task in tasks:
        source_type = task["source_type"]
        if source_type in MARKDOWN_SOURCE_TYPES:
            results.extend(_query_markdown_task(task))
        elif source_type in STRUCTURED_SOURCE_TYPES:
            results.extend(_query_structured_task(task))
        else:
            raise ValueError(f"local_document_search_tool 不支持的 source_type：{source_type}")

    return {
        **record_node(state, "local_document_search_tool"),
        "local_document_results": results,
    }
