"""Web Search SubAgent 工具。

当前实现采用“确定性调度 + LLM 汇总打分 + 条件正文抓取”：
- 不再使用内部 `create_agent` ReAct 循环，避免工具自由循环和重复 trace。
- 只调度 SerpAPI / Tavily / Context7 / headless Playwright 页面读取工具。
- SerpAPI 按 NUM 取回后全部进入汇总打分，再按 KEEP_TOP_N 保留；Tavily 仅由 MAX_RESULTS 控制。
- SerpAPI 结果页正文抓取发生在首次汇总打分之后：问题整体分达标则跳过；
  未达标且 SerpAPI top1（仅看 relevance / source_confidence）够高时抓取正文，
  并由代码写入该条候选，不再二次汇总打分。
- 进入汇总 LLM 前压缩 tool_outputs，原始输出仍用于评测记录。
- 不包含 DevTools；DevTools 只属于 Web HITL 节点。
- 不包含 Claude Code / Codex；外部 Agent worker 只属于 strategy_iteration。
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any

from langchain.tools import tool
from langsmith import traceable
from serpapi import GoogleSearch

from src.llm.chat import build_chat_model
from src.llm.prompt_loader import load_prompt, render_prompt_template
from src.llm.structured_outputs import WebSearchSubAgentResultOutput
from src.tools.online_mcp_tools import (
    context7_mcp_query,
    playwright_mcp_fetch_page,
    tavily_mcp_search,
)


JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*(?P<body>.*?)\s*```$", re.DOTALL)
DOC_SOURCE_TYPES = {"official_docs", "github", "changelog"}
WEB_SOURCE_TYPES = {
    "official_docs",
    "pricing_page",
    "changelog",
    "blog",
    "community",
    "github",
    "product_directory",
    "traffic_data",
}
URL_PATTERN = re.compile(r"https?://[^\s]+")
DEFAULT_SERPAPI_NUM = 5
DEFAULT_SERPAPI_KEEP_TOP_N = 3
DEFAULT_TAVILY_MAX_RESULTS = 5
DEFAULT_CONTEXT7_MAX_CHARS = 4000
DEFAULT_PLAYWRIGHT_MAX_CHARS = 3000
DEFAULT_QUESTION_PASS_SCORE = 0.75
DEFAULT_SERPAPI_TOP1_HIGH_SCORE = 0.7
_TOOL_CONCURRENCY_LIMITS = {
    "serp_api_search": 3,
    "tavily_mcp_search": 1,
    "context7_mcp_query": 2,
}
_PLAYWRIGHT_CONCURRENCY_LIMIT = 1
_TOOL_SEMAPHORES = {
    tool_name: threading.Semaphore(limit)
    for tool_name, limit in _TOOL_CONCURRENCY_LIMITS.items()
}
_PLAYWRIGHT_SEMAPHORE = threading.Semaphore(_PLAYWRIGHT_CONCURRENCY_LIMIT)


def _read_bounded_int_env(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """读取带上下限的整数环境变量；非法值回退默认。"""

    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _read_bounded_float_env(
    name: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    """读取带上下限的浮点环境变量；非法值回退默认。"""

    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        value = float(raw_value)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _serpapi_num() -> int:
    """SerpAPI 请求条数。"""

    return _read_bounded_int_env(
        "WEB_SEARCH_SERPAPI_NUM",
        default=DEFAULT_SERPAPI_NUM,
        minimum=1,
        maximum=10,
    )


def _serpapi_keep_top_n() -> int:
    """汇总打分后保留的 SerpAPI 结果条数。"""

    return _read_bounded_int_env(
        "WEB_SEARCH_SERPAPI_KEEP_TOP_N",
        default=DEFAULT_SERPAPI_KEEP_TOP_N,
        minimum=1,
        maximum=10,
    )


def _tavily_max_results() -> int:
    """Tavily 请求条数。"""

    return _read_bounded_int_env(
        "WEB_SEARCH_TAVILY_MAX_RESULTS",
        default=DEFAULT_TAVILY_MAX_RESULTS,
        minimum=1,
        maximum=10,
    )


def _context7_max_chars() -> int:
    """Context7 文档正文截断长度。"""

    return _read_bounded_int_env(
        "WEB_SEARCH_CONTEXT7_MAX_CHARS",
        default=DEFAULT_CONTEXT7_MAX_CHARS,
        minimum=500,
        maximum=20000,
    )


def _playwright_max_chars() -> int:
    """Playwright 正文截断长度。"""

    return _read_bounded_int_env(
        "WEB_SEARCH_PLAYWRIGHT_MAX_CHARS",
        default=DEFAULT_PLAYWRIGHT_MAX_CHARS,
        minimum=500,
        maximum=20000,
    )


def _question_pass_score() -> float:
    """单个 Web 问题汇总后的通过分阈值。"""

    return _read_bounded_float_env(
        "WEB_SEARCH_QUESTION_PASS_SCORE",
        default=DEFAULT_QUESTION_PASS_SCORE,
        minimum=0.0,
        maximum=1.0,
    )


def _serpapi_top1_high_score() -> float:
    """触发 SerpAPI top1 正文抓取的门槛分（relevance/confidence 均值）。"""

    return _read_bounded_float_env(
        "WEB_SEARCH_SERPAPI_TOP1_HIGH_SCORE",
        default=DEFAULT_SERPAPI_TOP1_HIGH_SCORE,
        minimum=0.0,
        maximum=1.0,
    )


def _extract_url(value: str) -> str | None:
    """从检索 query 中提取 URL。"""

    match = URL_PATTERN.search(value)
    if match is None:
        return None
    return match.group(0)


def _strip_json_markdown_fence(raw_result: str) -> str:
    """移除模型可能包裹的 Markdown JSON 代码块。"""

    stripped = raw_result.strip()
    match = JSON_FENCE_PATTERN.match(stripped)
    if match is None:
        return stripped
    return match.group("body").strip()


def _parse_json_object_from_model_output(raw_result: str) -> dict[str, Any]:
    """解析 SubAgent 输出的 JSON 对象。"""

    normalized = _strip_json_markdown_fence(raw_result)
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Web Search SubAgent 未返回合法 JSON：{raw_result}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Web Search SubAgent 返回值必须是 JSON 对象")
    return parsed


@tool
def serp_api_search(query: str) -> str:
    """通过 SerpAPI 搜索网页资料。"""

    api_key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("缺少 SERPAPI_API_KEY，无法调用 SerpAPI。")

    num = _serpapi_num()
    search = GoogleSearch(
        {
            "engine": "google",
            "q": query,
            "api_key": api_key,
            "num": num,
            "hl": "zh-cn",
        }
    )
    payload = search.get_dict()
    if payload.get("error"):
        raise RuntimeError(f"SerpAPI 调用失败：{payload['error']}")

    results = []
    for item in payload.get("organic_results", [])[:num]:
        link = item.get("link")
        results.append(
            {
                "title": item.get("title"),
                "url": link,
                "snippet": item.get("snippet"),
                "source": item.get("source") or item.get("displayed_link"),
                "published_at": item.get("date"),
                "position": item.get("position"),
            }
        )

    return json.dumps(
        {
            "provider": "serpapi",
            "ok": True,
            "query": query,
            "results": results,
            "search_metadata": {
                "id": payload.get("search_metadata", {}).get("id"),
                "status": payload.get("search_metadata", {}).get("status"),
            },
        },
        ensure_ascii=False,
    )


def _loads_tool_json(raw: str) -> Any:
    """解析工具 JSON 字符串；解析失败时保留原始文本。"""

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _semaphore_for_tool(name: str) -> threading.Semaphore | None:
    """按工具名返回分级并发信号量。"""

    if name.startswith("playwright_mcp_fetch"):
        return _PLAYWRIGHT_SEMAPHORE
    return _TOOL_SEMAPHORES.get(name)


def _call_tool(name: str, tool_obj: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """确定性调用单个 LangChain tool 并返回结构化调用记录。"""

    semaphore = _semaphore_for_tool(name)

    def _invoke() -> dict[str, Any]:
        try:
            raw_output = tool_obj.invoke(payload)
            return {
                "tool_name": name,
                "ok": True,
                "input": payload,
                "output": _loads_tool_json(raw_output),
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 - 工具失败要进入 HITL 原因
            return {
                "tool_name": name,
                "ok": False,
                "input": payload,
                "output": None,
                "error": str(exc),
            }

    if semaphore is None:
        return _invoke()
    with semaphore:
        return _invoke()


def _score_or_default(value: Any, default: float) -> float:
    """把候选分规范到 [0, 1]，非法值回退默认。"""

    try:
        if value is None:
            return default
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(score, 1.0))


def _candidate_composite_score(item: dict[str, Any]) -> float:
    """计算单条候选综合分，与证据层可靠性权重一致。"""

    return round(
        _score_or_default(item.get("source_confidence_score"), 0.5) * 0.35
        + _score_or_default(item.get("freshness_score"), 0.5) * 0.15
        + _score_or_default(item.get("relevance_score"), 0.5) * 0.3
        + _score_or_default(item.get("answer_coverage_score"), 0.5) * 0.2,
        4,
    )


def _serpapi_top1_gate_score(item: dict[str, Any]) -> float:
    """SerpAPI top1 门槛分：仅看 relevance 与 source_confidence。"""

    return round(
        (
            _score_or_default(item.get("relevance_score"), 0.0)
            + _score_or_default(item.get("source_confidence_score"), 0.0)
        )
        / 2.0,
        4,
    )


def _is_serpapi_result(item: dict[str, Any]) -> bool:
    """判断候选是否来自 SerpAPI。"""

    source_name = str(item.get("source_name") or "").lower()
    return "serp" in source_name


def _question_overall_score(result: dict[str, Any]) -> float:
    """问题整体分：全部候选综合分均值。"""

    scores = [
        _candidate_composite_score(item)
        for item in result.get("results", [])
        if isinstance(item, dict)
    ]
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 4)


def _keep_serpapi_top_n_after_score(result: dict[str, Any]) -> dict[str, Any]:
    """汇总打分后仅对 SerpAPI 候选按综合分保留 top N。"""

    results = result.get("results")
    if not isinstance(results, list):
        return result

    serp_results: list[dict[str, Any]] = []
    other_results: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        if _is_serpapi_result(item):
            serp_results.append(item)
        else:
            other_results.append(item)

    keep_n = _serpapi_keep_top_n()
    serp_kept = sorted(
        serp_results,
        key=_candidate_composite_score,
        reverse=True,
    )[:keep_n]
    return {
        **result,
        "results": other_results + serp_kept,
    }


def _serpapi_top1_fetch_url(result: dict[str, Any]) -> str | None:
    """整体分未达标且 SerpAPI top1 门槛够高时，返回应抓取的 URL。"""

    if _question_overall_score(result) >= _question_pass_score():
        return None

    serp_results = [
        item
        for item in result.get("results", [])
        if isinstance(item, dict) and _is_serpapi_result(item)
    ]
    if not serp_results:
        return None

    top1 = max(serp_results, key=_serpapi_top1_gate_score)
    if _serpapi_top1_gate_score(top1) < _serpapi_top1_high_score():
        return None

    url = top1.get("url_or_path")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    return None


def _enrich_result_with_page_content(
    result: dict[str, Any],
    *,
    fetch_url: str,
    page_output: dict[str, Any],
) -> dict[str, Any]:
    """把 Playwright 抓取的正文确定性写入对应 SerpAPI 候选。"""

    compact = _compact_playwright_output(
        page_output.get("output"),
        url=fetch_url,
    )
    content = str(compact.get("content") or "").strip()
    if not content:
        return result

    page_title = compact.get("title")
    updated_results: list[Any] = []
    for item in result.get("results", []):
        if not isinstance(item, dict):
            updated_results.append(item)
            continue
        if item.get("url_or_path") != fetch_url:
            updated_results.append(item)
            continue
        enriched = dict(item)
        if isinstance(page_title, str) and page_title.strip():
            enriched["title"] = page_title.strip()
        enriched["snippet"] = content
        updated_results.append(enriched)

    return {
        **result,
        "results": updated_results,
    }


@traceable(name="collect_web_tool_outputs", run_type="chain")
def _collect_web_tool_outputs(task: dict[str, Any]) -> list[dict[str, Any]]:
    """按固定顺序收集 Web 工具输出，不让 LLM 自行循环调用工具。"""

    query = task["query"]
    source_type = task["source_type"]
    tool_outputs = []

    if source_type in WEB_SOURCE_TYPES:
        tool_outputs.append(
            _call_tool(
                "tavily_mcp_search",
                tavily_mcp_search,
                {"query": query, "max_results": _tavily_max_results()},
            )
        )

    if source_type in DOC_SOURCE_TYPES:
        tool_outputs.append(
            _call_tool("context7_mcp_query", context7_mcp_query, {"topic": query})
        )

    tool_outputs.append(
        _call_tool(
            "serp_api_search",
            serp_api_search,
            {"query": query},
        )
    )

    url = _extract_url(query)
    if url is not None:
        tool_outputs.append(
            _call_tool(
                "playwright_mcp_fetch_page",
                playwright_mcp_fetch_page,
                {"url": url},
            )
        )

    return tool_outputs


def _maybe_fetch_serpapi_page_and_enrich(
    tool_outputs: list[dict[str, Any]],
    result: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """打分后按阈值条件抓取 SerpAPI top1 正文，并由代码写入该条候选。"""

    fetch_url = _serpapi_top1_fetch_url(result)
    if fetch_url is None:
        return tool_outputs, result

    page_output = _call_tool(
        "playwright_mcp_fetch_serpapi_result_page_1",
        playwright_mcp_fetch_page,
        {"url": fetch_url},
    )
    updated_outputs = [*tool_outputs, page_output]
    if not page_output.get("ok"):
        return updated_outputs, result

    enriched = _enrich_result_with_page_content(
        result,
        fetch_url=fetch_url,
        page_output=page_output,
    )
    return updated_outputs, enriched


def _truncate_text(value: Any, max_chars: int) -> str:
    """截断文本并标记省略。"""

    text = value if isinstance(value, str) else _tool_output_text(value)
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"


def _compact_search_result_item(item: Any) -> dict[str, Any] | None:
    """压缩单条搜索结果，只保留汇总所需字段。"""

    if not isinstance(item, dict):
        return None
    url = item.get("url") or item.get("link") or item.get("url_or_path")
    title = item.get("title")
    snippet = item.get("snippet") or item.get("content") or item.get("description")
    published_at = item.get("published_at") or item.get("date") or item.get("published_date")
    if not title and not url and not snippet:
        return None
    return {
        "title": title,
        "url": url,
        "snippet": snippet,
        "published_at": published_at,
    }


def _extract_tavily_result_items(output: Any) -> list[Any]:
    """尽量从 Tavily 原始输出提取结果列表。"""

    if isinstance(output, list):
        return output
    if not isinstance(output, dict):
        return []
    for key in ("results", "organic_results", "data"):
        value = output.get(key)
        if isinstance(value, list):
            return value
    raw_result = output.get("raw_result")
    if isinstance(raw_result, str):
        try:
            parsed = json.loads(raw_result)
        except json.JSONDecodeError:
            return []
        return _extract_tavily_result_items(parsed)
    if isinstance(raw_result, dict):
        return _extract_tavily_result_items(raw_result)
    if isinstance(raw_result, list):
        return raw_result
    return []


def _compact_playwright_output(output: Any, *, url: str | None) -> dict[str, Any]:
    """压缩 Playwright 输出，只保留 url / title / 截断正文。"""

    output_dict = _tool_output_as_dict(output)
    title = (
        output_dict.get("title")
        or output_dict.get("page_title")
        or output_dict.get("name")
    )
    candidates = [
        output_dict.get("content"),
        output_dict.get("text"),
        output_dict.get("snapshot"),
        output_dict.get("page_text"),
        output_dict.get("snapshot_result"),
        output_dict.get("raw_text"),
        output_dict.get("navigation_result"),
    ]
    body = ""
    for candidate in candidates:
        text = _tool_output_text(candidate).strip()
        if len(text) > len(body):
            body = text
    if not body and not isinstance(output, dict):
        body = _tool_output_text(output).strip()
    return {
        "url": url or output_dict.get("url"),
        "title": title,
        "content": _truncate_text(body, _playwright_max_chars()),
    }


def _compact_context7_output(output: Any) -> dict[str, Any]:
    """压缩 Context7 输出，截断文档正文。"""

    max_chars = _context7_max_chars()
    if isinstance(output, dict):
        raw_result = output.get("raw_result")
        if isinstance(raw_result, str):
            try:
                parsed = json.loads(raw_result)
            except json.JSONDecodeError:
                return {
                    "provider": output.get("provider"),
                    "ok": output.get("ok", True),
                    "docs": _truncate_text(raw_result, max_chars),
                }
            if isinstance(parsed, dict):
                return {
                    "provider": output.get("provider"),
                    "ok": output.get("ok", True),
                    "library_id": parsed.get("library_id"),
                    "resolve_result": _truncate_text(
                        parsed.get("resolve_result"),
                        max(500, max_chars // 4),
                    ),
                    "docs_result": _truncate_text(parsed.get("docs_result"), max_chars),
                }
        return {
            "provider": output.get("provider"),
            "ok": output.get("ok", True),
            "library_id": output.get("library_id"),
            "resolve_result": _truncate_text(
                output.get("resolve_result"),
                max(500, max_chars // 4),
            ),
            "docs_result": _truncate_text(
                output.get("docs_result") or output.get("raw_result") or output,
                max_chars,
            ),
        }
    return {"docs": _truncate_text(output, max_chars)}


def _compact_tool_output_for_summarize(tool_output: dict[str, Any]) -> dict[str, Any]:
    """压缩单条工具输出，降低汇总 LLM 输入体积。"""

    tool_name = str(tool_output.get("tool_name") or "")
    compact: dict[str, Any] = {
        "tool_name": tool_name,
        "ok": bool(tool_output.get("ok")),
        "error": tool_output.get("error"),
    }
    output = tool_output.get("output")

    if tool_name == "serp_api_search":
        output_dict = _tool_output_as_dict(output)
        results = output_dict.get("results") or output_dict.get("organic_results") or []
        compact_results = []
        if isinstance(results, list):
            for item in results:
                compacted = _compact_search_result_item(item)
                if compacted is not None:
                    compact_results.append(compacted)
        compact["output"] = {
            "provider": output_dict.get("provider", "serpapi"),
            "results": compact_results,
        }
        return compact

    if tool_name == "tavily_mcp_search":
        items = _extract_tavily_result_items(output)
        compact_results = []
        for item in items:
            compacted = _compact_search_result_item(item)
            if compacted is not None:
                compact_results.append(compacted)
        if compact_results:
            compact["output"] = {
                "provider": "tavily_mcp",
                "results": compact_results,
            }
        else:
            compact["output"] = {
                "provider": "tavily_mcp",
                "docs": _truncate_text(output, _context7_max_chars()),
            }
        return compact

    if tool_name == "context7_mcp_query":
        compact["output"] = _compact_context7_output(output)
        return compact

    if tool_name.startswith("playwright_mcp_fetch"):
        input_payload = tool_output.get("input")
        url = None
        if isinstance(input_payload, dict):
            maybe_url = input_payload.get("url")
            if isinstance(maybe_url, str):
                url = maybe_url
        compact["output"] = _compact_playwright_output(output, url=url)
        return compact

    compact["output"] = _truncate_text(output, _context7_max_chars())
    return compact


def _compact_tool_outputs_for_summarize(
    tool_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """压缩全部工具输出后再交给汇总 LLM。"""

    return [_compact_tool_output_for_summarize(item) for item in tool_outputs]


def _tool_output_as_dict(value: Any) -> dict[str, Any]:
    """把工具输出转换为便于确定性检查的字典。"""

    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return {"raw_text": value}
    return {}


def _tool_output_text(value: Any) -> str:
    """提取工具输出中的可检查文本。"""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _valid_search_result_count(tool_output: dict[str, Any]) -> int:
    """确定性统计搜索工具的有效结果数量。"""

    tool_name = str(tool_output["tool_name"])
    output = _tool_output_as_dict(tool_output.get("output"))
    if tool_name == "serp_api_search":
        results = output.get("results") or output.get("organic_results") or []
        return sum(
            1
            for item in results
            if isinstance(item, dict)
            and item.get("title")
            and (item.get("url") or item.get("link"))
            and item.get("snippet")
        )
    if tool_name == "tavily_mcp_search":
        results = output.get("results") or []
        if not results and len(str(output.get("raw_text") or "").strip()) >= 100:
            return 1
        return sum(
            1
            for item in results
            if isinstance(item, dict)
            and item.get("url")
            and (item.get("content") or item.get("snippet") or item.get("title"))
        )
    if tool_name == "context7_mcp_query":
        output_text = _tool_output_text(tool_output.get("output")).strip()
        invalid_markers = (
            "no docs",
            "not found",
            "找不到",
            "没有找到",
            "error",
            "缺少",
            "无法使用",
        )
        if output_text and not any(
            marker in output_text.lower() for marker in invalid_markers
        ):
            return 1
    return 0


def _playwright_content_length(output: Any) -> int:
    """估算 Playwright 返回的最大正文长度。"""

    output_dict = _tool_output_as_dict(output)
    candidates = [
        output_dict.get("content"),
        output_dict.get("text"),
        output_dict.get("markdown"),
        output_dict.get("page_text"),
        output_dict.get("raw_text"),
        output_dict.get("snapshot"),
    ]
    return max((len(_tool_output_text(item).strip()) for item in candidates), default=0)


def _playwright_failure_type(tool_output: dict[str, Any], content_length: int) -> str:
    """确定性识别 Playwright 的主要失败类型。"""

    output = _tool_output_as_dict(tool_output.get("output"))
    text = " ".join(
        [
            _tool_output_text(tool_output.get("error")),
            _tool_output_text(tool_output.get("output")),
            _tool_output_text(tool_output.get("input")),
        ]
    )
    lowered = text.lower()
    url = _tool_output_as_dict(tool_output.get("input")).get("url") or output.get("url")
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
    if content_length < 500:
        return "empty_content"
    return "none" if tool_output.get("ok") else "unknown"


def _build_web_tool_evaluation_records(
    tool_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从原始工具输出提取可写入 State 的紧凑评测记录。"""

    records = []
    for tool_output in tool_outputs:
        tool_name = str(tool_output["tool_name"])
        is_playwright = tool_name.startswith("playwright_mcp_fetch")
        content_length = (
            _playwright_content_length(tool_output.get("output"))
            if is_playwright
            else 0
        )
        failure_type = (
            _playwright_failure_type(tool_output, content_length)
            if is_playwright
            else None
        )
        valid_result_count = (
            1
            if is_playwright and tool_output.get("ok") and failure_type == "none"
            else _valid_search_result_count(tool_output)
        )
        error = tool_output.get("error")
        records.append(
            {
                "tool_name": tool_name,
                "ok": bool(tool_output.get("ok")),
                "valid_result_count": valid_result_count,
                "content_length": content_length,
                "failure_type": failure_type,
                "error": str(error)[:500] if error else None,
            }
        )
    return records


