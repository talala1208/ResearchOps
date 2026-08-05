"""Online MCP 工具封装。

参考 `reference/2.1_mcp.ipynb` 的 `MultiServerMCPClient` 用法，接入：
- Tavily remote MCP：Web search
- Context7 remote MCP：官方文档查询

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
from functools import lru_cache
from typing import Any

from langchain.tools import tool
from langsmith import traceable


class MCPToolError(RuntimeError):
    """MCP 工具调用错误。"""


def _read_required_env(name: str) -> str:
    """读取 MCP 必需环境变量。"""

    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise MCPToolError(f"缺少必要环境变量：{name}")
    return value.strip()


def _build_mcp_config() -> dict[str, dict[str, Any]]:
    """构建 Online MCP 配置。"""

    tavily_api_key = _read_required_env("TAVILY_API_KEY")
    context7_api_key = _read_required_env("CONTEXT7_API_KEY")

    return {
        "context7": {
            "transport": "streamable_http",
            "url": "https://mcp.context7.com/mcp",
            "headers": {
                "CONTEXT7_API_KEY": context7_api_key,
            },
        },
        "tavily-remote-mcp": {
            "transport": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "mcp-remote",
                f"https://mcp.tavily.com/mcp/?tavilyApiKey={tavily_api_key}",
            ],
            "env": {},
        },
    }


@lru_cache(maxsize=1)
def _build_mcp_client():
    """构建 MultiServerMCPClient。"""

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:
        raise MCPToolError(
            "缺少 langchain-mcp-adapters，无法使用 Online MCP；"
            "请先安装依赖 langchain-mcp-adapters。"
        ) from exc

    return MultiServerMCPClient(_build_mcp_config())


async def _get_online_mcp_tools() -> list[Any]:
    """获取 Online MCP tools。"""

    client = _build_mcp_client()
    return await client.get_tools()


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

    tools = await _get_online_mcp_tools()
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

    tools = await _get_online_mcp_tools()
    context7_tools = [tool_obj for tool_obj in tools if "context7" in _tool_name(tool_obj).lower()]
    candidate_tools = context7_tools or tools

    # 优先 query / docs 类工具；如果服务只暴露 resolve 工具，也会显式返回其结果。
    preferred = None
    for tool_obj in candidate_tools:
        name = _tool_name(tool_obj).lower()
        if "query" in name or "docs" in name or "documentation" in name:
            preferred = tool_obj
            break
    if preferred is None:
        preferred = _select_tool(candidate_tools, ("context7",))

    return await _invoke_tool_with_candidate_payloads(
        preferred,
        [
            {"query": topic},
            {"topic": topic},
            {"libraryName": topic, "query": topic},
            {"libraryId": topic, "query": topic},
        ],
    )


def _run_async(coro):
    """在同步 tool 中执行异步 MCP 调用。"""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise MCPToolError("当前线程已有运行中的 event loop，请改用 async MCP 调用入口。")


@tool
@traceable(name="tavily_mcp_search", run_type="tool")
def tavily_mcp_search(query: str) -> str:
    """通过 Tavily remote MCP 搜索网页资料。"""

    try:
        result = _run_async(_tavily_search_async(query=query, max_results=5))
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
@traceable(name="context7_mcp_query", run_type="tool")
def context7_mcp_query(topic: str) -> str:
    """通过 Context7 MCP 查询官方库、框架或 SDK 文档。"""

    try:
        result = _run_async(_context7_query_async(topic=topic))
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
