"""Web Search SubAgent 工具。

设计：
- 内部用 `create_agent` 创建一个 Web Search SubAgent。
- SubAgent 可调用若干 `@tool` 包装的内部工具。
- 再用 `@tool` 把整个 SubAgent 包成外层工具，供 LangGraph 节点调用。

当前内部工具全部是替代实现，不访问真实网络。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from langchain.agents import create_agent
from langchain.tools import tool

from src.llm.chat import build_chat_model
from src.llm.prompt_loader import load_prompt, render_prompt_template
from src.tools.external_agent_workers import call_claude_code_worker, call_codex_worker
from src.tools.online_mcp_tools import context7_mcp_query, tavily_mcp_search


@tool
def placeholder_search_web_api(query: str, source_type: str) -> str:
    """使用替代 Web Search API 返回候选搜索结果。"""

    result = {
        "title": f"Web 替代检索结果：{query}",
        "url": f"placeholder://web/{source_type}/{query.replace(' ', '_')}",
        "snippet": f"替代 Web 搜索结果：围绕 `{query}` 提供 {source_type} 类型候选资料。",
        "source": "placeholder_web_search",
        "published_at": None,
    }
    return json.dumps([result], ensure_ascii=False)


@tool
def placeholder_fetch_static_page(url: str) -> str:
    """使用替代静态网页抓取工具返回网页正文。"""

    page = {
        "url": url,
        "title": "替代网页正文",
        "content": f"这是 `{url}` 的替代网页正文，用于验证 Web Search SubAgent 的工程链路。",
        "login_required": False,
        "blocked_reason": None,
    }
    return json.dumps(page, ensure_ascii=False)


@tool
def placeholder_detect_login_required(url: str, page_content: str) -> str:
    """判断替代页面是否需要登录或人工接管。"""

    result = {
        "login_required": False,
        "site_name": "placeholder_web",
        "url": url,
        "reason": None,
    }
    return json.dumps(result, ensure_ascii=False)


@tool
def placeholder_extract_page_content(page_content: str) -> str:
    """从替代网页正文抽取证据片段。"""

    extracted = {
        "title": "替代网页证据片段",
        "snippet": page_content[:500],
        "published_at": None,
    }
    return json.dumps(extracted, ensure_ascii=False)


@lru_cache(maxsize=1)
def build_web_search_subagent():
    """构建 Web Search SubAgent。"""

    model = build_chat_model("web_search_subagent")
    prompt = load_prompt("web_search_subagent.yml")
    return create_agent(
        model=model,
        tools=[
            placeholder_search_web_api,
            placeholder_fetch_static_page,
            placeholder_detect_login_required,
            placeholder_extract_page_content,
            tavily_mcp_search,
            context7_mcp_query,
            call_claude_code_worker,
            call_codex_worker,
        ],
        system_prompt=prompt["system_prompt"],
    )


@tool("web_search_subagent_tool")
def web_search_subagent_tool(task_json: str) -> str:
    """调用 Web Search SubAgent 完成单个 SearchTask 的候选证据收集。"""

    task = json.loads(task_json)
    agent = build_web_search_subagent()
    query = task["query"]
    source_type = task["source_type"]
    prompt = load_prompt("web_search_subagent.yml")
    user_prompt = render_prompt_template(
        prompt["user_prompt_template"],
        {
            "task_id": task["task_id"],
            "question_id": task["question_id"],
            "query": query,
            "source_type": source_type,
        },
    )
    response = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ]
        }
    )
    return response["messages"][-1].content


def run_web_search_subagent_for_task(task: dict[str, Any]) -> dict[str, Any]:
    """运行外层 SubAgent 工具，并解析为 Python dict。"""

    raw_result = web_search_subagent_tool.invoke(
        json.dumps(task, ensure_ascii=False)
    )
    try:
        parsed = json.loads(raw_result)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Web Search SubAgent 未返回合法 JSON：{raw_result}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Web Search SubAgent 返回值必须是 JSON 对象")
    if "results" not in parsed or not isinstance(parsed["results"], list):
        raise ValueError("Web Search SubAgent 返回值缺少 results 列表")
    if "web_hitl_required" not in parsed:
        raise ValueError("Web Search SubAgent 返回值缺少 web_hitl_required")

    return parsed
