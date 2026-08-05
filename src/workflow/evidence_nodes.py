"""证据治理、HITL、证据充足性与检索预算节点。"""

from __future__ import annotations

from typing import Any

from src.schemas.state import ResearchState
from src.workflow.node_utils import record_node


def deduplicate_and_cluster(state: ResearchState) -> dict[str, Any]:
    """去重与聚类占位节点。"""

    return {
        **record_node(state, "deduplicate_and_cluster"),
        "evidence_clusters": [],
    }


def evaluate_evidence_quality(state: ResearchState) -> dict[str, Any]:
    """证据质量评估占位节点。"""

    return {
        **record_node(state, "evaluate_evidence_quality"),
        "evidence_items": {},
        "conflicts": {},
        "entity_index": {
            **state.get("entity_index", {}),
            "evidence_ids_by_question_id": {},
            "conflict_ids_by_question_id": {},
        },
        "hitl_decisions": [],
        "hitl_required": False,
    }


def request_human_review(state: ResearchState) -> dict[str, Any]:
    """动态 HITL 人工确认占位节点。"""

    return {
        **record_node(state, "request_human_review"),
        "hitl_decisions": state.get("hitl_decisions", []),
    }


def build_evidence_matrix(state: ResearchState) -> dict[str, Any]:
    """证据矩阵构建占位节点。"""

    return {
        **record_node(state, "build_evidence_matrix"),
        "evidence_matrix": {},
    }


def check_evidence_sufficiency(state: ResearchState) -> dict[str, Any]:
    """证据充足性判断占位节点。

    后续真实实现中，Priority 会影响 `evidence_sufficiency_score`：
    high / medium / low 子问题分别按不同权重计算整体覆盖率，且 high
    优先级子问题未满足时通常不能判断为整体证据充足。
    """

    evidence_sufficiency_score = 1.0
    evidence_sufficiency_threshold = 0.75
    insufficient_question_ids: list[str] = []
    question_evidence_status = {}

    return {
        **record_node(state, "check_evidence_sufficiency"),
        "question_evidence_status": question_evidence_status,
        "evidence_sufficiency_score": evidence_sufficiency_score,
        "evidence_sufficiency_threshold": evidence_sufficiency_threshold,
        "evidence_sufficient": True,
        "insufficient_question_ids": insufficient_question_ids,
        "evidence_sufficiency_result": {
            "sufficient": True,
            "overall_score": evidence_sufficiency_score,
            "threshold": evidence_sufficiency_threshold,
            "high_priority_all_met": True,
            "question_status": question_evidence_status,
            "insufficient_question_ids": insufficient_question_ids,
            "degradation_recommended": False,
            "degradation_reason": None,
        },
    }


def check_step_budget(state: ResearchState) -> dict[str, Any]:
    """检索预算检查占位节点。

    该节点只判断是否还允许继续检索 / 迭代，不负责生成降级报告。
    报告生成、Review 和 Safety Review 不消耗检索预算。
    """

    search_steps = state.get("search_steps", 0)
    max_search_steps = state.get("max_search_steps", 3)
    step_budget_exhausted = search_steps >= max_search_steps

    return {
        **record_node(state, "check_step_budget"),
        "step_budget_exhausted": step_budget_exhausted,
    }


def strategy_iteration(state: ResearchState) -> dict[str, Any]:
    """证据不足时的策略迭代占位节点。"""

    iteration_count = state.get("iteration_count", 0) + 1

    return {
        **record_node(state, "strategy_iteration"),
        "iteration_count": iteration_count,
        "active_question_ids": state.get("insufficient_question_ids", []),
    }
