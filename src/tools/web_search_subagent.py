"""Web Search SubAgent 工具。

当前实现采用“分流物化 + 仅 SerpAPI 进汇总 LLM + 代码合并”：
- Tavily：保留自带 score，代码物化，不进汇总 LLM。
- Context7：官方文档不打搜索相关性分，代码保留 docs_result/resolve_result，不进汇总 LLM。
- SerpAPI：压缩后进汇总 LLM 打四维分并判断是否抓正文；代码写入综合 score、keep top N，
  再按 LLM 主决策 + 硬分否决/兜底可选 Playwright 写 body。
- Playwright：query URL 由代码物化；Serp 结果页抓取不二次进汇总 LLM。
- 最终由代码合并各来源候选，并按规范化 URL 去重。
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
DEFAULT_TAVILY_SOURCE_CONFIDENCE = 0.65
DEFAULT_TAVILY_FRESHNESS_WITHOUT_DATE = 0.5
DEFAULT_TAVILY_FRESHNESS_WITH_DATE = 0.7
DEFAULT_CONTEXT7_SOURCE_CONFIDENCE = 0.95
DEFAULT_CONTEXT7_RELEVANCE = 0.5
DEFAULT_CONTEXT7_COVERAGE = 0.5
DEFAULT_CONTEXT7_FRESHNESS = 0.7
URL_PATTERN = re.compile(r"https?://[^\s]+")
DEFAULT_SERPAPI_NUM = 5
DEFAULT_SERPAPI_KEEP_TOP_N = 3
DEFAULT_TAVILY_MAX_RESULTS = 5
DEFAULT_CONTEXT7_MAX_CHARS = 4000
DEFAULT_PLAYWRIGHT_MAX_CHARS = 3000
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


def _serpapi_top1_high_score() -> float:
    """触发 SerpAPI top1 正文抓取的 relevance 阈值（需严格大于）。"""

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
    """计算单条候选综合分。"""

    if item.get("score") is not None:
        return _score_or_default(item.get("score"), 0.0)
    return round(
        _score_or_default(item.get("source_confidence_score"), 0.5) * 0.35
        + _score_or_default(item.get("freshness_score"), 0.5) * 0.15
        + _score_or_default(item.get("relevance_score"), 0.5) * 0.3
        + _score_or_default(item.get("answer_coverage_score"), 0.5) * 0.2,
        4,
    )


def _is_serpapi_result(item: dict[str, Any]) -> bool:
    """判断候选是否来自 SerpAPI。"""

    source_name = str(item.get("source_name") or "").lower()
    return "serp" in source_name


def _attach_serpapi_composite_scores(result: dict[str, Any]) -> dict[str, Any]:
    """为 SerpAPI 候选写入综合 score，并规范 url 字段。"""

    updated: list[Any] = []
    for item in result.get("results", []):
        if not isinstance(item, dict):
            updated.append(item)
            continue
        normalized = dict(item)
        if _is_serpapi_result(normalized):
            score = round(
                _score_or_default(normalized.get("source_confidence_score"), 0.5) * 0.35
                + _score_or_default(normalized.get("freshness_score"), 0.5) * 0.15
                + _score_or_default(normalized.get("relevance_score"), 0.5) * 0.3
                + _score_or_default(normalized.get("answer_coverage_score"), 0.5) * 0.2,
                4,
            )
            normalized["score"] = score
            url = normalized.get("url") or normalized.get("url_or_path")
            if isinstance(url, str):
                normalized["url"] = url
                normalized["url_or_path"] = url
            normalized["score_bucket"] = "serpapi"
            normalized["scored_by"] = "serpapi_llm"
            # HITL 字段由 Playwright 路径写入，汇总 LLM 不输出
            normalized.setdefault("requires_login", False)
            normalized.setdefault("blocked_reason", None)
        updated.append(normalized)
    return {
        **result,
        "results": updated,
        "web_hitl_required": bool(result.get("web_hitl_required", False)),
        "hitl_reason": result.get("hitl_reason"),
    }


def _keep_serpapi_top_n_after_score(result: dict[str, Any]) -> dict[str, Any]:
    """汇总打分后仅对 SerpAPI 候选按 score 保留 top N。"""

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


def _normalize_candidate_url(value: Any) -> str | None:
    """规范化候选 URL / 路径，供去重与抓取校验。"""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.startswith(("http://", "https://")):
        return normalized.rstrip("/")
    return normalized


def _serpapi_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    """提取结果中的 SerpAPI 候选。"""

    return [
        item
        for item in result.get("results", [])
        if isinstance(item, dict) and _is_serpapi_result(item)
    ]


def _serpapi_item_by_url(
    serp_results: list[dict[str, Any]],
    url: str,
) -> dict[str, Any] | None:
    """按规范化 URL 查找 Serp 候选。"""

    target = _normalize_candidate_url(url)
    if target is None:
        return None
    for item in serp_results:
        item_url = _normalize_candidate_url(item.get("url") or item.get("url_or_path"))
        if item_url == target:
            return item
    return None


def _resolve_serpapi_fetch_url(result: dict[str, Any]) -> str | None:
    """解析应抓取的 Serp 正文 URL。

    主决策：LLM 的 needs_page_fetch + fetch_url。
    否决：目标条 relevance_score 未严格大于阈值（合法 URL 被否决后不再改抓）。
    兜底：LLM 要求抓取但 fetch_url 缺失/不在 keep 候选中时，
    选 keep 后 relevance 最高且超阈值的一条。
    """

    serp_results = _serpapi_items(result)
    if not serp_results:
        return None

    threshold = _serpapi_top1_high_score()
    needs_page_fetch = bool(result.get("needs_page_fetch"))

    def _passes_relevance_gate(item: dict[str, Any]) -> bool:
        return _score_or_default(item.get("relevance_score"), 0.0) > threshold

    def _http_url(item: dict[str, Any]) -> str | None:
        url = item.get("url") or item.get("url_or_path")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
        return None

    def _fallback_top_relevance() -> str | None:
        top1 = max(
            serp_results,
            key=lambda item: _score_or_default(item.get("relevance_score"), 0.0),
        )
        if _passes_relevance_gate(top1):
            return _http_url(top1)
        return None

    if needs_page_fetch:
        fetch_url = result.get("fetch_url")
        if isinstance(fetch_url, str) and fetch_url.strip():
            matched = _serpapi_item_by_url(serp_results, fetch_url)
            if matched is not None:
                if _passes_relevance_gate(matched):
                    return _http_url(matched)
                # 合法 URL 但硬分否决：不再改抓其他页
                return None
            # fetch_url 不在 keep 候选中 → 兜底
            return _fallback_top_relevance()
        # 缺少 fetch_url → 兜底
        return _fallback_top_relevance()

    # LLM 明确不抓时，不以硬分为由主动抓取
    if "needs_page_fetch" in result:
        return None

    # 汇总失败等未给出决策字段时：硬分兜底
    return _fallback_top_relevance()


def _enrich_result_with_page_body(
    result: dict[str, Any],
    *,
    fetch_url: str,
    page_output: dict[str, Any],
) -> dict[str, Any]:
    """把 Playwright 截断正文写入对应 SerpAPI 候选的 body，保留原 snippet。"""

    compact = _compact_playwright_output(
        page_output.get("output"),
        url=fetch_url,
    )
    body = str(compact.get("content") or "").strip()
    if not body:
        return result

    page_title = compact.get("title")
    updated_results: list[Any] = []
    for item in result.get("results", []):
        if not isinstance(item, dict):
            updated_results.append(item)
            continue
        item_url = item.get("url") or item.get("url_or_path")
        if item_url != fetch_url:
            updated_results.append(item)
            continue
        enriched = dict(item)
        if isinstance(page_title, str) and page_title.strip():
            enriched["title"] = page_title.strip()
        enriched["body"] = body
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


def _playwright_login_signal(tool_output: dict[str, Any]) -> str | None:
    """从 Playwright 工具输出中确定性识别登录墙 / 验证码 / 权限墙。"""

    text = " ".join(
        [
            _tool_output_text(tool_output.get("error")),
            _tool_output_text(tool_output.get("output")),
        ]
    )
    lowered = text.lower()
    checks = (
        ("验证码", "Playwright 检测到验证码墙"),
        ("captcha", "Playwright 检测到验证码墙"),
        ("登录", "Playwright 检测到登录墙"),
        ("sign in", "Playwright 检测到登录墙"),
        ("log in", "Playwright 检测到登录墙"),
        ("login", "Playwright 检测到登录墙"),
        ("权限", "Playwright 检测到权限墙"),
        ("unauthorized", "Playwright 检测到权限墙"),
        ("access denied", "Playwright 检测到权限墙"),
    )
    for keyword, reason in checks:
        if keyword in lowered:
            return reason
    return None


def _playwright_tool_url(tool_output: dict[str, Any]) -> str | None:
    """提取 Playwright 工具调用对应的 URL。"""

    input_payload = tool_output.get("input")
    if isinstance(input_payload, dict):
        maybe_url = input_payload.get("url")
        if isinstance(maybe_url, str) and maybe_url.startswith(("http://", "https://")):
            return maybe_url
    output = _tool_output_as_dict(tool_output.get("output"))
    maybe_url = output.get("url")
    if isinstance(maybe_url, str) and maybe_url.startswith(("http://", "https://")):
        return maybe_url
    return None


def _mark_result_requires_login(
    result: dict[str, Any],
    *,
    url: str,
    reason: str,
) -> dict[str, Any]:
    """把指定 URL 的候选标为需要登录 HITL。"""

    updated: list[Any] = []
    matched = False
    for item in result.get("results", []):
        if not isinstance(item, dict):
            updated.append(item)
            continue
        item_url = item.get("url") or item.get("url_or_path")
        if item_url != url:
            updated.append(item)
            continue
        enriched = dict(item)
        enriched["requires_login"] = True
        enriched["blocked_reason"] = reason
        enriched["playwright_hitl"] = True
        updated.append(enriched)
        matched = True
    if not matched:
        updated.append(
            {
                "title": url,
                "url": url,
                "url_or_path": url,
                "snippet": "",
                "published_at": None,
                "source_name": "playwright",
                "requires_login": True,
                "blocked_reason": reason,
                "playwright_hitl": True,
                "relevance_score": 0.0,
                "answer_coverage_score": 0.0,
                "source_confidence_score": 0.3,
                "freshness_score": 0.3,
                "score_reason": reason,
            }
        )
    return {**result, "results": updated}


def _strip_non_playwright_hitl_claims(result: dict[str, Any]) -> dict[str, Any]:
    """清除非 Playwright 路径提出的 HITL 声明（如 Serp 汇总 LLM）。"""

    updated: list[Any] = []
    for item in result.get("results", []):
        if not isinstance(item, dict):
            updated.append(item)
            continue
        cleaned = dict(item)
        if not cleaned.get("playwright_hitl"):
            cleaned["requires_login"] = False
        updated.append(cleaned)
    return {
        **result,
        "results": updated,
        "web_hitl_required": False,
        "hitl_reason": None,
    }


def _finalize_playwright_only_hitl(
    tool_outputs: list[dict[str, Any]],
    result: dict[str, Any],
) -> dict[str, Any]:
    """仅根据 Playwright 登录墙信号设置 Web HITL。"""

    cleared = _strip_non_playwright_hitl_claims(result)
    updated = cleared
    for tool_output in tool_outputs:
        tool_name = str(tool_output.get("tool_name") or "")
        if not tool_name.startswith("playwright_mcp_fetch"):
            continue
        reason = _playwright_login_signal(tool_output)
        if reason is None:
            # 成功抓到的正文里也可能是登录页
            compact = _compact_playwright_output(
                tool_output.get("output"),
                url=_playwright_tool_url(tool_output),
            )
            body_signal = _playwright_login_signal(
                {
                    "error": None,
                    "output": {"content": compact.get("content")},
                }
            )
            reason = body_signal
        if reason is None:
            continue
        url = _playwright_tool_url(tool_output)
        if url is None:
            continue
        updated = _mark_result_requires_login(updated, url=url, reason=reason)

    login_items = [
        item
        for item in updated.get("results", [])
        if isinstance(item, dict) and item.get("requires_login")
    ]
    if not login_items:
        return {
            **updated,
            "web_hitl_required": False,
            "hitl_reason": None,
        }
    return {
        **updated,
        "web_hitl_required": True,
        "hitl_reason": str(
            login_items[0].get("blocked_reason")
            or "Playwright 检测到登录/验证码/权限墙"
        ),
    }


def _maybe_fetch_serpapi_page_and_enrich(
    tool_outputs: list[dict[str, Any]],
    result: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """按 LLM 抓正文决策（硬分否决/兜底）抓取 SerpAPI 正文，写入 body。"""

    fetch_url = _resolve_serpapi_fetch_url(result)
    if fetch_url is None:
        return tool_outputs, result

    page_output = _call_tool(
        "playwright_mcp_fetch_serpapi_result_page_1",
        playwright_mcp_fetch_page,
        {"url": fetch_url},
    )
    updated_outputs = [*tool_outputs, page_output]
    login_reason = _playwright_login_signal(page_output)
    if login_reason is not None:
        marked = _mark_result_requires_login(
            result,
            url=fetch_url,
            reason=login_reason,
        )
        return updated_outputs, marked
    if not page_output.get("ok"):
        return updated_outputs, result

    enriched = _enrich_result_with_page_body(
        result,
        fetch_url=fetch_url,
        page_output=page_output,
    )
    body_login = None
    for item in enriched.get("results", []):
        if not isinstance(item, dict):
            continue
        if _normalize_candidate_url(
            item.get("url") or item.get("url_or_path")
        ) != _normalize_candidate_url(fetch_url):
            continue
        body_login = _playwright_login_signal(
            {"error": None, "output": {"content": item.get("body")}}
        )
        break
    if body_login is not None:
        enriched = _mark_result_requires_login(
            enriched,
            url=fetch_url,
            reason=body_login,
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


def _materialize_tavily_candidate(item: dict[str, Any]) -> dict[str, Any] | None:
    """把单条 Tavily 结果确定性转为候选，保留原始 score。"""

    url = item.get("url") or item.get("link") or item.get("url_or_path")
    title = item.get("title")
    snippet = item.get("content") or item.get("snippet") or item.get("description")
    if not (
        isinstance(title, str)
        and title.strip()
        and isinstance(url, str)
        and url.startswith(("http://", "https://"))
        and isinstance(snippet, str)
        and snippet.strip()
    ):
        return None

    score = _score_or_default(item.get("score"), 0.5)
    published_at = (
        item.get("published_at") or item.get("date") or item.get("published_date")
    )
    freshness = (
        DEFAULT_TAVILY_FRESHNESS_WITH_DATE
        if published_at
        else DEFAULT_TAVILY_FRESHNESS_WITHOUT_DATE
    )
    return {
        "title": title.strip(),
        "score": score,
        "url": url,
        "url_or_path": url,
        "snippet": snippet.strip(),
        "published_at": published_at,
        "source_name": "tavily",
        "requires_login": False,
        "blocked_reason": None,
        "relevance_score": score,
        "answer_coverage_score": score,
        "source_confidence_score": DEFAULT_TAVILY_SOURCE_CONFIDENCE,
        "freshness_score": freshness,
        "score_reason": f"Tavily 相关性 score={score}",
        "tavily_score": score,
        "score_bucket": "tavily",
        "scored_by": "tavily_provider",
    }


def _materialize_tavily_results_from_tool_outputs(
    tool_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从原始工具输出确定性提取 Tavily 候选。"""

    candidates: list[dict[str, Any]] = []
    for tool_output in tool_outputs:
        if tool_output.get("tool_name") != "tavily_mcp_search":
            continue
        if not tool_output.get("ok"):
            continue
        for item in _extract_tavily_result_items(tool_output.get("output")):
            if not isinstance(item, dict):
                continue
            candidate = _materialize_tavily_candidate(item)
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _parse_context7_payload(output: Any) -> dict[str, Any]:
    """解析 Context7 工具输出中的 docs/resolve 字段。"""

    output_dict = _tool_output_as_dict(output)
    raw_result = output_dict.get("raw_result")
    parsed: dict[str, Any] = {}
    if isinstance(raw_result, str):
        try:
            maybe = json.loads(raw_result)
        except json.JSONDecodeError:
            maybe = None
        if isinstance(maybe, dict):
            parsed = maybe
    elif isinstance(raw_result, dict):
        parsed = raw_result
    if not parsed:
        parsed = output_dict
    return {
        "library_id": parsed.get("library_id") or output_dict.get("library_id"),
        "resolve_result": parsed.get("resolve_result") or output_dict.get("resolve_result"),
        "docs_result": (
            parsed.get("docs_result")
            or output_dict.get("docs_result")
            or parsed.get("docs")
            or output_dict.get("docs")
            or raw_result
        ),
    }


