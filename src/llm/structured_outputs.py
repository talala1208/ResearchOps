"""前三个真实节点使用的结构化输出模型。"""

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
    "structured_mock",
    "local_document",
]
Priority = Literal["high", "medium", "low"]
AuthorityLevel = Literal["high", "medium", "low"]
SafetyRiskLevel = Literal["low", "medium", "high"]


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
    attempt: int = Field(ge=1)


class SearchTaskPlanOutput(BaseModel):
    """检索任务规划结构化输出。"""

    search_tasks: list[SearchTaskOutput]
    active_question_ids_after_dispatch: list[str] = Field(default_factory=list)
