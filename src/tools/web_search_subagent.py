"""Web Search SubAgent 工具。

当前实现采用“确定性调度 + 单次 LLM 汇总”：
- 不再使用内部 `create_agent` ReAct 循环，避免工具自由循环和重复 trace。
- 默认只调度 SerpAPI / Tavily / Context7 工具。
- Claude Code / Codex worker 仅在策略迭代或显式允许时最多调用一个。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from langchain.tools import tool
from langchain_community.agent_toolkits.load_tools import load_tools
from langsmith import traceable

from src.llm.chat import build_chat_model
from src.llm.prompt_loader import load_prompt, render_prompt_template
from src.llm.structured_outputs import WebSearchSubAgentResultOutput
from src.tools.external_agent_workers import call_claude_code_worker, call_codex_worker
from src.tools.online_mcp_tools import (
    context7_mcp_query,
    devtools_mcp_inspect_page,
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

    serpapi_tool = load_tools(["serpapi"])[0]
    result = serpapi_tool.invoke(query)
    return json.dumps(
        {
            "provider": "serpapi",
            "raw_result": result,
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


def _should_allow_external_worker(task: dict[str, Any], allow_external_workers: bool) -> bool:
    """判断本次 Web 检索是否允许调用外部 Agent worker。"""

    if allow_external_workers:
        return True
    return bool(task.get("allow_external_worker"))


def _call_optional_external_worker(task: dict[str, Any]) -> dict[str, Any]:
    """按配置最多调用一个外部 worker。"""

    prompt = (
        "请协助处理一个复杂 Web 研究检索任务，只返回可公开引用的网页候选资料摘要。\n"
        f"task_id: {task['task_id']}\n"
        f"question_id: {task['question_id']}\n"
        f"query: {task['query']}\n"
        f"source_type: {task['source_type']}"
    )
    provider = os.getenv("WEB_SEARCH_EXTERNAL_WORKER", "claude_code").strip()
    if provider == "codex":
        return _call_tool(
            "call_codex_worker",
            call_codex_worker,
            {"prompt": prompt},
        )
    return _call_tool(
        "call_claude_code_worker",
        call_claude_code_worker,
        {"prompt": prompt},
    )


@traceable(name="collect_web_tool_outputs", run_type="chain")
def _collect_web_tool_outputs(
    task: dict[str, Any],
    *,
    allow_external_workers: bool,
) -> list[dict[str, Any]]:
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
        tool_outputs.append(
            _call_tool(
                "devtools_mcp_inspect_page",
                devtools_mcp_inspect_page,
                {"url": url},
            )
        )

    if _should_allow_external_worker(task, allow_external_workers):
        tool_outputs.append(_call_optional_external_worker(task))

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
        raw_result = output.get("raw_result") if isinstance(output, dict) else output
        if raw_result:
            return {
                "results": [
                    {
                        "title": f"SerpAPI 搜索摘要：{task['query']}",
                        "url_or_path": f"serpapi://search/{task['task_id']}",
                        "snippet": str(raw_result)[:1000],
                        "source_name": "serpapi",
                        "published_at": None,
                        "requires_login": False,
                        "blocked_reason": None,
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
    allow_external_workers = bool(payload.get("allow_external_workers", False))
    tool_outputs = _collect_web_tool_outputs(
        task,
        allow_external_workers=allow_external_workers,
    )
    try:
        result = _summarize_web_tool_outputs(task, tool_outputs)
    except Exception as exc:  # noqa: BLE001 - 汇总失败时不能重启工具循环
        result = _fallback_result_from_tool_outputs(task, tool_outputs)
        result["hitl_reason"] = f"Web 工具汇总失败：{exc}；{result['hitl_reason']}"
    return json.dumps(result, ensure_ascii=False)


@traceable(name="run_web_search_subagent_for_task", run_type="chain")
def run_web_search_subagent_for_task(
    task: dict[str, Any],
    *,
    allow_external_workers: bool = False,
) -> dict[str, Any]:
    """运行确定性 Web Search SubAgent 工具，并解析为 Python dict。"""

    raw_result = web_search_subagent_tool.invoke(
        {
            "task_json": json.dumps(
                {
                    "task": task,
                    "allow_external_workers": allow_external_workers,
                },
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