def _materialize_context7_results_from_tool_outputs(
    tool_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Context7 官方文档不打分，直接保留 docs_result / resolve_result。"""

    candidates: list[dict[str, Any]] = []
    for tool_output in tool_outputs:
        if tool_output.get("tool_name") != "context7_mcp_query":
            continue
        if not tool_output.get("ok"):
            continue
        payload = _parse_context7_payload(tool_output.get("output"))
        docs_result = _truncate_text(payload.get("docs_result"), _context7_max_chars())
        resolve_result = _truncate_text(
            payload.get("resolve_result"),
            max(500, _context7_max_chars() // 4),
        )
        if not docs_result and not resolve_result:
            continue
        library_id = payload.get("library_id") or "context7"
        candidates.append(
            {
                "title": f"Context7 {library_id}",
                "url": str(library_id),
                "url_or_path": str(library_id),
                "snippet": "",
                "published_at": None,
                "source_name": "context7",
                "library_id": library_id,
                "docs_result": docs_result,
                "resolve_result": resolve_result,
                "requires_login": False,
                "blocked_reason": None,
                "relevance_score": DEFAULT_CONTEXT7_RELEVANCE,
                "answer_coverage_score": DEFAULT_CONTEXT7_COVERAGE,
                "source_confidence_score": DEFAULT_CONTEXT7_SOURCE_CONFIDENCE,
                "freshness_score": DEFAULT_CONTEXT7_FRESHNESS,
                "score_reason": "Context7 官方文档，不经 summarize LLM 打分；证据层按权威加权分桶。",
                "score_bucket": "context7",
                "scored_by": "context7_authority",
            }
        )
    return candidates


def _materialize_query_url_playwright_results(
    tool_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """query 含 URL 时的 Playwright 抓取结果，由代码物化为带 body 的候选。"""

    candidates: list[dict[str, Any]] = []
    for tool_output in tool_outputs:
        if tool_output.get("tool_name") != "playwright_mcp_fetch_page":
            continue
        input_payload = tool_output.get("input")
        url = None
        if isinstance(input_payload, dict):
            maybe_url = input_payload.get("url")
            if isinstance(maybe_url, str):
                url = maybe_url
        login_reason = _playwright_login_signal(tool_output)
        page_url = url
        if isinstance(page_url, str) and page_url.startswith(("http://", "https://")):
            if login_reason is not None:
                candidates.append(
                    {
                        "title": page_url,
                        "url": page_url,
                        "url_or_path": page_url,
                        "snippet": "",
                        "published_at": None,
                        "source_name": "playwright",
                        "requires_login": True,
                        "blocked_reason": login_reason,
                        "playwright_hitl": True,
                        "relevance_score": 0.0,
                        "answer_coverage_score": 0.0,
                        "source_confidence_score": 0.3,
                        "freshness_score": 0.3,
                        "score_reason": login_reason,
                    }
                )
                continue
        if not tool_output.get("ok"):
            continue
        compact = _compact_playwright_output(tool_output.get("output"), url=url)
        body = str(compact.get("content") or "").strip()
        page_url = compact.get("url") or url
        if not isinstance(page_url, str) or not page_url.startswith(("http://", "https://")):
            continue
        body_login = _playwright_login_signal(
            {"error": None, "output": {"content": body}}
        )
        if body_login is not None:
            candidates.append(
                {
                    "title": compact.get("title") or page_url,
                    "url": page_url,
                    "url_or_path": page_url,
                    "snippet": "",
                    "body": body,
                    "published_at": None,
                    "source_name": "playwright",
                    "requires_login": True,
                    "blocked_reason": body_login,
                    "playwright_hitl": True,
                    "relevance_score": 0.0,
                    "answer_coverage_score": 0.0,
                    "source_confidence_score": 0.3,
                    "freshness_score": 0.3,
                    "score_reason": body_login,
                }
            )
            continue
        if not body:
            continue
        title = compact.get("title") or page_url
        candidates.append(
            {
                "title": str(title),
                "url": page_url,
                "url_or_path": page_url,
                "snippet": body[:500],
                "body": body,
                "published_at": None,
                "source_name": "playwright",
                "requires_login": False,
                "blocked_reason": None,
                "relevance_score": 0.7,
                "answer_coverage_score": 0.7,
                "source_confidence_score": 0.7,
                "freshness_score": 0.5,
                "score_reason": "query URL Playwright 正文，由代码物化。",
                "score_bucket": "playwright",
                "scored_by": "playwright_code",
            }
        )
    return candidates


def _candidate_richness_key(item: dict[str, Any]) -> tuple[Any, ...]:
    """同 URL 去重时的保留优先级：body > 更高分 > 更长正文/摘要。"""

    body = item.get("body")
    docs = item.get("docs_result")
    snippet = item.get("snippet") or ""
    body_len = len(body) if isinstance(body, str) else 0
    docs_len = len(docs) if isinstance(docs, str) else 0
    snippet_len = len(snippet) if isinstance(snippet, str) else 0
    has_body = 1 if body_len > 0 else 0
    score = _candidate_composite_score(item)
    source_rank = {
        "playwright": 4,
        "serpapi": 3,
        "tavily": 2,
        "context7": 1,
    }.get(str(item.get("source_name") or "").lower(), 0)
    return (has_body, score, body_len + docs_len, snippet_len, source_rank)


def _dedupe_candidates_by_url(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按规范化 URL/路径去重，保留更完整的候选并维持首次出现顺序。"""

    best_by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        key = _normalize_candidate_url(item.get("url_or_path") or item.get("url"))
        if key is None:
            # 无 URL 的候选各自保留
            synthetic = f"__no_url__{len(order)}"
            best_by_key[synthetic] = item
            order.append(synthetic)
            continue
        existing = best_by_key.get(key)
        if existing is None:
            best_by_key[key] = item
            order.append(key)
            continue
        if _candidate_richness_key(item) > _candidate_richness_key(existing):
            best_by_key[key] = item
    return [best_by_key[key] for key in order]


def _merge_code_materialized_candidates(
    serp_result: dict[str, Any],
    *,
    tavily_candidates: list[dict[str, Any]],
    context7_candidates: list[dict[str, Any]],
    playwright_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """由代码合并各来源候选，并按 URL 去重。"""

    serp_results = [
        item for item in serp_result.get("results", []) if isinstance(item, dict)
    ]
    merged = [
        *tavily_candidates,
        *context7_candidates,
        *playwright_candidates,
        *serp_results,
    ]
    return {
        **serp_result,
        "results": _dedupe_candidates_by_url(merged),
    }


def _tool_outputs_for_summarize(
    tool_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """汇总 LLM 只接收 SerpAPI 工具输出。"""

    return [
        item
        for item in tool_outputs
        if item.get("tool_name") == "serp_api_search"
    ]
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
    """压缩非 Tavily 工具输出后再交给汇总 LLM。"""

    return [
        _compact_tool_output_for_summarize(item)
        for item in _tool_outputs_for_summarize(tool_outputs)
    ]

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
                        "score": 0.545,
                    }
                ],
                "needs_page_fetch": False,
                "fetch_url": None,
                "fetch_reason": "LLM 汇总失败，跳过抓正文决策。",
                "web_hitl_required": False,
                "hitl_reason": None,
            }

    return {
        "results": [],
        "needs_page_fetch": False,
        "fetch_url": None,
        "fetch_reason": None,
        "web_hitl_required": False,
        "hitl_reason": None,
    }


