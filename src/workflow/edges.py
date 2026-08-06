"""ResearchOps Agent 的占位边和路由规则。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.config.settings import get_workflow_config
from src.schemas.state import ResearchState


INPUT_GUARD = "input_guard"
PLAN_RESEARCH = "plan_research"
WEB_SEARCH_SUB_AGENT = "web_search_sub_agent"
WEB_SEARCH_RESULT_READY = "web_search_result_ready"
LOCAL_DOCUMENT_SEARCH_TOOL = "local_document_search_tool"
TOOL_OUTPUT_SANITIZER = "tool_output_sanitizer"
DEDUPLICATE_AND_CLUSTER = "deduplicate_and_cluster"
EVALUATE_EVIDENCE_QUALITY = "evaluate_evidence_quality"
REQUEST_HUMAN_REVIEW = "request_human_review"
WEB_SEARCH_HITL_REQUEST = "web_search_hitl_request"
BUILD_EVIDENCE_MATRIX = "build_evidence_matrix"
CHECK_EVIDENCE_SUFFICIENCY = "check_evidence_sufficiency"
CHECK_STEP_BUDGET = "check_step_budget"
STRATEGY_ITERATION = "strategy_iteration"
PREPARE_DEGRADED_REPORT = "prepare_degraded_report"
GENERATE_RESEARCH_REPORT = "generate_research_report"
REVIEW_RESEARCH_REPORT = "review_research_report"
SAFETY_REVIEW = "safety_review"
PERSIST_OUTPUTS = "persist_outputs"


def route_after_input_guard(state: ResearchState) -> str:
    """输入安全检查后的路由。"""

    result = state.get("input_guard_result", {})
    if result.get("safety_pass") is False or result.get("downgrade_required") is True:
        return PREPARE_DEGRADED_REPORT
    return PLAN_RESEARCH


def route_after_plan_research(state: ResearchState) -> list[str]:
    """检索任务分发后的条件路由。

    当前图里 Web 检索和本地资料检索两个节点需要并行汇聚到同一个清洗节点。
    结构化 mock 数据也属于本地资料检索能力，由 `local_document_search_tool`
    根据 `search_tasks.source_type` 在内部选择 Markdown 关键词搜索或结构化 SQL 查询。
    """

    return [
        WEB_SEARCH_SUB_AGENT,
        LOCAL_DOCUMENT_SEARCH_TOOL,
    ]


def route_after_web_search(state: ResearchState) -> str:
    """Web Search 后的 HITL 路由。

    用于登录墙、验证码、反爬或必须用户接管的读取场景。
    """

    if state.get("web_hitl_required", False):
        return WEB_SEARCH_HITL_REQUEST
    return WEB_SEARCH_RESULT_READY


def route_after_evidence_quality(state: ResearchState) -> str:
    """证据质量评估后的路由。"""

    if state.get("hitl_required", False):
        return REQUEST_HUMAN_REVIEW
    return BUILD_EVIDENCE_MATRIX


def route_after_evidence_sufficiency(state: ResearchState) -> str:
    """证据充足性判断后的路由。"""

    if state.get("evidence_sufficient", False):
        return GENERATE_RESEARCH_REPORT

    return CHECK_STEP_BUDGET


def route_after_step_budget(state: ResearchState) -> str:
    """最大步数检查后的路由。"""

    if state.get("step_budget_exhausted", False):
        return PREPARE_DEGRADED_REPORT
    return STRATEGY_ITERATION


def route_after_review(state: ResearchState) -> str:
    """报告复核后的路由。

    Review 不通过且步数充足时回到报告生成；否则进入安全审查。
    这里保留条件路由，避免失败时同时进入修正和安全审查两个分支。
    """

    workflow_config = get_workflow_config()
    review_result = state.get("review_result", {})
    review_revision_count = state.get("review_revision_count", 0)
    max_review_revisions = state.get(
        "max_review_revisions", workflow_config.max_review_revisions
    )

    if (
        review_result.get("passed") is False
        and review_revision_count < max_review_revisions
    ):
        return GENERATE_RESEARCH_REPORT
    return SAFETY_REVIEW


def route_after_safety_review(state: ResearchState) -> str:
    """安全审查后的路由。"""

    workflow_config = get_workflow_config()
    safety_result = state.get("safety_review_result", {})
    safety_revision_count = state.get("safety_revision_count", 0)
    max_safety_revisions = state.get(
        "max_safety_revisions", workflow_config.max_safety_revisions
    )
    safety_failed = (
        safety_result.get("safety_pass") is False
        or safety_result.get("downgrade_required") is True
    )

    if safety_failed and safety_revision_count < max_safety_revisions:
        return PREPARE_DEGRADED_REPORT
    return PERSIST_OUTPUTS


def add_workflow_edges(builder: StateGraph) -> StateGraph:
    """给 Graph 添加占位边。"""

    builder.add_edge(START, INPUT_GUARD)
    builder.add_conditional_edges(
        INPUT_GUARD,
        route_after_input_guard,
        {
            PLAN_RESEARCH: PLAN_RESEARCH,
            PREPARE_DEGRADED_REPORT: PREPARE_DEGRADED_REPORT,
        },
    )

    # Web 与本地资料检索能力并行触发；结构化 mock 在本地资料节点内部处理。
    builder.add_conditional_edges(
        PLAN_RESEARCH,
        route_after_plan_research,
        {
            WEB_SEARCH_SUB_AGENT: WEB_SEARCH_SUB_AGENT,
            LOCAL_DOCUMENT_SEARCH_TOOL: LOCAL_DOCUMENT_SEARCH_TOOL,
        },
    )
    builder.add_conditional_edges(
        WEB_SEARCH_SUB_AGENT,
        route_after_web_search,
        {
            WEB_SEARCH_HITL_REQUEST: WEB_SEARCH_HITL_REQUEST,
            WEB_SEARCH_RESULT_READY: WEB_SEARCH_RESULT_READY,
        },
    )
    builder.add_edge(WEB_SEARCH_HITL_REQUEST, WEB_SEARCH_RESULT_READY)
    builder.add_edge(
        [WEB_SEARCH_RESULT_READY, LOCAL_DOCUMENT_SEARCH_TOOL],
        TOOL_OUTPUT_SANITIZER,
    )
    builder.add_edge(TOOL_OUTPUT_SANITIZER, DEDUPLICATE_AND_CLUSTER)
    builder.add_edge(DEDUPLICATE_AND_CLUSTER, EVALUATE_EVIDENCE_QUALITY)
    builder.add_conditional_edges(
        EVALUATE_EVIDENCE_QUALITY,
        route_after_evidence_quality,
        {
            REQUEST_HUMAN_REVIEW: REQUEST_HUMAN_REVIEW,
            BUILD_EVIDENCE_MATRIX: BUILD_EVIDENCE_MATRIX,
        },
    )
    builder.add_edge(REQUEST_HUMAN_REVIEW, BUILD_EVIDENCE_MATRIX)
    builder.add_edge(BUILD_EVIDENCE_MATRIX, CHECK_EVIDENCE_SUFFICIENCY)
    builder.add_conditional_edges(
        CHECK_EVIDENCE_SUFFICIENCY,
        route_after_evidence_sufficiency,
        {
            GENERATE_RESEARCH_REPORT: GENERATE_RESEARCH_REPORT,
            CHECK_STEP_BUDGET: CHECK_STEP_BUDGET,
        },
    )
    builder.add_conditional_edges(
        CHECK_STEP_BUDGET,
        route_after_step_budget,
        {
            STRATEGY_ITERATION: STRATEGY_ITERATION,
            PREPARE_DEGRADED_REPORT: PREPARE_DEGRADED_REPORT,
        },
    )
    builder.add_edge(STRATEGY_ITERATION, PLAN_RESEARCH)

    builder.add_edge(PREPARE_DEGRADED_REPORT, GENERATE_RESEARCH_REPORT)
    builder.add_edge(GENERATE_RESEARCH_REPORT, REVIEW_RESEARCH_REPORT)
    builder.add_conditional_edges(
        REVIEW_RESEARCH_REPORT,
        route_after_review,
        {
            GENERATE_RESEARCH_REPORT: GENERATE_RESEARCH_REPORT,
            SAFETY_REVIEW: SAFETY_REVIEW,
        },
    )
    builder.add_conditional_edges(
        SAFETY_REVIEW,
        route_after_safety_review,
        {
            PREPARE_DEGRADED_REPORT: PREPARE_DEGRADED_REPORT,
            PERSIST_OUTPUTS: PERSIST_OUTPUTS,
        },
    )
    builder.add_edge(PERSIST_OUTPUTS, END)

    return builder
