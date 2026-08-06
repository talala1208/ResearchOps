"""Web 工具稳定性 LangSmith evaluator。

评价目标：
- SerpAPI / Tavily / Context7 是否返回有效数据。
- Playwright 是否成功拿到可用页面正文，或是否遇到 403、about:blank、动态渲染、PDF、反爬、超时等问题。

该 evaluator 是规则型 evaluator，不调用 LLM。
"""

from __future__ import annotations

import json
from typing import Any, Literal, cast

from pydantic import BaseModel, Field


PlaywrightFailureType = Literal[
    "none",
    "no_attempt",
    "403",
    "about_blank",
    "pdf",
    "dynamic_rendering",
    "anti_bot",
    "timeout",
    "empty_content",
    "unknown",
]


class ToolValidity(BaseModel):
    """单个搜索工具有效性。"""

    ok: bool = Field(description="工具调用是否成功")
    valid_result_count: int = Field(ge=0, description="有效结果数量")
    reason: str = Field(description="简短判断理由")


class PlaywrightValidity(BaseModel):
    """Playwright 页面抓取稳定性。"""

    ok: bool = Field(description="Playwright 是否拿到可用页面正文")
    attempted_count: int = Field(ge=0, description="Playwright 抓取尝试次数")
    successful_count: int = Field(ge=0, description="Playwright 成功次数")
    failure_type: PlaywrightFailureType = Field(description="主要失败类型")
    content_length: int = Field(ge=0, description="最大正文长度")
    reason: str = Field(description="简短判断理由")


class WebToolStabilityResult(BaseModel):
    """Web 工具稳定性评估结果。"""

    serpapi: ToolValidity
    tavily: ToolValidity
    context7: ToolValidity
    playwright: PlaywrightValidity
    overall_stability_score: float = Field(ge=0.0, le=1.0, description="整体稳定性分数")
    blocking_issues: list[str] = Field(default_factory=list, description="阻塞问题列表")


def _as_list(value: Any) -> list[Any]:
    """把常见结果容器统一为 list。"""

    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _extract_evaluation_records(outputs: dict[str, Any]) -> list[dict[str, Any]]:
    """从 Run.outputs 中提取 Web 工具紧凑评测记录。"""

    candidate = outputs.get("web_tool_evaluation_records")
    if candidate is None and isinstance(outputs.get("output"), dict):
        candidate = outputs["output"].get("web_tool_evaluation_records")
    return [item for item in _as_list(candidate) if isinstance(item, dict)]


def _evaluate_search_tool(
    evaluation_records: list[dict[str, Any]],
    *,
    tool_name: str,
) -> ToolValidity:
    """评价 SerpAPI / Tavily / Context7 的有效性。"""

    matched = [
        item for item in evaluation_records if item.get("tool_name") == tool_name
    ]
    if not matched:
        return ToolValidity(ok=False, valid_result_count=0, reason="没有工具调用记录")

    valid_count = 0
    errors = []
    for item in matched:
        if not item.get("ok"):
            errors.append(str(item.get("error") or "工具返回 ok=false"))
            continue
        valid_count += max(0, int(item.get("valid_result_count") or 0))

    if valid_count > 0:
        return ToolValidity(ok=True, valid_result_count=valid_count, reason="返回了有效数据")
    return ToolValidity(
        ok=False,
        valid_result_count=0,
        reason="；".join(errors) if errors else "工具成功调用但没有有效数据",
    )


def _evaluate_playwright(
    evaluation_records: list[dict[str, Any]],
) -> PlaywrightValidity:
    """评价 Playwright 页面抓取稳定性。"""

    matched = [
        item
        for item in evaluation_records
        if str(item.get("tool_name", "")).startswith("playwright_mcp_fetch")
    ]
    if not matched:
        return PlaywrightValidity(
            ok=False,
            attempted_count=0,
            successful_count=0,
            failure_type="no_attempt",
            content_length=0,
            reason="没有 Playwright 抓取记录",
        )

    max_content_length = 0
    failure_types: list[PlaywrightFailureType] = []
    successful_count = 0
    for item in matched:
        content_length = max(0, int(item.get("content_length") or 0))
        max_content_length = max(max_content_length, content_length)
        failure_type_value = str(item.get("failure_type") or "unknown")
        failure_type = cast(
            PlaywrightFailureType,
            failure_type_value
            if failure_type_value in PlaywrightFailureType.__args__
            else "unknown",
        )
        if item.get("ok") and failure_type == "none":
            successful_count += 1
            continue
        failure_types.append(failure_type)

    if successful_count > 0:
        return PlaywrightValidity(
            ok=True,
            attempted_count=len(matched),
            successful_count=successful_count,
            failure_type="none",
            content_length=max_content_length,
            reason="至少一次 Playwright 抓取返回可用正文",
        )

    primary_failure = failure_types[0] if failure_types else "unknown"
    return PlaywrightValidity(
        ok=False,
        attempted_count=len(matched),
        successful_count=0,
        failure_type=primary_failure,
        content_length=max_content_length,
        reason=f"Playwright 未返回可用正文，主要失败类型：{primary_failure}",
    )


