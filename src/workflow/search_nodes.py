"""检索工具与工具输出清洗节点。

当前实现为替代数据源版本：不联网、不读取真实文件、不调用外部工具，
只根据 `search_tasks` 生成可预测的候选结果，供后续节点先跑通数据流。
"""

from __future__ import annotations

from typing import Any

from src.schemas.state import ResearchState
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
    """Web Search Sub Agent 替代实现。

    读取：`search_tasks`
    写入：`web_search_results`、`web_hitl_required`
    """

    tasks = _tasks_by_source_type(state, WEB_SOURCE_TYPES)
    results = [
        _build_placeholder_result(
            task=task,
            collected_by="web_search",
            source_name="placeholder_web_search",
            title_prefix="Web 替代检索结果",
            url_or_path_prefix="placeholder://web",
        )
        for task in tasks
    ]

    web_hitl_required = any(result["requires_login"] for result in results)

    return {
        "web_search_results": results,
        "web_hitl_required": web_hitl_required,
    }


def web_search_hitl_request(state: ResearchState) -> dict[str, Any]:
    """Web Search 登录 / 反爬 HITL 替代实现。

    当前不真正暂停等待用户，只记录 Web HITL 被处理为“未触发或已跳过”。
    后续接入真实 HITL 时再改为中断 / 恢复逻辑。
    """

    web_results = state.get("web_search_results", [])
    decisions = [
        {
            "hitl_type": "web_search_handoff",
            "result_id": result["result_id"],
            "task_id": result["task_id"],
            "question_id": result["question_id"],
            "completed": False,
            "decision": "placeholder_no_handoff_required",
            "reason": result.get("blocked_reason"),
        }
        for result in web_results
        if result.get("requires_login")
    ]

    return {
        "web_hitl_decisions": decisions,
        "web_hitl_required": False,
    }


def local_document_search_sub_agent(state: ResearchState) -> dict[str, Any]:
    """Local Document Search Sub Agent 替代实现。

    读取：`search_tasks`
    写入：`local_document_results`
    """

    tasks = _tasks_by_source_type(state, LOCAL_SOURCE_TYPES)
    results = [
        _build_placeholder_result(
            task=task,
            collected_by="local_document_search",
            source_name="placeholder_local_vault",
            title_prefix="本地文档替代检索结果",
            url_or_path_prefix="placeholder://local_document",
        )
        for task in tasks
    ]

    return {"local_document_results": results}


def query_structured_data(state: ResearchState) -> dict[str, Any]:
    """结构化数据查询替代实现。

    读取：`search_tasks`
    写入：`structured_data_results`
    """

    tasks = _tasks_by_source_type(state, STRUCTURED_SOURCE_TYPES)
    results = [
        {
            **_build_placeholder_result(
                task=task,
                collected_by="query_structured_data",
                source_name="placeholder_structured_mock",
                title_prefix="结构化 mock 替代查询结果",
                url_or_path_prefix="placeholder://structured_mock",
            ),
            "structured_payload": {
                "matched_product_count": 1,
                "fields": ["name", "pricing", "features", "target_users"],
                "aggregation": "placeholder_single_match",
            },
        }
        for task in tasks
    ]

    return {"structured_data_results": results}


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