def _fallback_result_from_tool_outputs(
    task: dict[str, Any],
    tool_outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """当 LLM 汇总不可用时，从工具输出生成确定性降级结果。"""

    for tool_output in tool_outputs:
        if tool_output["tool_name"] != "serp_api_search" or not tool_output.get("ok"):
            continue
        output = tool_output.get("output")
        serp_results = output.get("results") if isinstance(output, dict) else []
        if serp_results:
            first_result = serp_results[0]
            return {
                "results": [
                    {
                        "title": first_result.get("title") or f"SerpAPI 搜索结果：{task['query']}",
                        "url_or_path": first_result.get("url") or f"serpapi://search/{task['task_id']}",
                        "snippet": first_result.get("snippet") or "",
                        "source_name": "serpapi",
                        "published_at": first_result.get("published_at"),
                        "requires_login": False,
                        "blocked_reason": None,
                        "relevance_score": 0.6,
                        "answer_coverage_score": 0.4,
                        "source_confidence_score": 0.6,
                        "freshness_score": 0.5,
                        "score_reason": "LLM 汇总失败时由 SerpAPI 首条结果生成的候选分，可信度较低。",
                    }
                ],
                "web_hitl_required": False,
                "hitl_reason": None,
            }

    return {
        "results": [],
        "web_hitl_required": True,
        "hitl_reason": "没有可用 Web 搜索结果，需要检查 SERPAPI_API_KEY、Tavily/Context7 配置或人工介入。",
    }


def _has_usable_web_result(result: dict[str, Any]) -> bool:
    """判断汇总结果中是否已有可用的候选网页证据。"""

    for item in result.get("results", []):
        if not isinstance(item, dict):
            continue
        url_or_path = item.get("url_or_path")
        snippet = item.get("snippet")
        requires_login = item.get("requires_login")
        if (
            isinstance(url_or_path, str)
            and url_or_path.startswith(("http://", "https://"))
            and isinstance(snippet, str)
            and snippet.strip()
            and not requires_login
        ):
            return True
    return False


def _normalize_web_hitl_decision(result: dict[str, Any]) -> dict[str, Any]:
    """避免把普通页面抓取失败误升级为 HITL。

    SerpAPI / Tavily 的标题、URL、摘要可以先作为候选证据进入后续证据评分；
    页面正文 403、about:blank 或超时只应降低证据可信度，不应默认要求人工接管。
    真正的登录墙、验证码和权限确认仍保留 HITL。
    """

    if not result.get("web_hitl_required") or not _has_usable_web_result(result):
        return result

    reason = str(result.get("hitl_reason") or "")
    hard_hitl_keywords = ("登录", "验证码", "captcha", "login", "sign in")
    if any(keyword in reason.lower() for keyword in hard_hitl_keywords):
        return result

    return {
        **result,
        "web_hitl_required": False,
        "hitl_reason": (
            reason
            + "；已降级为候选证据处理：页面正文抓取失败不再单独触发 HITL。"
        ).lstrip("；"),
    }


@traceable(name="summarize_web_tool_outputs", run_type="llm")
def _summarize_web_tool_outputs(
    task: dict[str, Any],
    tool_outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """用单次 LLM 调用把工具输出汇总成标准 WebSearchSubAgentResult。"""

    compact_outputs = _compact_tool_outputs_for_summarize(tool_outputs)
    prompt = load_prompt("web_search_subagent.yml")
    user_prompt = render_prompt_template(
        prompt["user_prompt_template"],
        {
            "task_id": task["task_id"],
            "question_id": task["question_id"],
            "question": task.get("question") or task["query"],
            "expected_evidence_json": json.dumps(
                task.get("expected_evidence"),
                ensure_ascii=False,
            ),
            "query": task["query"],
            "source_type": task["source_type"],
            "tool_outputs_json": json.dumps(compact_outputs, ensure_ascii=False),
        },
    )
    model = build_chat_model("web_search_subagent").with_structured_output(
        WebSearchSubAgentResultOutput
    )
    result = model.invoke(
        [
            ("system", prompt["system_prompt"]),
            ("human", user_prompt),
        ]
    )
    return result.model_dump()


@tool("web_search_subagent_tool")
def web_search_subagent_tool(task_json: str) -> str:
    """确定性调度 Web 工具，汇总打分后再按阈值条件抓取正文。"""

    payload = json.loads(task_json)
    task = payload["task"] if "task" in payload else payload
    tool_outputs = _collect_web_tool_outputs(task)
    try:
        result = _summarize_web_tool_outputs(task, tool_outputs)
        result = _keep_serpapi_top_n_after_score(result)
        tool_outputs, result = _maybe_fetch_serpapi_page_and_enrich(
            tool_outputs,
            result,
        )
    except Exception as exc:  # noqa: BLE001 - 汇总失败时不能重启工具循环
        result = _fallback_result_from_tool_outputs(task, tool_outputs)
        result["hitl_reason"] = f"Web 工具汇总失败：{exc}；{result['hitl_reason']}"
    result = _normalize_web_hitl_decision(result)
    result["tool_evaluation_records"] = _build_web_tool_evaluation_records(tool_outputs)
    return json.dumps(result, ensure_ascii=False)


@traceable(name="run_web_search_subagent_for_task", run_type="chain")
def run_web_search_subagent_for_task(task: dict[str, Any]) -> dict[str, Any]:
    """运行确定性 Web Search SubAgent 工具，并解析为 Python dict。"""

    raw_result = web_search_subagent_tool.invoke(
        {
            "task_json": json.dumps(
                {"task": task},
                ensure_ascii=False,
            )
        }
    )
    parsed = _parse_json_object_from_model_output(raw_result)
    if "results" not in parsed or not isinstance(parsed["results"], list):
        raise ValueError("Web Search SubAgent 返回值缺少 results 列表")
    if "web_hitl_required" not in parsed:
        raise ValueError("Web Search SubAgent 返回值缺少 web_hitl_required")
    if not isinstance(parsed.get("tool_evaluation_records"), list):
        raise ValueError("Web Search SubAgent 返回值缺少 tool_evaluation_records 列表")

    return parsed
