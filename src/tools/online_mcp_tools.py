"""Online MCP 工具封装。

参考 `reference/2.1_mcp.ipynb` 的 `MultiServerMCPClient` 用法，接入：
- Tavily remote MCP：Web search
- Context7 remote MCP：官方文档查询
- Playwright MCP：静态页面读取、页面快照和登录墙观察
- Chrome DevTools MCP：页面检查、快照和调试观察

密钥只从环境变量读取：
- TAVILY_API_KEY
- CONTEXT7_API_KEY

注意：`langchain_mcp_adapters` 当前作为可选依赖延迟导入；缺失时工具会
显式返回错误，不静默回退。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from functools import lru_cache
from typing import Any

from langchain.tools import tool

from src.config import settings as _settings


ONLINE_MCP_TIMEOUT_SECONDS = int(os.getenv("ONLINE_MCP_TIMEOUT_SECONDS", "30"))
_ = _settings.PROJECT_ROOT
CONTEXT7_LIBRARY_ID_PATTERN = re.compile(
    r"Context7-compatible library ID:\s*(?P<library_id>/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)"
)


class MCPToolError(RuntimeError):
    """MCP 工具调用错误。"""


def _read_optional_env(name: str) -> str | None:
    """读取 MCP 可选环境变量。"""

    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _env_enabled(name: str, default: bool = False) -> bool:
    """读取布尔环境变量。"""

    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _devtools_args() -> list[str]:
    """构建 HITL DevTools MCP 启动参数。"""

    args = [
        "chrome-devtools-mcp@latest",
        "--isolated",
        "--no-usage-statistics",
        "--no-performance-crux",
    ]
    if _env_enabled("HITL_DEVTOOLS_HEADLESS", default=False):
        args.append("--headless")
    return args


def _build_mcp_config() -> dict[str, dict[str, Any]]:
    """构建 Online MCP 配置。"""

    tavily_api_key = _read_optional_env("TAVILY_API_KEY")
    context7_api_key = _read_optional_env("CONTEXT7_API_KEY")
    config: dict[str, dict[str, Any]] = {
        "playwright": {
            "transport": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "@playwright/mcp",
                "--headless",
                "--isolated",
                "--browser",
                "chrome",
            ],
            "env": {},
        },
        "devtools": {
            "transport": "stdio",
            "command": "npx",
            "args": _devtools_args(),
            "env": {},
        },
    }
    if context7_api_key is not None:
        config["context7"] = {
            "transport": "streamable_http",
            "url": "https://mcp.context7.com/mcp",
            "headers": {
                "CONTEXT7_API_KEY": context7_api_key,
            },
        }
    if tavily_api_key is not None:
        config["tavily-remote-mcp"] = {
            "transport": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "mcp-remote",
                f"https://mcp.tavily.com/mcp/?tavilyApiKey={tavily_api_key}",
            ],
            "env": {},
        }
    return config


@lru_cache(maxsize=1)
def _build_mcp_client():
    """构建 MultiServerMCPClient。"""

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:
        raise MCPToolError(
            "无法导入 langchain-mcp-adapters.client.MultiServerMCPClient；"
            "请检查 langchain-mcp-adapters 与 mcp 包版本是否兼容。"
            f"原始错误：{exc}"
        ) from exc

    return MultiServerMCPClient(_build_mcp_config())


async def _get_online_mcp_tools(server_name: str | None = None) -> list[Any]:
    """获取 Online MCP tools。"""

    client = _build_mcp_client()
    return await client.get_tools(server_name=server_name)


def _tool_name(tool_obj: Any) -> str:
    """读取工具名。"""

    return str(getattr(tool_obj, "name", ""))


def _select_tool(tools: list[Any], keywords: tuple[str, ...]) -> Any:
    """按工具名关键词选择 MCP tool。"""

    for tool_obj in tools:
        name = _tool_name(tool_obj).lower()
        if all(keyword in name for keyword in keywords):
            return tool_obj
    for tool_obj in tools:
        name = _tool_name(tool_obj).lower()
        if any(keyword in name for keyword in keywords):
            return tool_obj
    available = [_tool_name(tool_obj) for tool_obj in tools]
    raise MCPToolError(f"未找到匹配 MCP 工具：keywords={keywords}；available={available}")


def _try_select_tool(tools: list[Any], keyword_options: list[tuple[str, ...]]) -> Any:
    """按多组关键词尝试选择 MCP tool。"""

    errors = []
    for keywords in keyword_options:
        try:
            return _select_tool(tools, keywords)
        except MCPToolError as exc:
            errors.append(str(exc))
    raise MCPToolError("未找到匹配 MCP 工具：" + " | ".join(errors))


def _filter_tools_by_name(tools: list[Any], keywords: tuple[str, ...]) -> list[Any]:
    """按工具名包含任一关键词过滤 tools。"""

    matched = [
        tool_obj
        for tool_obj in tools
        if any(keyword in _tool_name(tool_obj).lower() for keyword in keywords)
    ]
    return matched or tools


async def _invoke_tool_with_candidate_payloads(
    tool_obj: Any,
    payloads: list[dict[str, Any]],
) -> Any:
    """用候选参数调用 MCP tool。"""

    errors = []
    for payload in payloads:
        try:
            return await tool_obj.ainvoke(payload)
        except Exception as exc:  # noqa: BLE001 - 需要收集不同 MCP schema 的失败信息
            errors.append(f"payload={payload} error={exc}")
    raise MCPToolError("MCP tool 参数尝试全部失败：" + " | ".join(errors))


async def _tavily_search_async(query: str, max_results: int) -> Any:
    """调用 Tavily MCP 搜索。"""

    tools = await _get_online_mcp_tools(server_name="tavily-remote-mcp")
    tavily_tool = _select_tool(tools, ("tavily", "search"))
    return await _invoke_tool_with_candidate_payloads(
        tavily_tool,
        [
            {"query": query, "max_results": max_results},
            {"query": query},
            {"search_query": query, "max_results": max_results},
            {"q": query, "max_results": max_results},
        ],
    )


async def _context7_query_async(topic: str) -> Any:
    """调用 Context7 MCP 查询官方文档。"""

    candidate_tools = await _get_online_mcp_tools(server_name="context7")
    if not candidate_tools:
        raise MCPToolError("Context7 MCP 未配置或未暴露工具，请检查 CONTEXT7_API_KEY")

    resolve_tool = _select_tool(candidate_tools, ("resolve",))
    query_docs_tool = _try_select_tool(
        candidate_tools,
        [("query", "docs"), ("docs",)],
    )
    resolve_result = await _invoke_tool_with_candidate_payloads(
        resolve_tool,
        [
            {"libraryName": topic, "query": topic},
        ],
    )
    resolve_text = str(resolve_result)
    match = CONTEXT7_LIBRARY_ID_PATTERN.search(resolve_text)
    if match is None:
        raise MCPToolError(f"Context7 未解析出 libraryId：{resolve_text[:1000]}")

    library_id = match.group("library_id")
    docs_result = await _invoke_tool_with_candidate_payloads(
        query_docs_tool,
        [
            {"libraryId": library_id, "query": topic},
        ],
    )
    return {
        "library_id": library_id,
        "resolve_result": str(resolve_result),
        "docs_result": str(docs_result),
    }


async def _playwright_fetch_page_async(url: str) -> Any:
    """调用 Playwright MCP 读取页面快照。"""

    tools = await _get_online_mcp_tools(server_name="playwright")
    playwright_tools = _filter_tools_by_name(tools, ("playwright", "browser"))
    navigate_tool = _try_select_tool(
        playwright_tools,
        [("navigate",), ("goto",), ("open",), ("new", "page")],
    )
    navigation_result = await _invoke_tool_with_candidate_payloads(
        navigate_tool,
        [
            {"url": url},
            {"url": url, "timeout": ONLINE_MCP_TIMEOUT_SECONDS * 1000},
            {"input": url},
        ],
    )
    snapshot_tool = _try_select_tool(
        playwright_tools,
        [("snapshot",), ("content",), ("text",), ("accessibility",)],
    )
    snapshot_result = await _invoke_tool_with_candidate_payloads(
        snapshot_tool,
        [
            {},
            {"url": url},
        ],
    )
    return {
        "navigation_result": str(navigation_result),
        "snapshot_result": str(snapshot_result),
    }


async def _devtools_inspect_page_async(url: str) -> Any:
    """调用 Chrome DevTools MCP 检查页面状态。"""

    tools = await _get_online_mcp_tools(server_name="devtools")
    devtools_tools = _filter_tools_by_name(tools, ("devtools", "chrome", "page"))
    navigate_tool = _try_select_tool(
        devtools_tools,
        [("navigate",), ("new", "page"), ("open",), ("select", "page")],
    )
    navigation_result = await _invoke_tool_with_candidate_payloads(
        navigate_tool,
        [
            {"url": url},
            {"url": url, "timeout": ONLINE_MCP_TIMEOUT_SECONDS * 1000},
            {"input": url},
        ],
    )
    inspect_tool = _try_select_tool(
        devtools_tools,
        [("snapshot",), ("content",), ("evaluate",), ("screenshot",)],
    )
    inspect_result = await _invoke_tool_with_candidate_payloads(
        inspect_tool,
        [
            {},
            {"url": url},
            {"expression": "document.body.innerText.slice(0, 4000)"},
        ],
    )
    return {
        "navigation_result": str(navigation_result),
        "inspect_result": str(inspect_result),
    }


def _run_async(coro):
    """在同步 tool 中执行异步 MCP 调用。"""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise MCPToolError("当前线程已有运行中的 event loop，请改用 async MCP 调用入口。")


@tool
def tavily_mcp_search(query: str) -> str:
    """通过 Tavily remote MCP 搜索网页资料。"""

    try:
        result = _run_async(
            asyncio.wait_for(
                _tavily_search_async(query=query, max_results=5),
                timeout=ONLINE_MCP_TIMEOUT_SECONDS,
            )
        )
    except Exception as exc:  # noqa: BLE001 - tool 需要把错误显式返回给 SubAgent
        return json.dumps(
            {
                "ok": False,
                "provider": "tavily_mcp",
                "error": str(exc),
                "results": [],
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "ok": True,
            "provider": "tavily_mcp",
            "raw_result": str(result),
        },
        ensure_ascii=False,
    )


@tool
def context7_mcp_query(topic: str) -> str:
    """通过 Context7 MCP 查询官方库、框架或 SDK 文档。"""

    try:
        result = _run_async(
            asyncio.wait_for(
                _context7_query_async(topic=topic),
                timeout=ONLINE_MCP_TIMEOUT_SECONDS,
            )
        )
    except Exception as exc:  # noqa: BLE001 - tool 需要把错误显式返回给 SubAgent
        return json.dumps(
            {
                "ok": False,
                "provider": "context7_mcp",
                "error": str(exc),
                "results": [],
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "ok": True,
            "provider": "context7_mcp",
            "raw_result": str(result),
        },
        ensure_ascii=False,
    )


@tool
def playwright_mcp_fetch_page(url: str) -> str:
    """通过 Playwright MCP 读取静态页面快照，用于页面正文、登录墙和可访问性观察。"""

    try:
        result = _run_async(
            asyncio.wait_for(
                _playwright_fetch_page_async(url=url),
                timeout=ONLINE_MCP_TIMEOUT_SECONDS,
            )
        )
    except Exception as exc:  # noqa: BLE001 - tool 需要把错误显式返回给 SubAgent
        return json.dumps(
            {
                "ok": False,
                "provider": "playwright_mcp",
                "error": str(exc),
                "results": [],
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "ok": True,
            "provider": "playwright_mcp",
            "raw_result": result,
        },
        ensure_ascii=False,
    )


@tool
def devtools_mcp_inspect_page(url: str) -> str:
    """通过 Chrome DevTools MCP 检查页面状态，用于登录页、阻塞页和页面内容观察。"""

    try:
        result = _run_async(
            asyncio.wait_for(
                _devtools_inspect_page_async(url=url),
                timeout=ONLINE_MCP_TIMEOUT_SECONDS,
            )
        )
    except Exception as exc:  # noqa: BLE001 - tool 需要把错误显式返回给 SubAgent
        return json.dumps(
            {
                "ok": False,
                "provider": "devtools_mcp",
                "error": str(exc),
                "results": [],
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "ok": True,
            "provider": "devtools_mcp",
            "raw_result": result,
        },
        ensure_ascii=False,
    )