def _has_usable_web_result(result: dict[str, Any]) -> bool:
    """判断汇总结果中是否已有可用的候选网页证据。"""

    for item in result.get("results", []):
        if not isinstance(item, dict):
            continue
        url_or_path = item.get("url_or_path") or item.get("url")
        snippet = item.get("snippet") or item.get("body") or item.get("docs_result")
        requires_login = item.get("requires_login")
        if (
            isinstance(url_or_path, str)
            and (
                url_or_path.startswith(("http://", "https://"))
                or item.get("source_name") == "context7"
            )
            and isinstance(snippet, str)
            and snippet.strip()
            and not requires_login
        ):
            return True
    return False


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
    """分流物化各工具结果；仅 SerpAPI 经汇总 LLM 打分后由代码合并。"""

    payload = json.loads(task_json)
    task = payload["task"] if "task" in payload else payload
    tool_outputs = _collect_web_tool_outputs(task)
    tavily_candidates = _materialize_tavily_results_from_tool_outputs(tool_outputs)
    context7_candidates = _materialize_context7_results_from_tool_outputs(tool_outputs)
    playwright_candidates = _materialize_query_url_playwright_results(tool_outputs)
    try:
        result = _summarize_web_tool_outputs(task, tool_outputs)
        result = _attach_serpapi_composite_scores(result)
        result = _keep_serpapi_top_n_after_score(result)
        tool_outputs, result = _maybe_fetch_serpapi_page_and_enrich(
            tool_outputs,
            result,
        )
    except Exception as exc:  # noqa: BLE001 - 汇总失败时不能重启工具循环
        result = _fallback_result_from_tool_outputs(task, tool_outputs)
        result = _attach_serpapi_composite_scores(result)
        result["summarize_error"] = str(exc)
    result = _merge_code_materialized_candidates(
        result,
        tavily_candidates=tavily_candidates,
        context7_candidates=context7_candidates,
        playwright_candidates=playwright_candidates,
    )
    result = _finalize_playwright_only_hitl(tool_outputs, result)
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
