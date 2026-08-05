"""检索工具与工具输出清洗节点。"""

from __future__ import annotations

from typing import Any

from src.schemas.state import ResearchState
from src.workflow.node_utils import record_node


def web_search_sub_agent(state: ResearchState) -> dict[str, Any]:
    """Web Search Sub Agent 占位节点。"""

    return {
        "web_search_results": [],
        "web_hitl_required": False,
    }


def local_document_search_sub_agent(state: ResearchState) -> dict[str, Any]:
    """Local Document Search Sub Agent 占位节点。"""

    return {"local_document_results": []}


def query_structured_data(state: ResearchState) -> dict[str, Any]:
    """结构化数据查询占位节点。"""

    return {"structured_data_results": []}


def tool_output_sanitizer(state: ResearchState) -> dict[str, Any]:
    """工具输出清洗占位节点。"""

    raw_results = []
    raw_results.extend(state.get("web_search_results", []))
    raw_results.extend(state.get("local_document_results", []))
    raw_results.extend(state.get("structured_data_results", []))

    return {
        **record_node(state, "tool_output_sanitizer"),
        "raw_search_results": raw_results,
        "sanitized_results": raw_results,
    }
