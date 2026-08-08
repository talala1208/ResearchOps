"""Web Search SubAgent 工具。

当前实现采用“分流物化 + 可切换主搜索（serp / ydc）进汇总 LLM + 代码合并”：
- Tavily：保留自带 score，代码物化，不进汇总 LLM。
- Context7：官方文档不打搜索相关性分，代码保留 docs_result/resolve_result，不进汇总 LLM。
- 主搜索由 `WEB_SEARCH_PROVIDER=serp|ydc` 切换；选中的 provider 压缩后进汇总 LLM 打四维分并判断是否抓正文。
  代码写入综合 score、keep top N，再按 LLM 主决策 + 硬分否决/兜底可选 Playwright 写 body。
- Playwright：query URL 由代码物化；主搜索结果页抓取不二次进汇总 LLM。
- 最终由代码合并各来源候选，并按规范化 URL 去重。
- 不包含 DevTools；DevTools 只属于 Web HITL 节点。
- 不包含 Claude Code / Codex；外部 Agent worker 只属于 strategy_iteration。
"""

from __future__ import annotations

import json
import os
import re
import threading
from ast import literal_eval
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from langchain.tools import tool
from langsmith import traceable
from serpapi import GoogleSearch

from src.artifacts.tool_content_cleanup import (
    clean_context7_docs,
    clean_context7_resolve,
    clean_playwright_body,
    clean_tavily_snippet,
    unwrap_text_content,
)
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
YOU_COM_SEARCH_URL = "https://ydc-index.io/v1/search"
DEFAULT_YOU_COM_TIMEOUT_SECONDS = 30
# WEB_SEARCH_PROVIDER 仅接受：serp | ydc
DEFAULT_WEB_SEARCH_PROVIDER = "serp"
_TOOL_CONCURRENCY_LIMITS = {
    "serp_api_search": 3,
    "you_com_api_search": 3,
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
    """主搜索请求条数（serp / ydc 共用）。"""

    return _read_bounded_int_env(
        "WEB_SEARCH_SERPAPI_NUM",
        default=DEFAULT_SERPAPI_NUM,
        minimum=1,
        maximum=10,
    )


def _web_search_provider() -> str:
    """读取主搜索 provider：仅 serp 或 ydc。"""

    raw = os.getenv("WEB_SEARCH_PROVIDER", DEFAULT_WEB_SEARCH_PROVIDER).strip().lower()
    if raw == "ydc":
        return "ydc"
    return "serp"


def _configured_search_tool() -> tuple[str, Any]:
    """返回当前环境变量选中的主搜索工具名与对象。"""

    if _web_search_provider() == "ydc":
        return "you_com_api_search", you_com_api_search
    return "serp_api_search", serp_api_search


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
            "provider": "serp",
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


def _you_com_snippet(item: dict[str, Any]) -> str:
    """从 you.com web 结果拼装 snippet。"""

    parts: list[str] = []
    description = item.get("description")
    if isinstance(description, str) and description.strip():
        parts.append(description.strip())
    snippets = item.get("snippets")
    if isinstance(snippets, list):
        for snippet in snippets:
            if isinstance(snippet, str) and snippet.strip():
                parts.append(snippet.strip())
    return " ".join(parts).strip()


@tool
def you_com_api_search(query: str) -> str:
    """通过 you.com（YDC）Search API 搜索网页资料；作为 SerpAPI 备选。"""

    api_key = os.getenv("YDC_API_KEY", "").strip()
    if not api_key:
        raise ValueError("缺少 YDC_API_KEY，无法调用 you.com Search。")

    num = _serpapi_num()
    request = Request(
        f"{YOU_COM_SEARCH_URL}?{urlencode({'query': query})}",
        headers={
            "Accept": "application/json",
            "X-API-Key": api_key,
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=DEFAULT_YOU_COM_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(
            f"you.com Search 调用失败：HTTP {exc.code} {body[:500]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"you.com Search 调用失败：{exc.reason}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("you.com Search 返回了非法 JSON。") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("you.com Search 返回值必须是 JSON 对象。")

    web_items = []
    results_obj = payload.get("results")
    if isinstance(results_obj, dict):
        maybe_web = results_obj.get("web")
        if isinstance(maybe_web, list):
            web_items = maybe_web

    results: list[dict[str, Any]] = []
    for index, item in enumerate(web_items[:num], start=1):
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        url = item.get("url")
        snippet = _you_com_snippet(item)
        if not (
            isinstance(title, str)
            and title.strip()
            and isinstance(url, str)
            and url.startswith(("http://", "https://"))
            and snippet
        ):
            continue
        published_at = item.get("page_age")
        results.append(
            {
                "title": title.strip(),
                "url": url,
                "snippet": snippet[:2000],
                "source": "you_com",
                "published_at": published_at if isinstance(published_at, str) else None,
                "position": index,
            }
        )

    return json.dumps(
        {
            "provider": "ydc",
            "ok": True,
            "query": query,
            "results": results,
            "search_metadata": payload.get("metadata")
            if isinstance(payload.get("metadata"), dict)
            else {},
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
            output = _loads_tool_json(raw_output)
            # 工具 JSON 内 ok=false（如 MCP ConnectError）时，外层也记失败
            if isinstance(output, dict) and output.get("ok") is False:
                return {
                    "tool_name": name,
                    "ok": False,
                    "input": payload,
                    "output": output,
                    "error": str(output.get("error") or "tool returned ok=false"),
                }
            return {
                "tool_name": name,
                "ok": True,
                "input": payload,
                "output": output,
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
    """判断候选是否来自主搜索链路（serp / ydc）。"""

    source_name = str(item.get("source_name") or "").lower()
    return (
        "serp" in source_name
        or source_name in {"ydc", "you_com", "youcom"}
        or "you_com" in source_name
    )


def _attach_serpapi_composite_scores(result: dict[str, Any]) -> dict[str, Any]:
    """为 SerpAPI 候选写入综合 score，并规范 url 字段。"""

    updated: list[Any] = []
    for item in result.get("results", []):
        if not isinstance(item, dict):
            updated.append(item)
            continue
        normalized = dict(item)
        if _is_serpapi_result(normalized):
            provider = _web_search_provider()
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
            if not isinstance(normalized.get("source_name"), str) or not normalized[
                "source_name"
            ]:
                normalized["source_name"] = provider
            normalized["score_bucket"] = provider
            normalized["scored_by"] = f"{provider}_llm"
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

    browser_snapshot_output = _browser_snapshot_output(page_output.get("output"))
    materialized = _materialize_query_url_playwright_body(
        browser_snapshot_output=browser_snapshot_output,
    )
    body = str(materialized.get("body") or "").strip()
    if not body:
        return result

    target_url = _normalize_candidate_url(fetch_url)
    updated_results: list[Any] = []
    appended = False
    for item in result.get("results", []):
        if not isinstance(item, dict):
            updated_results.append(item)
            continue
        item_url = _normalize_candidate_url(
            item.get("url") or item.get("url_or_path")
        )
        if item_url is None or item_url != target_url:
            updated_results.append(item)
            continue
        enriched = dict(item)
        enriched["body"] = body
        updated_results.append(enriched)
        appended = True

    if not appended:
        return result
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

    search_tool_name, search_tool = _configured_search_tool()
    tool_outputs.append(
        _call_tool(search_tool_name, search_tool, {"query": query})
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


# 强信号：几乎可判定为登录墙 / 验证码 / 权限墙（避免被导航栏 “Sign in” 误触发）
_PLAYWRIGHT_STRONG_LOGIN_RE = re.compile(
    r"(?:"
    r"please\s+(?:sign|log)\s+in\s+to\s+continue"
    r"|sign\s+in\s+to\s+(?:continue|access|view|read)"
    r"|log\s+in\s+to\s+(?:continue|access|view|read)"
    r"|you\s+(?:must|need\s+to)\s+(?:sign|log)\s+in"
    r"|authentication\s+required"
    r"|verify\s+you\s+are\s+(?:a\s+)?human"
    r"|\bcaptcha\b"
    r"|recaptcha"
    r"|hcaptcha"
    r"|验证码"
    r"|请(?:先)?登录"
    r"|需要登录"
    r"|登录后(?:才能|继续|查看|访问)"
    r"|access\s+denied"
    r"|\bunauthorized\b"
    r"|401\s+unauthorized"
    r"|403\s+forbidden"
    r"|cf-browser-verification"
    r"|just\s+a\s+moment(?:\.\.\.|…)"
    r"|attention\s+required"
    r")",
    re.IGNORECASE,
)
_PLAYWRIGHT_LOGIN_FORM_CUE_RE = re.compile(
    r"(?:"
    r"\bpassword\b"
    r"|\busername\b"
    r"|type=[\"']password[\"']"
    r"|密码"
    r"|用户名"
    r"|continue\s+with\s+(?:google|github|microsoft|sso|apple)"
    r"|单点登录"
    r"|enter\s+your\s+(?:password|credentials)"
    r"|输入(?:密码|账号|凭证)"
    r")",
    re.IGNORECASE,
)
_PLAYWRIGHT_WEAK_LOGIN_RE = re.compile(
    r"(?:\bsign\s+in\b|\blog\s+in\b|\blogin\b|登录)",
    re.IGNORECASE,
)
_PLAYWRIGHT_LOGIN_URL_RE = re.compile(
    r"/(?:login|signin|sign-in|log-in|auth|sso|account/login)(?:/|$|\?)",
    re.IGNORECASE,
)
# 弱信号仅在短页或伴随表单时生效，避免文档站导航栏误触发 DevTools
_PLAYWRIGHT_SHORT_LOGIN_PAGE_CHARS = 1200


def _playwright_login_signal(tool_output: dict[str, Any]) -> str | None:
    """从 Playwright 工具输出中确定性识别登录墙 / 验证码 / 权限墙。

    不把导航栏常见的 “Sign in / Log in / 登录” 单独当成登录墙；
    需强措辞、登录 URL、或「弱措辞 + 表单线索 / 极短页」组合。
    """

    text = " ".join(
        [
            _tool_output_text(tool_output.get("error")),
            _tool_output_text(tool_output.get("output")),
        ]
    )
    url = _playwright_tool_url(tool_output) or ""
    compact = text.strip()
    if not compact and not url:
        return None

    strong = _PLAYWRIGHT_STRONG_LOGIN_RE.search(compact)
    if strong is not None:
        matched = strong.group(0).lower()
        if any(
            token in matched
            for token in ("captcha", "recaptcha", "hcaptcha", "验证码", "human")
        ):
            return "Playwright 检测到验证码墙"
        if any(
            token in matched
            for token in ("unauthorized", "access denied", "403", "401")
        ):
            return "Playwright 检测到权限墙"
        return "Playwright 检测到登录墙"

    form_cues = _PLAYWRIGHT_LOGIN_FORM_CUE_RE.search(compact) is not None
    weak_count = len(_PLAYWRIGHT_WEAK_LOGIN_RE.findall(compact))
    url_looks_login = bool(url and _PLAYWRIGHT_LOGIN_URL_RE.search(url))

    if url_looks_login and (weak_count > 0 or form_cues or len(compact) < _PLAYWRIGHT_SHORT_LOGIN_PAGE_CHARS):
        return "Playwright 检测到登录墙"
    if weak_count > 0 and form_cues:
        return "Playwright 检测到登录墙"
    # 极短页且多次出现弱登录措辞：更像登录页本身，而非带导航的文档
    if (
        weak_count >= 2
        and 0 < len(compact) < _PLAYWRIGHT_SHORT_LOGIN_PAGE_CHARS
    ):
        return "Playwright 检测到登录墙"
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
        url = _playwright_tool_url(tool_output)
        reason = _playwright_login_signal(tool_output)
        if reason is None:
            # 成功抓到的正文里也可能是登录页（带上 url 供路径启发式）
            compact = _compact_playwright_output(
                tool_output.get("output"),
                url=url,
            )
            reason = _playwright_login_signal(
                {
                    "error": None,
                    "input": {"url": url} if url else {},
                    "output": {"content": compact.get("content")},
                }
            )
        if reason is None or url is None:
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


@traceable(name="fetch_serpapi_page_and_enrich_body", run_type="chain")
def _maybe_fetch_serpapi_page_and_enrich(
    tool_outputs: list[dict[str, Any]],
    result: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """按 LLM 抓正文决策（硬分否决/兜底）抓取 SerpAPI 正文，写入 body。

    该步骤单独成 LangSmith span，便于观测：是否抓取、抓取 URL、是否成功写入 body。
    """

    fetch_url = _resolve_serpapi_fetch_url(result)
    if fetch_url is None:
        return tool_outputs, {
            **result,
            "page_fetch": {
                "attempted": False,
                "fetch_url": None,
                "ok": None,
                "body_appended": False,
                "body_chars": 0,
                "reason": result.get("fetch_reason") or "未触发 Serp 结果页抓取",
            },
        }

    page_output = _call_tool(
        "playwright_mcp_fetch_serpapi_result_page_1",
        playwright_mcp_fetch_page,
        {"url": fetch_url},
    )
    page_output["body_appended"] = False
    page_output["body_chars"] = 0
    updated_outputs = [*tool_outputs, page_output]
    login_reason = _playwright_login_signal(page_output)
    if login_reason is not None:
        marked = _mark_result_requires_login(
            result,
            url=fetch_url,
            reason=login_reason,
        )
        marked["page_fetch"] = {
            "attempted": True,
            "fetch_url": fetch_url,
            "ok": bool(page_output.get("ok")),
            "body_appended": False,
            "body_chars": 0,
            "reason": login_reason,
            "tool_name": page_output.get("tool_name"),
        }
        return updated_outputs, marked
    if not page_output.get("ok"):
        failed = {
            **result,
            "page_fetch": {
                "attempted": True,
                "fetch_url": fetch_url,
                "ok": False,
                "body_appended": False,
                "body_chars": 0,
                "reason": page_output.get("error") or "Playwright 抓取失败",
                "tool_name": page_output.get("tool_name"),
            },
        }
        return updated_outputs, failed

    enriched = _enrich_result_with_page_body(
        result,
        fetch_url=fetch_url,
        page_output=page_output,
    )
    body_chars = 0
    body_appended = False
    body_login = None
    for item in enriched.get("results", []):
        if not isinstance(item, dict):
            continue
        if _normalize_candidate_url(
            item.get("url") or item.get("url_or_path")
        ) != _normalize_candidate_url(fetch_url):
            continue
        body = item.get("body")
        if isinstance(body, str) and body.strip():
            body_chars = len(body)
            body_appended = True
        body_login = _playwright_login_signal(
            {"error": None, "output": {"content": body}}
        )
        break
    page_output["body_appended"] = body_appended
    page_output["body_chars"] = body_chars
    if body_login is not None:
        enriched = _mark_result_requires_login(
            enriched,
            url=fetch_url,
            reason=body_login,
        )
        enriched["page_fetch"] = {
            "attempted": True,
            "fetch_url": fetch_url,
            "ok": True,
            "body_appended": body_appended,
            "body_chars": body_chars,
            "reason": body_login,
            "tool_name": page_output.get("tool_name"),
        }
        return updated_outputs, enriched

    enriched["page_fetch"] = {
        "attempted": True,
        "fetch_url": fetch_url,
        "ok": True,
        "body_appended": body_appended,
        "body_chars": body_chars,
        "reason": (
            "已写入 body"
            if body_appended
            else "抓取成功但未匹配到候选 URL，body 未追加"
        ),
        "tool_name": page_output.get("tool_name"),
    }
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


def _coerce_structured_value(value: Any) -> Any:
    """解析工具里常见的结构化包装：dict/list、JSON 字符串、Python repr、text blocks。"""

    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        if _looks_like_mcp_text_blocks(value):
            unwrapped = unwrap_text_content(value).strip()
            if unwrapped:
                return _coerce_structured_value(unwrapped)
            # unwrap 失败时仍尝试拼接 text 字段再解析
            joined = "\n".join(
                str(item.get("text")).strip()
                for item in value
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ).strip()
            if joined:
                return _coerce_structured_value(joined)
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if text[0] in "{[" and text[-1] in "}]":
        try:
            return literal_eval(text)
        except (ValueError, SyntaxError, MemoryError):
            pass
    return value


def _looks_like_mcp_text_blocks(value: list[Any]) -> bool:
    """判断是否为 MCP text block 列表（而非搜索结果条目列表）。"""

    if not value:
        return False
    return all(
        isinstance(item, dict)
        and (
            item.get("type") == "text"
            or (
                isinstance(item.get("text"), str)
                and not (
                    item.get("url")
                    or item.get("link")
                    or item.get("content")
                    or item.get("snippet")
                    or item.get("title")
                )
            )
        )
        for item in value
    )


def _extract_tavily_result_items(output: Any) -> list[Any]:
    """尽量从 Tavily 原始输出提取结果列表。

    真实 Tavily remote MCP 常见形态：
    raw_result = [{"type":"text","text":"{\\"results\\":[...]}", "id":"..."}]
    """

    coerced = _coerce_structured_value(output)
    if isinstance(coerced, list):
        if _looks_like_mcp_text_blocks(coerced):
            # 再解包一次，避免把 text blocks 误当成结果条目
            reparsed = _coerce_structured_value(unwrap_text_content(coerced))
            if reparsed is not coerced:
                return _extract_tavily_result_items(reparsed)
            return []
        return [item for item in coerced if isinstance(item, dict)]
    if not isinstance(coerced, dict):
        return []
    for key in ("results", "organic_results", "data"):
        value = _coerce_structured_value(coerced.get(key))
        if isinstance(value, list):
            if _looks_like_mcp_text_blocks(value):
                return _extract_tavily_result_items(value)
            return [item for item in value if isinstance(item, dict)]
    raw_result = coerced.get("raw_result")
    if raw_result is None:
        return []
    return _extract_tavily_result_items(raw_result)


def _materialize_tavily_candidate(item: dict[str, Any]) -> dict[str, Any] | None:
    """把单条 Tavily 结果确定性转为候选，保留原始 score。"""

    url = item.get("url") or item.get("link") or item.get("url_or_path")
    title = item.get("title")
    if isinstance(title, str):
        title = title.strip()
    else:
        title = ""
    snippet = clean_tavily_snippet(
        item.get("content") or item.get("snippet") or item.get("description"),
        max_chars=2000,
    )
    if not (
        title
        and isinstance(url, str)
        and url.startswith(("http://", "https://"))
        and snippet
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
        "title": title,
        "score": score,
        "url": url,
        "url_or_path": url,
        "snippet": snippet,
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


@traceable(name="materialize_tavily_candidates", run_type="chain")
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
    """解析 Context7 工具输出中的 docs/resolve 字段。

    docs_result 不回退到整个 raw_result，避免把 library 列表 dict 当成正文。
    """

    output_dict = _tool_output_as_dict(output)
    raw_result = _coerce_structured_value(output_dict.get("raw_result"))
    parsed: dict[str, Any] = {}
    if isinstance(raw_result, dict):
        parsed = raw_result
    if not parsed:
        parsed = {
            key: output_dict.get(key)
            for key in ("library_id", "resolve_result", "docs_result", "docs")
            if key in output_dict
        }

    docs_raw = (
        parsed.get("docs_result")
        or output_dict.get("docs_result")
        or parsed.get("docs")
        or output_dict.get("docs")
    )
    # 仅当 raw_result 本身就是文档字符串/可解包正文时才作为 docs 回退
    if docs_raw is None and raw_result is not None and not isinstance(raw_result, dict):
        if isinstance(raw_result, str):
            stripped = raw_result.strip()
            if stripped and not stripped.startswith("{") and not stripped.startswith("["):
                docs_raw = raw_result
        else:
            # 例如 text blocks 列表
            unwrapped = unwrap_text_content(raw_result).strip()
            if unwrapped:
                docs_raw = raw_result

    return {
        "library_id": parsed.get("library_id") or output_dict.get("library_id"),
        "resolve_result": parsed.get("resolve_result") or output_dict.get("resolve_result"),
        "docs_result": docs_raw,
    }


@traceable(name="materialize_context7_candidates", run_type="chain")
def _materialize_context7_results_from_tool_outputs(
    tool_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Context7 官方文档不打分；清洗后保留 docs_result / resolve_result。"""

    candidates: list[dict[str, Any]] = []
    max_docs = _context7_max_chars()
    max_resolve = max(500, max_docs // 4)
    for tool_output in tool_outputs:
        if tool_output.get("tool_name") != "context7_mcp_query":
            continue
        if not tool_output.get("ok"):
            continue
        payload = _parse_context7_payload(tool_output.get("output"))
        docs_result = clean_context7_docs(payload.get("docs_result"), max_chars=max_docs)
        resolve_result = clean_context7_resolve(
            payload.get("resolve_result"),
            max_chars=max_resolve,
        )
        if not docs_result and not resolve_result:
            continue
        library_id = payload.get("library_id") or "context7"
        if not isinstance(library_id, str) or not library_id.strip():
            library_id = "context7"
        else:
            library_id = library_id.strip()
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


def _browser_snapshot_output(playwright_output: Any) -> Any:
    """从 Playwright 工具结果中提取 browser_snapshot 的原始 output。"""

    output_dict = _tool_output_as_dict(playwright_output)
    snapshot_output = output_dict.get("snapshot_result")
    if snapshot_output is not None:
        return snapshot_output
    raw_result = _coerce_structured_value(output_dict.get("raw_result"))
    if isinstance(raw_result, dict):
        return raw_result.get("snapshot_result")
    return None


@traceable(name="materialize_query_url_playwright_body", run_type="chain")
def _materialize_query_url_playwright_body(
    *,
    browser_snapshot_output: Any,
) -> dict[str, Any]:
    """清洗 browser_snapshot output，并显式返回实际写入候选的正文。"""

    snapshot_text = unwrap_text_content(browser_snapshot_output).strip()
    if not snapshot_text:
        snapshot_text = _tool_output_text(browser_snapshot_output).strip()
    body = clean_playwright_body(snapshot_text, max_chars=_playwright_max_chars())
    return {
        "body_appended": bool(body),
        "body_chars": len(body),
        "body": body,
    }


def _materialize_query_url_playwright_results(
    tool_outputs: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """物化 query URL，并在 trace output 显式记录实际写入的清洗正文。"""

    candidates: list[dict[str, Any]] = []
    body_writes: list[dict[str, Any]] = []
    for tool_output in tool_outputs:
        if tool_output.get("tool_name") != "playwright_mcp_fetch_page":
            continue
        tool_output["body_appended"] = False
        tool_output["body_chars"] = 0
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
        playwright_output = tool_output.get("output")
        compact = _compact_playwright_output(playwright_output, url=url)
        materialized = _materialize_query_url_playwright_body(
            browser_snapshot_output=_browser_snapshot_output(playwright_output),
        )
        body = str(materialized.get("body") or "").strip()
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
            tool_output["body_appended"] = bool(body)
            tool_output["body_chars"] = len(body)
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
        body_writes.append(
            {
                "url": page_url,
                "body_appended": True,
                "body_chars": len(body),
                "body": body,
            }
        )
        tool_output["body_appended"] = True
        tool_output["body_chars"] = len(body)
    return {
        "candidates": candidates,
        "body_writes": body_writes,
    }


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
    source_name = str(item.get("source_name") or "").lower()
    source_rank = {
        "playwright": 4,
        "serp": 3,
        "serpapi": 3,
        "ydc": 3,
        "you_com": 3,
        "tavily": 2,
        "context7": 1,
    }.get(source_name, 0)
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


def _count_results_by_source(results: list[Any]) -> dict[str, int]:
    """按 source_name 统计合并后的候选数。"""

    counts: dict[str, int] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_name") or "unknown")
        counts[source] = counts.get(source, 0) + 1
    return counts


@traceable(name="merge_all_web_tool_results_by_code", run_type="chain")
def _merge_all_web_tool_results_by_code(
    tool_outputs: list[dict[str, Any]],
    serp_result: dict[str, Any],
) -> dict[str, Any]:
    """单次任务内：把各工具结果经代码物化、合并去重，并整理 HITL / 评测记录。

    该步骤独立成 LangSmith span，覆盖 Tavily / Context7 / Playwright / SerpAPI
    的代码侧汇总，不经过汇总 LLM。
    """

    tavily_candidates = _materialize_tavily_results_from_tool_outputs(tool_outputs)
    context7_candidates = _materialize_context7_results_from_tool_outputs(tool_outputs)
    playwright_materialization = _materialize_query_url_playwright_results(tool_outputs)
    playwright_candidates = playwright_materialization["candidates"]
    merged = _merge_code_materialized_candidates(
        serp_result,
        tavily_candidates=tavily_candidates,
        context7_candidates=context7_candidates,
        playwright_candidates=playwright_candidates,
    )
    finalized = _finalize_playwright_only_hitl(tool_outputs, merged)
    results = finalized.get("results") or []
    if not isinstance(results, list):
        results = []
    body_count = sum(
        1
        for item in results
        if isinstance(item, dict)
        and isinstance(item.get("body"), str)
        and item["body"].strip()
    )
    finalized["tool_evaluation_records"] = _build_web_tool_evaluation_records(
        tool_outputs
    )
    primary_provider = _web_search_provider()
    finalized["code_merge"] = {
        "input_counts": {
            "tavily": len(tavily_candidates),
            "context7": len(context7_candidates),
            "playwright": len(playwright_candidates),
            primary_provider: sum(
                1
                for item in serp_result.get("results", [])
                if isinstance(item, dict)
            ),
            "tool_outputs": len(tool_outputs),
        },
        "merged_count": len(results),
        "source_counts": _count_results_by_source(results),
        "with_body_count": body_count,
        "web_hitl_required": bool(finalized.get("web_hitl_required")),
    }
    return finalized


def _tool_outputs_for_summarize(
    tool_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """汇总 LLM 只接收 WEB_SEARCH_PROVIDER 选中的主搜索工具输出。"""

    search_tool_name, _ = _configured_search_tool()
    return [
        item
        for item in tool_outputs
        if item.get("tool_name") == search_tool_name
    ]


def _extract_serpapi_results_for_summarize(
    tool_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从当前主搜索工具输出中提取并压缩结果。"""

    serp_results: list[dict[str, Any]] = []
    for tool_output in _tool_outputs_for_summarize(tool_outputs):
        if not tool_output.get("ok"):
            continue
        output = _tool_output_as_dict(tool_output.get("output"))
        raw_results = output.get("results") or output.get("organic_results") or []
        if not isinstance(raw_results, list):
            continue
        for item in raw_results:
            compacted = _compact_search_result_item(item)
            if compacted is not None:
                serp_results.append(compacted)
    return serp_results


def _compact_playwright_output(output: Any, *, url: str | None) -> dict[str, Any]:
    """压缩 Playwright 输出，只保留 url / title / 清洗截断后的正文。"""

    output_dict = _tool_output_as_dict(output)
    raw_result = _coerce_structured_value(output_dict.get("raw_result"))
    if isinstance(raw_result, dict):
        # online_mcp_tools 把正文放在 raw_result 内；顶层字段优先
        output_dict = {
            **raw_result,
            **{key: value for key, value in output_dict.items() if key != "raw_result"},
        }
    title = (
        output_dict.get("title")
        or output_dict.get("page_title")
        or output_dict.get("name")
    )
    if isinstance(title, str):
        title = title.strip()
    else:
        title = None
    candidates = [
        output_dict.get("content"),
        output_dict.get("text"),
        output_dict.get("snapshot"),
        output_dict.get("page_text"),
        output_dict.get("snapshot_result"),
        output_dict.get("raw_text"),
        output_dict.get("navigation_result"),
    ]
    if isinstance(raw_result, str):
        candidates.append(raw_result)
    body = ""
    for candidate in candidates:
        text = unwrap_text_content(candidate).strip()
        if not text:
            text = _tool_output_text(candidate).strip()
        if len(text) > len(body):
            body = text
    if not body and not isinstance(output, dict):
        body = unwrap_text_content(output).strip() or _tool_output_text(output).strip()
    cleaned_body = clean_playwright_body(body, max_chars=_playwright_max_chars())
    return {
        "url": url or output_dict.get("url"),
        "title": title,
        "content": cleaned_body,
    }


def _compact_tool_outputs_for_summarize(
    tool_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """兼容旧调用：返回仅含当前主搜索 provider 的压缩结构。

    新路径请优先使用 `_extract_serpapi_results_for_summarize`。
    """

    search_tool_name, _ = _configured_search_tool()
    return [
        {
            "tool_name": search_tool_name,
            "ok": True,
            "error": None,
            "output": {
                "provider": _web_search_provider(),
                "results": _extract_serpapi_results_for_summarize(tool_outputs),
            },
        }
    ] if _tool_outputs_for_summarize(tool_outputs) else []


def _empty_serpapi_summarize_result() -> dict[str, Any]:
    """无可用主搜索结果时跳过汇总 LLM。"""

    provider = _web_search_provider()
    return {
        "results": [],
        "needs_page_fetch": False,
        "fetch_url": None,
        "fetch_reason": f"无可用 {provider} 结果，跳过汇总 LLM。",
        "web_hitl_required": False,
        "hitl_reason": None,
    }


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
    if tool_name in {"serp_api_search", "you_com_api_search"}:
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
        results = _extract_tavily_result_items(output)
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
        body_appended = bool(tool_output.get("body_appended")) if is_playwright else False
        body_chars = int(tool_output.get("body_chars") or 0) if is_playwright else 0
        records.append(
            {
                "tool_name": tool_name,
                "ok": bool(tool_output.get("ok")),
                "valid_result_count": valid_result_count,
                "content_length": content_length,
                "body_appended": body_appended,
                "body_chars": body_chars,
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

    search_tool_name, _ = _configured_search_tool()
    source_name = "ydc" if search_tool_name == "you_com_api_search" else "serp"
    for tool_output in tool_outputs:
        if tool_output.get("tool_name") != search_tool_name:
            continue
        if not tool_output.get("ok"):
            continue
        output = tool_output.get("output")
        search_results = output.get("results") if isinstance(output, dict) else []
        if search_results:
            first_result = search_results[0]
            return {
                "results": [
                    {
                        "title": first_result.get("title")
                        or f"{source_name} 搜索结果：{task['query']}",
                        "url_or_path": first_result.get("url")
                        or f"{source_name}://search/{task['task_id']}",
                        "snippet": first_result.get("snippet") or "",
                        "source_name": source_name,
                        "published_at": first_result.get("published_at"),
                        "requires_login": False,
                        "blocked_reason": None,
                        "relevance_score": 0.6,
                        "answer_coverage_score": 0.4,
                        "source_confidence_score": 0.6,
                        "freshness_score": 0.5,
                        "score_reason": (
                            f"LLM 汇总失败时由 {source_name} 首条结果生成的候选分，可信度较低。"
                        ),
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


def _parse_llm_json_content(content: Any) -> dict[str, Any]:
    """从聊天消息 content 解析 JSON 对象，兼容 text blocks。"""

    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
        content = "\n".join(text_parts)
    if not isinstance(content, str):
        raise ValueError(f"无法解析 Web 汇总输出类型：{type(content).__name__}")
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Web 汇总 LLM 输出必须是 JSON 对象")
    return parsed


def _coerce_web_summarize_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """归一化汇总 LLM 常见字段漂移。"""

    if not isinstance(payload, dict):
        raise ValueError("Web 汇总 LLM 输出必须是 JSON 对象")

    data = dict(payload)
    coerced_results: list[dict[str, Any]] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        result = dict(item)
        url_or_path = result.get("url_or_path")
        if not isinstance(url_or_path, str) or not url_or_path.strip():
            url = result.get("url")
            if isinstance(url, str) and url.strip():
                result["url_or_path"] = url.strip()
        if not isinstance(result.get("source_name"), str) or not result["source_name"]:
            result["source_name"] = _web_search_provider()
        coerced_results.append(result)
    data["results"] = coerced_results

    if "needs_page_fetch" not in data:
        data["needs_page_fetch"] = False
    if "fetch_url" not in data:
        data["fetch_url"] = None
    if "fetch_reason" not in data:
        data["fetch_reason"] = None
    return data


@traceable(name="summarize_serpapi_results", run_type="llm")
def _summarize_web_tool_outputs(
    task: dict[str, Any],
    tool_outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """仅对 SerpAPI 压缩结果做单次 LLM 打分；Tavily/Context7/Playwright 不进入本函数输入。"""

    serp_results = _extract_serpapi_results_for_summarize(tool_outputs)
    if not serp_results:
        return _empty_serpapi_summarize_result()

    prompt = load_prompt("web_search_subagent.yml")
    source_name = _web_search_provider()
    prompt_vars = {
        "source_name": source_name,
        "task_id": task["task_id"],
        "question_id": task["question_id"],
        "question": task.get("question") or task["query"],
        "expected_evidence_json": json.dumps(
            task.get("expected_evidence"),
            ensure_ascii=False,
        ),
        "query": task["query"],
        "source_type": task["source_type"],
        "search_results_json": json.dumps(serp_results, ensure_ascii=False),
    }
    system_prompt = render_prompt_template(prompt["system_prompt"], prompt_vars)
    user_prompt = render_prompt_template(prompt["user_prompt_template"], prompt_vars)
    # 硬断言：汇总 prompt 不得混入其他工具输出
    forbidden = ("tavily_mcp_search", "context7_mcp_query", "playwright_mcp_fetch")
    if any(marker in user_prompt for marker in forbidden):
        raise ValueError("汇总 LLM prompt 混入了非 SerpAPI 工具输出")

    model = build_chat_model("web_search_subagent").bind(
        response_format={"type": "json_object"}
    )
    response = model.invoke(
        [
            ("system", system_prompt),
            ("human", user_prompt),
        ]
    )
    payload = _parse_llm_json_content(getattr(response, "content", response))
    coerced = _coerce_web_summarize_dict(payload)
    return WebSearchSubAgentResultOutput.model_validate(coerced).model_dump()


@tool("web_search_subagent_tool")
def web_search_subagent_tool(task_json: str) -> str:
    """分流物化各工具结果；仅 SerpAPI 经汇总 LLM 打分后由代码合并。"""

    payload = json.loads(task_json)
    task = payload["task"] if "task" in payload else payload
    tool_outputs = _collect_web_tool_outputs(task)
    try:
        # 明确只把 SerpAPI 相关 tool_outputs 交给汇总；其他来源在代码合并 span 中物化
        serp_only_outputs = _tool_outputs_for_summarize(tool_outputs)
        result = _summarize_web_tool_outputs(task, serp_only_outputs)
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
    result = _merge_all_web_tool_results_by_code(tool_outputs, result)
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
