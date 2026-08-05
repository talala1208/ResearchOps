"""Workflow 节点共享工具函数。"""

from __future__ import annotations

from src.schemas.state import ResearchState


def record_node(state: ResearchState, node_name: str) -> ResearchState:
    """记录节点执行痕迹，不参与任何预算控制。"""

    del state
    return {"executed_nodes": [node_name]}


def increase_search_step(state: ResearchState, node_name: str) -> ResearchState:
    """记录一次检索 / 迭代预算消耗。"""

    search_steps = state.get("search_steps", 0) + 1
    return {
        **record_node(state, node_name),
        "search_steps": search_steps,
    }
