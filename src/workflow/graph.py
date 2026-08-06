"""ResearchOps Agent 的 LangGraph 占位图。"""

from __future__ import annotations

from langgraph.graph import StateGraph

from src.schemas.state import ResearchInput, ResearchState
from src.workflow.edges import (
    PLAN_RESEARCH,
    BUILD_EVIDENCE_MATRIX,
    CHECK_EVIDENCE_SUFFICIENCY,
    CHECK_STEP_BUDGET,
    DEDUPLICATE_AND_CLUSTER,
    EVALUATE_EVIDENCE_QUALITY,
    GENERATE_RESEARCH_REPORT,
    INPUT_GUARD,
    LOCAL_DOCUMENT_SEARCH_TOOL,
    PERSIST_OUTPUTS,
    PREPARE_DEGRADED_REPORT,
    REQUEST_HUMAN_REVIEW,
    REVIEW_RESEARCH_REPORT,
    SAFETY_REVIEW,
    STRATEGY_ITERATION,
    TOOL_OUTPUT_SANITIZER,
    WEB_SEARCH_SUB_AGENT,
    WEB_SEARCH_RESULT_READY,
    WEB_SEARCH_HITL_REQUEST,
    add_workflow_edges,
)
from src.workflow.nodes import (
    plan_research,
    build_evidence_matrix,
    check_evidence_sufficiency,
    check_step_budget,
    deduplicate_and_cluster,
    evaluate_evidence_quality,
    generate_research_report,
    input_guard,
    local_document_search_tool,
    persist_outputs,
    prepare_degraded_report,
    request_human_review,
    review_research_report,
    safety_review,
    strategy_iteration,
    tool_output_sanitizer,
    web_search_hitl_request,
    web_search_result_ready,
    web_search_sub_agent,
)


def build_graph():
    """构建并编译 ResearchOps Agent 的占位 Graph。"""

    builder = StateGraph(ResearchState, input_schema=ResearchInput)

    builder.add_node(INPUT_GUARD, input_guard)
    builder.add_node(PLAN_RESEARCH, plan_research)
    builder.add_node(WEB_SEARCH_SUB_AGENT, web_search_sub_agent)
    builder.add_node(WEB_SEARCH_HITL_REQUEST, web_search_hitl_request)
    builder.add_node(WEB_SEARCH_RESULT_READY, web_search_result_ready)
    builder.add_node(LOCAL_DOCUMENT_SEARCH_TOOL, local_document_search_tool)
    builder.add_node(TOOL_OUTPUT_SANITIZER, tool_output_sanitizer)
    builder.add_node(DEDUPLICATE_AND_CLUSTER, deduplicate_and_cluster)
    builder.add_node(EVALUATE_EVIDENCE_QUALITY, evaluate_evidence_quality)
    builder.add_node(REQUEST_HUMAN_REVIEW, request_human_review)
    builder.add_node(BUILD_EVIDENCE_MATRIX, build_evidence_matrix)
    builder.add_node(CHECK_EVIDENCE_SUFFICIENCY, check_evidence_sufficiency)
    builder.add_node(CHECK_STEP_BUDGET, check_step_budget)
    builder.add_node(STRATEGY_ITERATION, strategy_iteration)
    builder.add_node(PREPARE_DEGRADED_REPORT, prepare_degraded_report)
    builder.add_node(GENERATE_RESEARCH_REPORT, generate_research_report)
    builder.add_node(REVIEW_RESEARCH_REPORT, review_research_report)
    builder.add_node(SAFETY_REVIEW, safety_review)
    builder.add_node(PERSIST_OUTPUTS, persist_outputs)

    add_workflow_edges(builder)

    return builder.compile()


graph = build_graph()