def evaluate_web_tool_stability_from_outputs(outputs: dict[str, Any]) -> WebToolStabilityResult:
    """从 Run.outputs 计算 Web 工具稳定性。"""

    evaluation_records = _extract_evaluation_records(outputs)
    serpapi = _evaluate_search_tool(evaluation_records, tool_name="serp_api_search")
    tavily = _evaluate_search_tool(evaluation_records, tool_name="tavily_mcp_search")
    context7 = _evaluate_search_tool(
        evaluation_records,
        tool_name="context7_mcp_query",
    )
    playwright = _evaluate_playwright(evaluation_records)

    attempted_tool_names = {
        str(item.get("tool_name", ""))
        for item in evaluation_records
    }
    weighted_results = [
        (0.25, serpapi.ok, "serp_api_search" in attempted_tool_names),
        (0.25, tavily.ok, "tavily_mcp_search" in attempted_tool_names),
        (0.20, context7.ok, "context7_mcp_query" in attempted_tool_names),
        (
            0.30,
            playwright.ok,
            any(name.startswith("playwright_mcp_fetch") for name in attempted_tool_names),
        ),
    ]
    attempted_weight = sum(weight for weight, _, attempted in weighted_results if attempted)
    successful_weight = sum(
        weight
        for weight, ok, attempted in weighted_results
        if attempted and ok
    )
    score = successful_weight / attempted_weight if attempted_weight else 0.0

    blocking_issues = []
    if "serp_api_search" in attempted_tool_names and not serpapi.ok:
        blocking_issues.append(f"SerpAPI 无有效数据：{serpapi.reason}")
    if "tavily_mcp_search" in attempted_tool_names and not tavily.ok:
        blocking_issues.append(f"Tavily 无有效数据：{tavily.reason}")
    if "context7_mcp_query" in attempted_tool_names and not context7.ok:
        blocking_issues.append(f"Context7 无有效数据：{context7.reason}")
    playwright_attempted = any(
        name.startswith("playwright_mcp_fetch") for name in attempted_tool_names
    )
    if playwright_attempted and not playwright.ok:
        blocking_issues.append(
            f"Playwright 抓取失败：{playwright.failure_type}；{playwright.reason}"
        )

    return WebToolStabilityResult(
        serpapi=serpapi,
        tavily=tavily,
        context7=context7,
        playwright=playwright,
        overall_stability_score=round(score, 4),
        blocking_issues=blocking_issues,
    )


def web_tool_stability_evaluator(
    inputs: dict,
    reference_outputs: dict,
    outputs: dict,
) -> dict:
    """LangSmith Web 工具稳定性 evaluator。

    LangSmith 会自动传入：
    - inputs: Example.inputs
    - reference_outputs: Example.outputs
    - outputs: Run.outputs
    """

    result = evaluate_web_tool_stability_from_outputs(outputs)
    evaluation_records = _extract_evaluation_records(outputs)
    question = inputs.get("question") or inputs.get("user_query") or ""
    comment = {
        "question": question,
        "serpapi": result.serpapi.model_dump(),
        "tavily": result.tavily.model_dump(),
        "context7": result.context7.model_dump(),
        "playwright": result.playwright.model_dump(),
        "blocking_issues": result.blocking_issues,
    }
    if not evaluation_records:
        return {
            "key": "web_tool_stability",
            "value": "not_applicable",
            "comment": json.dumps(comment, ensure_ascii=False),
        }
    return {
        "key": "web_tool_stability",
        "score": result.overall_stability_score,
        "comment": json.dumps(comment, ensure_ascii=False),
    }
