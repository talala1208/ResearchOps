"""Web 工具稳定性 LangSmith evaluator。

评价目标：
- SerpAPI / Tavily / Context7 是否返回有效数据。
- Playwright 是否成功拿到可用页面正文，或是否遇到 403、about:blank、动态渲染、PDF、反爬、超时等问题。

该 evaluator 是规则型 evaluator，不调用 LLM。
"""

from __future__ import annotations

import json
from typing import Any, Literal

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


def _as_dict(value: Any) -> dict[str, Any]:
    """把 JSON 字符串或 dict 统一为 dict。"""

    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw_text": value}
        return parsed if isinstance(parsed, dict) else {"raw_value": parsed}
    return {"raw_value": value}


def _as_list(value: Any) -> list[Any]:
    """把常见结果容器统一为 list。"""

    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _text_of(value: Any) -> str:
    """提取用于规则判断的文本。"""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _extract_tool_outputs(outputs: dict[str, Any]) -> list[dict[str, Any]]:
    """从 Run.outputs 中提取 web_search_subagent 的工具调用记录。"""

    candidate = (
        outputs.get("tool_outputs")
        or outputs.get("web_tool_outputs")
        or outputs.get("web_search_tool_outputs")
        or outputs.get("raw_tool_outputs")
    )
    if candidate is None and isinstance(outputs.get("output"), dict):
        nested_output = outputs["output"]
        candidate = (
            nested_output.get("tool_outputs")
            or nested_output.get("web_tool_outputs")
            or nested_output.get("web_search_tool_outputs")
            or nested_output.get("raw_tool_outputs")
        )
    return [item for item in _as_list(candidate) if isinstance(item, dict)]


def _valid_serpapi_result_count(tool_output: dict[str, Any]) -> int:
    """统计 SerpAPI 有效结果数。"""

    output = _as_dict(tool_output.get("output"))
    results = _as_list(output.get("results") or output.get("organic_results"))
    count = 0
    for item in results:
        item_dict = _as_dict(item)
        title = item_dict.get("title")
        url = item_dict.get("url") or item_dict.get("link")
        snippet = item_dict.get("snippet")
        if title and url and snippet:
            count += 1
    return count


def _valid_tavily_result_count(tool_output: dict[str, Any]) -> int:
    """统计 Tavily 有效结果数。"""

    output = _as_dict(tool_output.get("output"))
    results = _as_list(output.get("results"))
    if not results and output.get("raw_text"):
        return 1 if len(str(output["raw_text"]).strip()) >= 100 else 0

    count = 0
    for item in results:
        item_dict = _as_dict(item)
        url = item_dict.get("url")
        text = item_dict.get("content") or item_dict.get("snippet") or item_dict.get("title")
        if url and text:
            count += 1
    return count


def _valid_context7_result_count(tool_output: dict[str, Any]) -> int:
    """判断 Context7 是否返回有效文档内容。"""

    output_text = _text_of(tool_output.get("output"))
    lowered = output_text.lower()
    invalid_markers = [
        "no docs",
        "not found",
        "找不到",
        "没有找到",
        "error",
        "缺少",
        "无法使用",
    ]
    if not output_text.strip():
        return 0
    if any(marker in lowered for marker in invalid_markers):
        return 0
    return 1


def _content_length_from_playwright_output(output: Any) -> int:
    """估算 Playwright 抓取正文长度。"""

    output_dict = _as_dict(output)
    text_candidates = [
        output_dict.get("content"),
        output_dict.get("text"),
        output_dict.get("markdown"),
        output_dict.get("page_text"),
        output_dict.get("raw_text"),
        output_dict.get("snapshot"),
    ]
    return max((len(_text_of(item).strip()) for item in text_candidates), default=0)


