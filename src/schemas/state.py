"""ResearchOps Agent 的状态结构定义。"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, NotRequired, TypedDict


SourceType = Literal[
    "official_docs",
    "pricing_page",
    "changelog",
    "blog",
    "community",
    "github",
    "product_directory",
    "traffic_data",
    "local",
    "structured_mock",
    "local_document",
    "local_rag",
]

Priority = Literal["high", "medium", "low"]
AuthorityLevel = Literal["high", "medium", "low"]
SafetyRiskLevel = Literal["low", "medium", "high"]


class SubQuestion(TypedDict):
    """研究子问题。"""

    question_id: str
    question: str
    priority: Priority
    required_source_types: list[SourceType]


class ExpectedEvidence(TypedDict):
    """子问题对应的预期证据。"""

    question_id: str
    evidence_description: str
    minimum_count: int
    required_source_types: list[SourceType]
    required_authority_level: AuthorityLevel


class MinimumEvidenceStandard(TypedDict):
    """子问题对应的最低证据标准。"""

    question_id: str
    min_total_evidence: int
    min_high_quality_sources: int
    must_include_source_types: list[SourceType]
    allow_degraded_answer: bool


class SearchTask(TypedDict):
    """检索任务。"""

    task_id: str
    question_id: str
    query: str
    source_type: SourceType
    search_provider: str
    attempt: int


class WebToolEvaluationRecord(TypedDict):
    """Web 工具调用的紧凑确定性评测记录。"""

    tool_name: str
    ok: bool
    valid_result_count: int
    content_length: int
    failure_type: str | None
    error: str | None


class EvidenceItem(TypedDict):
    """标准化证据项。"""

    evidence_id: str
    question_id: str
    source_type: SourceType
    source_name: str
    url_or_path: str
    title: str
    snippet: str
    published_at: str | None
    collected_by: str
    freshness_score: float
    relevance_score: float
    answer_coverage_score: float
    source_confidence_score: float
    reliability_score: float
    score_reason: str | None
    used_in_final_report: bool
    content_path: NotRequired[str | None]
    library_id: NotRequired[str | None]
    score_bucket: NotRequired[str]
    scored_by: NotRequired[str]


class ConflictItem(TypedDict):
    """证据冲突项。"""

    conflict_id: str
    question_id: str
    evidence_ids: list[str]
    conflict_summary: str
    preferred_evidence_id: str | None
    hitl_need_score: float
    hitl_triggered: bool


class QuestionEvidenceStatus(TypedDict):
    """单个子问题的证据满足状态。

    Priority 会通过 `priority_weight` 影响整体证据充足性评分。
    """

    question_id: str
    priority: Priority
    priority_weight: int
    required_evidence_count: int
    collected_evidence_count: int
    high_quality_evidence_count: int
    required_source_types: list[SourceType]
    covered_source_types: list[SourceType]
    minimum_standard_met: bool
    weighted_score: float
    missing_reason: str | None


class EvidenceSufficiencyResult(TypedDict):
    """整体证据充足性判断结果。

    `question_status` 的 key 必须是 `question_id`，并且必须能在
    `ResearchState.sub_questions` 中找到同名 key。
    """

    sufficient: bool
    overall_score: float
    threshold: float
    high_priority_all_met: bool
    question_status: dict[str, QuestionEvidenceStatus]
    insufficient_question_ids: list[str]
    degradation_recommended: bool
    degradation_reason: str | None


class StateEntityIndex(TypedDict):
    """State 内业务对象之间的显式关联索引。

    这个结构只保存 ID，不复制业务正文。后续节点读取这些必须存在的
    业务对象时，应使用 `state["xxx"][id]` 或显式校验，而不是静默 `.get()`。
    """

    question_ids: list[str]
    evidence_ids_by_question_id: dict[str, list[str]]
    conflict_ids_by_question_id: dict[str, list[str]]
    search_task_ids_by_question_id: dict[str, list[str]]
    used_evidence_ids: list[str]


class ReviewResult(TypedDict):
    """报告质量 Review 结果。"""

    passed: bool
    report_score: float
    source_coverage_score: float
    citation_completeness_score: float
    groundedness_score: float
    boundary_score: float
    over_inference_risk: float
    revision_suggestions: list[str]


class SafetyReviewResult(TypedDict):
    """安全审查结果。"""

    safety_pass: bool
    safety_risk_level: SafetyRiskLevel
    detected_risks: list[str]
    downgrade_required: bool
    downgrade_reason: str | None


class EvaluationMetrics(TypedDict):
    """最终运行指标。"""

    total_steps: int
    search_steps: int
    max_search_steps: int
    review_revision_count: int
    max_review_revisions: int
    safety_revision_count: int
    max_safety_revisions: int
    total_latency_ms: int
    total_tokens: int
    evidence_count: int
    final_citation_count: int
    conflict_count: int
    discarded_candidate_count: int
    degraded: bool
    degradation_reason: str | None
    review_score: float | None
    safety_risk_level: str | None
    source_contribution: dict[str, int]
    web_hitl_trigger_count_by_source: dict[str, int]


class ResearchInput(TypedDict):
    """LangGraph Studio 入口输入。"""

    user_query: str


class ResearchState(TypedDict, total=False):
    """ResearchOps Agent 全局状态。

    关联约定：
    - `sub_questions` 是 `question_id -> SubQuestion` 的唯一问题目录。
    - `expected_evidence`、`minimum_evidence_standard`、
      `evidence_sufficiency_result.question_status` 必须使用同一组 `question_id`。
    - `evidence_items` 是 `evidence_id -> EvidenceItem` 的唯一证据目录。
    - `conflicts` 是 `conflict_id -> ConflictItem` 的唯一冲突目录。
    - 跨结构关联只保存 ID，不复制问题正文或证据正文。
    - 必须存在的业务对象读取时应使用 `[]` 或显式校验，不要用 `.get()` 隐藏上游错误。
    """

    # 输入与规划
    user_query: str
    input_guard_result: SafetyReviewResult
    research_goal: str
    sub_questions: dict[str, SubQuestion]  # key: question_id
    required_source_types: list[SourceType]
    expected_evidence: dict[str, ExpectedEvidence]  # key: question_id
    minimum_evidence_standard: dict[str, MinimumEvidenceStandard]  # key: question_id
    entity_index: StateEntityIndex

    # 检索任务
    active_question_ids: list[str]
    search_tasks: list[SearchTask]
    planning_mode: Literal["initial", "iteration"]
    search_dispatch_mode: Literal["initial", "iteration"]
    search_attempt: int
    search_iteration_context: dict

    # 工具输出与紧凑观测记录
    web_search_results: list[dict]
    web_tool_evaluation_records: Annotated[
        list[WebToolEvaluationRecord],
        operator.add,
    ]
    web_hitl_required: bool
    web_hitl_reason: str | None
    web_hitl_decisions: list[dict]
    local_document_results: list[dict]

    # 证据治理（evidence_items / conflicts 由 evaluate_evidence_quality 整表替换）
    evidence_items: dict[str, EvidenceItem]  # key: evidence_id
    evidence_clusters: list[dict]
    discarded_candidate_count: int
    evidence_matrix: dict
    evidence_sufficiency_result: EvidenceSufficiencyResult
    conflicts: dict[str, ConflictItem]  # key: conflict_id

    # HITL
    hitl_required: bool
    hitl_decisions: list[dict]

    # 检索 / 迭代预算
    search_steps: int
    max_search_steps: int
    step_budget_exhausted: bool
    search_budget_remaining: int
    step_budget_reason: str | None
    iteration_count: int
    repeated_action_count: dict[str, int]

    # 报告 Review 修正预算
    review_revision_count: int
    max_review_revisions: int

    # 安全修正预算
    safety_revision_count: int
    max_safety_revisions: int

    # 报告与输出
    report_draft: str
    review_result: ReviewResult
    safety_review_result: SafetyReviewResult
    degraded: bool
    degradation_reason: str | None
    final_report: str
    final_report_path: str | None
    executed_mermaid: str | None
    executed_mermaid_png_path: str | None
    output_artifacts: dict[str, str]

    # 观测与指标
    executed_nodes: Annotated[list[str], operator.add]
    evaluation_metrics: EvaluationMetrics
