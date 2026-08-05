"""ResearchOps Agent 的占位节点实现。

本文件只放 LangGraph 节点函数。当前版本用于先跑通 Graph 编排骨架，
不接入真实 LLM、搜索工具、LangSmith 或本地文件写入。
"""

from __future__ import annotations

from typing import Any

from src.schemas.state import ResearchState


def _record_node(state: ResearchState, node_name: str) -> ResearchState:
    """记录节点执行痕迹，不参与任何预算控制。"""

    executed_nodes = list(state.get("executed_nodes", []))
    executed_nodes.append(node_name)
    return {"executed_nodes": executed_nodes}


def _increase_search_step(state: ResearchState, node_name: str) -> ResearchState:
    """记录一次检索 / 迭代预算消耗。"""

    search_steps = state.get("search_steps", 0) + 1
    return {
        **_record_node(state, node_name),
        "search_steps": search_steps,
    }


def input_guard(state: ResearchState) -> dict[str, Any]:
    """输入安全检查占位节点。"""

    return {
        **_record_node(state, "input_guard"),
        "input_guard_result": {
            "safety_pass": True,
            "safety_risk_level": "low",
            "detected_risks": [],
            "downgrade_required": False,
            "downgrade_reason": None,
        },
    }


def analyze_research_request(state: ResearchState) -> dict[str, Any]:
    """研究任务分析占位节点。"""

    user_query = state.get("user_query", "")

    return {
        **_record_node(state, "analyze_research_request"),
        "research_goal": user_query,
        "sub_questions": {},
        "required_source_types": [],
        "expected_evidence": {},
        "minimum_evidence_standard": {},
        "entity_index": {
            "question_ids": [],
            "evidence_ids_by_question_id": {},
            "conflict_ids_by_question_id": {},
            "search_task_ids_by_question_id": {},
            "used_evidence_ids": [],
        },
        "active_question_ids": [],
        "search_steps": state.get("search_steps", 0),
        "max_search_steps": state.get("max_search_steps", 3),
        "review_revision_count": state.get("review_revision_count", 0),
        "max_review_revisions": state.get("max_review_revisions", 1),
        "safety_revision_count": state.get("safety_revision_count", 0),
        "max_safety_revisions": state.get("max_safety_revisions", 1),
    }


def dispatch_search_tasks(state: ResearchState) -> dict[str, Any]:
    """检索任务分发占位节点。

    每进入一次检索任务分发，视为消耗一次检索预算。
    """

    return {
        **_increase_search_step(state, "dispatch_search_tasks"),
        "search_tasks": [],
        "active_question_ids": [],
    }


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
        **_record_node(state, "tool_output_sanitizer"),
        "raw_search_results": raw_results,
        "sanitized_results": raw_results,
    }


def deduplicate_and_cluster(state: ResearchState) -> dict[str, Any]:
    """去重与聚类占位节点。"""

    return {
        **_record_node(state, "deduplicate_and_cluster"),
        "evidence_clusters": [],
    }


def evaluate_evidence_quality(state: ResearchState) -> dict[str, Any]:
    """证据质量评估占位节点。"""

    return {
        **_record_node(state, "evaluate_evidence_quality"),
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
        **_record_node(state, "request_human_review"),
        "hitl_decisions": state.get("hitl_decisions", []),
    }


def build_evidence_matrix(state: ResearchState) -> dict[str, Any]:
    """证据矩阵构建占位节点。"""

    return {
        **_record_node(state, "build_evidence_matrix"),
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
        **_record_node(state, "check_evidence_sufficiency"),
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
        **_record_node(state, "check_step_budget"),
        "step_budget_exhausted": step_budget_exhausted,
    }


def strategy_iteration(state: ResearchState) -> dict[str, Any]:
    """证据不足时的策略迭代占位节点。"""

    iteration_count = state.get("iteration_count", 0) + 1

    return {
        **_record_node(state, "strategy_iteration"),
        "iteration_count": iteration_count,
        "active_question_ids": state.get("insufficient_question_ids", []),
    }


def prepare_degraded_report(state: ResearchState) -> dict[str, Any]:
    """降级报告准备占位节点。

    该节点只负责设置降级状态，不负责判断预算，也不负责写报告正文。
    """

    safety_result = state.get("safety_review_result", {})
    safety_revision_count = state.get("safety_revision_count", 0)
    if safety_result and safety_result.get("safety_pass") is False:
        safety_revision_count += 1

    reason = state.get("degradation_reason") or "占位降级：证据不足、检索预算不足或安全审查未通过"

    return {
        **_record_node(state, "prepare_degraded_report"),
        "degraded": True,
        "degradation_reason": reason,
        "safety_revision_count": safety_revision_count,
    }


def generate_research_report(state: ResearchState) -> dict[str, Any]:
    """研究报告生成占位节点。"""

    user_query = state.get("user_query", "")
    degraded = state.get("degraded", False)
    degradation_note = "\n\n降级原因：" + state.get("degradation_reason", "") if degraded else ""
    report_draft = f"# 研究报告草稿\n\n研究主题：{user_query}{degradation_note}\n\n当前为空 Graph 占位输出。"

    review_revision_count = state.get("review_revision_count", 0)
    previous_review = state.get("review_result", {})
    if previous_review and previous_review.get("passed") is False:
        review_revision_count += 1

    return {
        **_record_node(state, "generate_research_report"),
        "report_draft": report_draft,
        "degraded": degraded,
        "degradation_reason": state.get("degradation_reason"),
        "review_revision_count": review_revision_count,
    }


def review_research_report(state: ResearchState) -> dict[str, Any]:
    """报告复核占位节点。"""

    return {
        **_record_node(state, "review_research_report"),
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


def safety_review(state: ResearchState) -> dict[str, Any]:
    """最终安全审查占位节点。"""

    return {
        **_record_node(state, "safety_review"),
        "safety_review_result": {
            "safety_pass": True,
            "safety_risk_level": "low",
            "detected_risks": [],
            "downgrade_required": False,
            "downgrade_reason": None,
        },
        "final_report": state.get("report_draft", ""),
    }


def persist_outputs(state: ResearchState) -> dict[str, Any]:
    """本地运行产物持久化占位节点。

    当前只写 state 字段，不实际写文件。后续再接入 Markdown、Mermaid、
    PNG 和 metrics JSON 的保存逻辑。
    """

    executed_nodes = state.get("executed_nodes", [])

    return {
        **_record_node(state, "persist_outputs"),
        "final_report_path": None,
        "executed_mermaid": None,
        "executed_mermaid_png_path": None,
        "output_artifacts": {},
        "evaluation_metrics": {
            "total_steps": len(executed_nodes) + 1,
            "search_steps": state.get("search_steps", 0),
            "max_search_steps": state.get("max_search_steps", 3),
            "review_revision_count": state.get("review_revision_count", 0),
            "max_review_revisions": state.get("max_review_revisions", 1),
            "safety_revision_count": state.get("safety_revision_count", 0),
            "max_safety_revisions": state.get("max_safety_revisions", 1),
            "total_latency_ms": 0,
            "total_tokens": 0,
            "evidence_count": len(state.get("evidence_items", {})),
            "final_citation_count": 0,
            "conflict_count": len(state.get("conflicts", {})),
            "degraded": state.get("degraded", False),
            "degradation_reason": state.get("degradation_reason"),
            "review_score": state.get("review_result", {}).get("report_score"),
            "safety_risk_level": state.get("safety_review_result", {}).get(
                "safety_risk_level"
            ),
            "source_contribution": {},
            "web_hitl_trigger_count_by_source": {},
        },
    }
