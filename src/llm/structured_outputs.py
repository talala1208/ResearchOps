"""研究节点使用的结构化输出模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
LocalSearchToolName = Literal["local_document", "local_rag", "structured_mock"]


class InputGuardOutput(BaseModel):
    """输入安全检查结构化输出。"""

    safety_pass: bool = Field(description="输入是否允许进入主研究流程")
    safety_risk_level: SafetyRiskLevel = Field(description="输入安全风险等级")
    detected_risks: list[str] = Field(default_factory=list, description="检测到的风险标签")
    downgrade_required: bool = Field(description="是否需要降级回答")
    downgrade_reason: str | None = Field(default=None, description="降级原因")


class SubQuestionOutput(BaseModel):
    """研究子问题输出。"""

    question_id: str
    question: str
    priority: Priority
    required_source_types: list[SourceType]


class ExpectedEvidenceOutput(BaseModel):
    """预期证据输出。"""

    question_id: str
    evidence_description: str
    minimum_count: int = Field(ge=1)
    required_source_types: list[SourceType]
    required_authority_level: AuthorityLevel


class MinimumEvidenceStandardOutput(BaseModel):
    """最低证据标准输出。"""

    question_id: str
    min_total_evidence: int = Field(ge=1)
    min_high_quality_sources: int = Field(ge=0)
    must_include_source_types: list[SourceType]
    allow_degraded_answer: bool


class ResearchPlanOutput(BaseModel):
    """研究规划结构化输出。"""

    research_goal: str
    sub_questions: list[SubQuestionOutput]
    required_source_types: list[SourceType]
    expected_evidence: list[ExpectedEvidenceOutput]
    minimum_evidence_standard: list[MinimumEvidenceStandardOutput]


class SearchTaskOutput(BaseModel):
    """检索任务输出。"""

    task_id: str
    question_id: str
    query: str
    source_type: SourceType
    search_provider: str
    attempt: int = Field(default=1, ge=1, description="检索轮次，首轮应为 1")


class SearchTaskPlanOutput(BaseModel):
    """检索任务规划结构化输出。"""

    search_tasks: list[SearchTaskOutput]
    active_question_ids_after_dispatch: list[str] = Field(default_factory=list)


class ResearchPlanWithSearchTasksOutput(ResearchPlanOutput):
    """初始研究规划与首轮检索任务结构化输出。"""

    search_tasks: list[SearchTaskOutput]
    active_question_ids_after_dispatch: list[str] = Field(default_factory=list)


class WebSearchResultOutput(BaseModel):
    """Web Search SubAgent 单条候选结果。"""

    title: str
    url_or_path: str
    snippet: str
    source_name: str
    published_at: str | None = None
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description="候选结果对当前子问题的相关性评分。",
    )
    answer_coverage_score: float = Field(
        ge=0.0,
        le=1.0,
        description="候选结果覆盖当前子问题答案要点的程度。",
    )
    source_confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="候选结果来源可信度初判。",
    )
    freshness_score: float = Field(
        ge=0.0,
        le=1.0,
        description="候选结果时效性评分。",
    )
    score_reason: str = Field(description="候选分数的简短依据。")


class WebSearchSubAgentResultOutput(BaseModel):
    """Web Search SubAgent 标准输出。"""

    results: list[WebSearchResultOutput] = Field(
        description=(
            "SerpAPI 候选结果对象列表；每项必须是对象，"
            "禁止返回索引数字数组（例如 [1] 或 [0, 2]）。"
        ),
    )
    needs_page_fetch: bool = Field(
        description=(
            "是否需要抓取一条 Serp 结果页正文；"
            "snippet 已覆盖 expected_evidence 时应为 false。"
        ),
    )
    fetch_url: str | None = Field(
        default=None,
        description=(
            "需要抓取时，必须是本批 serpapi 候选中的一条 http(s) URL；"
            "不需要抓取时为 null。"
        ),
    )
    fetch_reason: str | None = Field(
        default=None,
        description="一句话说明为何需要或不需要抓正文。",
    )


class LocalStructuredSearchQueryOutput(BaseModel):
    """本地结构化资料查询规划输出。"""

    search_terms: list[str] = Field(
        min_length=1,
        description="用于本地 SQLite 参数化 LIKE 查询的关键词。",
    )
    sql_search_statement: str = Field(
        description="可观测的结构化查询说明，不直接拼接执行。",
    )
    reasoning: str = Field(description="简短说明为什么选择这些关键词。")


class LocalSearchTaskRouteOutput(BaseModel):
    """单个伞类型 local 任务的工具路由与入参。"""

    task_id: str = Field(description="必须对应该批输入中的某个 SearchTask.task_id")
    tools: list[LocalSearchToolName] = Field(
        min_length=1,
        description="选定的一个或多个具体本地检索工具，按相关性可多选。",
    )
    reasoning: str = Field(description="简短说明为何选择这些本地工具。")
    local_rag_query: str | None = Field(
        default=None,
        description=(
            "当 tools 含 local_rag 时必填非空：向量检索 query；"
            "未选 local_rag 时必须为 null。"
        ),
    )
    local_document_query: str | None = Field(
        default=None,
        description=(
            "当 tools 含 local_document 时必填非空：Markdown 关键词检索 query；"
            "未选 local_document 时必须为 null。"
        ),
    )
    structured_search: LocalStructuredSearchQueryOutput | None = Field(
        default=None,
        description=(
            "当 tools 含 structured_mock 时必填：安全关键词与查询说明；"
            "未选 structured_mock 时必须为 null。"
        ),
    )


class LocalSearchBatchRouteOutput(BaseModel):
    """本轮全部伞类型 local 任务的批量路由输出。"""

    task_routes: list[LocalSearchTaskRouteOutput] = Field(
        min_length=1,
        description="按 task_id 覆盖本批每个伞类型 local 任务的工具选择与入参。",
    )


class ResearchReportOutput(BaseModel):
    """研究报告 LLM 结构化输出。"""

    report_markdown: str = Field(description="完整 Markdown 研究报告正文")
    cited_evidence_ids: list[str] = Field(
        default_factory=list,
        description="报告中实际引用的 evidence_id 列表，必须来自输入证据目录",
    )
    limitations: list[str] = Field(
        default_factory=list,
        description="不确定性、边界与证据缺口说明",
    )


class ResearchReportReviewOutput(BaseModel):
    """研究报告质量 Review 结构化输出。"""

    passed: bool = Field(description="是否达到可进入 Safety Review 的质量门槛")
    report_score: float = Field(description="总分，0 到 1")
    source_coverage_score: float = Field(description="来源覆盖分，0 到 1")
    citation_completeness_score: float = Field(description="引用完整性分，0 到 1")
    groundedness_score: float = Field(description="主张可追溯分，0 到 1")
    boundary_score: float = Field(description="边界与限制披露分，0 到 1")
    over_inference_risk: float = Field(description="过度推断风险，0 到 1，越高越差")
    revision_suggestions: list[str] = Field(
        default_factory=list,
        description="若不通过，给出可执行的修正建议列表",
    )
