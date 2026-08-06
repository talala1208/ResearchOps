"""Web Search SubAgent 工具。

当前实现采用“确定性调度 + 单次 LLM 汇总”：
- 不再使用内部 `create_agent` ReAct 循环，避免工具自由循环和重复 trace。
- 只调度 SerpAPI / Tavily / Context7 / headless Playwright 页面读取工具。
- 不包含 DevTools；DevTools 只属于 Web HITL 节点。
- 不包含 Claude Code / Codex；外部 Agent worker 只属于 strategy_iteration。
"""

from __future__ import annotations

import json
import os
import re
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
DEFAULT_SERPAPI_PAGE_FETCH_TOP_N = 1


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

    search = GoogleSearch(
        {
            "engine": "google",
            "q": query,
            "api_key": api_key,
            "num": 5,
            "hl": "zh-cn",
        }
    )
    payload = search.get_dict()
    if payload.get("error"):
        raise RuntimeError(f"SerpAPI 调用失败：{payload['error']}")

    results = []
    for item in payload.get("organic_results", []):
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


def _call_tool(name: str, tool_obj: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """确定性调用单个 LangChain tool 并返回结构化调用记录。"""

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


def _serpapi_result_urls(tool_output: dict[str, Any]) -> list[str]:
    """从 SerpAPI 结构化结果中提取可继续读取正文的 URL。"""

    if tool_output["tool_name"] != "serp_api_search" or not tool_output.get("ok"):
        return []
    output = tool_output.get("output")
    if not isinstance(output, dict):
        return []
    results = output.get("results")
    if not isinstance(results, list):
        return []

    urls = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            urls.append(url)
    return urls


def _serpapi_page_fetch_top_n() -> int:
    """读取 SerpAPI 结果正文抓取数量。"""

    raw_value = os.getenv(
        "WEB_SEARCH_FETCH_SERPAPI_TOP_N",
        str(DEFAULT_SERPAPI_PAGE_FETCH_TOP_N),
    )
    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_SERPAPI_PAGE_FETCH_TOP_N
    return max(0, min(value, 5))


@traceable(name="collect_web_tool_outputs", run_type="chain")
def _collect_web_tool_outputs(task: dict[str, Any]) -> list[dict[str, Any]]:
    """按固定顺序收集 Web 工具输出，不让 LLM 自行循环调用工具。"""

    query = task["query"]
    source_type = task["source_type"]
    tool_outputs = []

    if source_type in WEB_SOURCE_TYPES:
        tool_outputs.append(
            _call_tool("tavily_mcp_search", tavily_mcp_search, {"query": query})
        )

    if source_type in DOC_SOURCE_TYPES:
        tool_outputs.append(
            _call_tool("context7_mcp_query", context7_mcp_query, {"topic": query})
        )

    serpapi_output = _call_tool(
        "serp_api_search",
        serp_api_search,
        {"query": query},
    )
    tool_outputs.append(serpapi_output)

    url = _extract_url(query)
    if url is not None:
        tool_outputs.append(
            _call_tool(
                "playwright_mcp_fetch_page",
                playwright_mcp_fetch_page,
                {"url": url},
            )
        )

    if source_type in WEB_SOURCE_TYPES:
        serpapi_fetch_top_n = _serpapi_page_fetch_top_n()
        if serpapi_fetch_top_n > 0:
            for index, result_url in enumerate(
                _serpapi_result_urls(serpapi_output)[:serpapi_fetch_top_n],
                start=1,
            ):
                tool_outputs.append(
                    _call_tool(
                        f"playwright_mcp_fetch_serpapi_result_page_{index}",
                        playwright_mcp_fetch_page,
                        {"url": result_url},
                    )
            )

    return tool_outputs


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
                indent=2,
            ),
            "query": task["query"],
            "source_type": task["source_type"],
            "tool_outputs_json": json.dumps(tool_outputs, ensure_ascii=False, indent=2),
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
    """确定性调度 Web 工具，并用单次 LLM 汇总候选证据。"""

    payload = json.loads(task_json)
    task = payload["task"] if "task" in payload else payload
    tool_outputs = _collect_web_tool_outputs(task)
    try:
        result = _summarize_web_tool_outputs(task, tool_outputs)
    except Exception as exc:  # noqa: BLE001 - 汇总失败时不能重启工具循环
        result = _fallback_result_from_tool_outputs(task, tool_outputs)
        result["hitl_reason"] = f"Web 工具汇总失败：{exc}；{result['hitl_reason']}"
    result = _normalize_web_hitl_decision(result)
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

    return parsed