def _classify_playwright_failure(tool_output: dict[str, Any]) -> PlaywrightFailureType:
    """识别 Playwright 失败类型。"""

    output = _as_dict(tool_output.get("output"))
    text = " ".join(
        [
            _text_of(tool_output.get("error")),
            _text_of(tool_output.get("output")),
            _text_of(tool_output.get("input")),
        ]
    )
    lowered = text.lower()
    url = _as_dict(tool_output.get("input")).get("url") or output.get("url")

    if "timeout" in lowered or "timed out" in lowered or "超时" in lowered:
        return "timeout"
    if "403" in lowered or "access denied" in lowered or "forbidden" in lowered:
        return "403"
    if url == "about:blank" or "about:blank" in lowered:
        return "about_blank"
    if "captcha" in lowered or "just a moment" in lowered or "cloudflare" in lowered:
        return "anti_bot"
    if "application/pdf" in lowered or str(url).lower().endswith(".pdf"):
        return "pdf"
    if "enable javascript" in lowered or "requires javascript" in lowered or "动态渲染" in lowered:
        return "dynamic_rendering"
    if _content_length_from_playwright_output(output) < 500:
        return "empty_content"
    return "unknown"


def _evaluate_search_tool(
    tool_outputs: list[dict[str, Any]],
    *,
    tool_name: str,
) -> ToolValidity:
    """评价 SerpAPI / Tavily / Context7 的有效性。"""

    matched = [item for item in tool_outputs if item.get("tool_name") == tool_name]
    if not matched:
        return ToolValidity(ok=False, valid_result_count=0, reason="没有工具调用记录")

    valid_count = 0
    errors = []
    for item in matched:
        if not item.get("ok"):
            errors.append(str(item.get("error") or "工具返回 ok=false"))
            continue
        if tool_name == "serp_api_search":
            valid_count += _valid_serpapi_result_count(item)
        elif tool_name == "tavily_mcp_search":
            valid_count += _valid_tavily_result_count(item)
        elif tool_name == "context7_mcp_query":
            valid_count += _valid_context7_result_count(item)

    if valid_count > 0:
        return ToolValidity(ok=True, valid_result_count=valid_count, reason="返回了有效数据")
    return ToolValidity(
        ok=False,
        valid_result_count=0,
        reason="；".join(errors) if errors else "工具成功调用但没有有效数据",
    )


def _evaluate_playwright(tool_outputs: list[dict[str, Any]]) -> PlaywrightValidity:
    """评价 Playwright 页面抓取稳定性。"""

    matched = [
        item
        for item in tool_outputs
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
        content_length = _content_length_from_playwright_output(item.get("output"))
        max_content_length = max(max_content_length, content_length)
        failure_type = _classify_playwright_failure(item)
        if item.get("ok") and failure_type == "unknown":
            successful_count += 1
            continue
        if item.get("ok") and content_length >= 500:
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

    tool_outputs = _extract_tool_outputs(outputs)
    serpapi = _evaluate_search_tool(tool_outputs, tool_name="serp_api_search")
    tavily = _evaluate_search_tool(tool_outputs, tool_name="tavily_mcp_search")
    context7 = _evaluate_search_tool(tool_outputs, tool_name="context7_mcp_query")
    playwright = _evaluate_playwright(tool_outputs)

    score = 0.0
    score += 0.25 if serpapi.ok else 0.0
    score += 0.25 if tavily.ok else 0.0
    score += 0.20 if context7.ok else 0.0
    score += 0.30 if playwright.ok else 0.0

    blocking_issues = []
    if not serpapi.ok:
        blocking_issues.append(f"SerpAPI 无有效数据：{serpapi.reason}")
    if not tavily.ok:
        blocking_issues.append(f"Tavily 无有效数据：{tavily.reason}")
    if not context7.ok:
        blocking_issues.append(f"Context7 无有效数据：{context7.reason}")
    if not playwright.ok:
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
    question = inputs.get("question") or inputs.get("user_query") or ""
    comment = {
        "question": question,
        "serpapi": result.serpapi.model_dump(),
        "tavily": result.tavily.model_dump(),
        "context7": result.context7.model_dump(),
        "playwright": result.playwright.model_dump(),
        "blocking_issues": result.blocking_issues,
    }
    return {
        "key": "web_tool_stability",
        "score": result.overall_stability_score,
        "comment": json.dumps(comment, ensure_ascii=False),
    }
