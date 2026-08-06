"""Web Search SubAgent 输出解析与调度测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.tools import web_search_subagent
from src.tools.web_search_subagent import (
    _normalize_web_hitl_decision,
    _parse_json_object_from_model_output,
)


class WebSearchSubAgentParseTest(unittest.TestCase):
    """验证 SubAgent JSON 输出解析。"""

    def test_parse_raw_json_object(self) -> None:
        """解析裸 JSON 对象。"""

        parsed = _parse_json_object_from_model_output(
            '{"results": [], "web_hitl_required": false, "hitl_reason": null}'
        )

        self.assertEqual(parsed["results"], [])
        self.assertFalse(parsed["web_hitl_required"])

    def test_parse_json_markdown_fence(self) -> None:
        """兼容模型返回的 JSON 代码块。"""

        parsed = _parse_json_object_from_model_output(
            '```json\n{"results": [], "web_hitl_required": true, "hitl_reason": "需要人工确认"}\n```'
        )

        self.assertEqual(parsed["results"], [])
        self.assertTrue(parsed["web_hitl_required"])
        self.assertEqual(parsed["hitl_reason"], "需要人工确认")

    def test_page_fetch_failure_does_not_force_hitl_when_search_result_is_usable(self) -> None:
        """页面正文抓取失败但搜索结果可用时，不应默认进入 HITL。"""

        result = {
            "results": [
                {
                    "title": "示例文章",
                    "url_or_path": "https://example.com/article",
                    "snippet": "搜索摘要已经覆盖查询主题。",
                    "source_name": "serpapi",
                    "published_at": None,
                    "requires_login": False,
                    "blocked_reason": "HTTP 403 被拦截，无法获取页面正文",
                }
            ],
            "web_hitl_required": True,
            "hitl_reason": "playwright 页面正文抓取失败：about:blank 空内容",
        }

        normalized = _normalize_web_hitl_decision(result)

        self.assertFalse(normalized["web_hitl_required"])
        self.assertIn("降级为候选证据", normalized["hitl_reason"])


class WebSearchSubAgentDispatchTest(unittest.TestCase):
    """验证 Web Search 采用确定性调度，并且不包含 HITL / 外部 worker。"""

    def test_default_dispatch_does_not_call_external_workers(self) -> None:
        """Web Search 只调用 Web 检索工具，不调用 Claude 或 Codex。"""

        task = {
            "task_id": "T1",
            "question_id": "Q1",
            "query": "Cursor docs",
            "source_type": "official_docs",
        }
        with patch.object(web_search_subagent, "_call_tool") as call_tool:
            call_tool.side_effect = lambda name, tool_obj, payload: {
                "tool_name": name,
                "ok": True,
                "input": payload,
                "output": [],
                "error": None,
            }
            outputs = web_search_subagent._collect_web_tool_outputs(task)

        tool_names = [item["tool_name"] for item in outputs]
        self.assertEqual(
            tool_names,
            ["tavily_mcp_search", "context7_mcp_query", "serp_api_search"],
        )


    def test_url_query_dispatches_page_observation_tools(self) -> None:
        """query 中包含 URL 时只调用 Playwright 页面观察工具。"""

        task = {
            "task_id": "T1",
            "question_id": "Q1",
            "query": "https://docs.cursor.com",
            "source_type": "official_docs",
        }
        with (
            patch.object(web_search_subagent, "_call_tool") as call_tool,
            patch.dict("os.environ", {"ENABLE_BROWSER_MCP_TOOLS": "true"}),
        ):
            call_tool.side_effect = lambda name, tool_obj, payload: {
                "tool_name": name,
                "ok": True,
                "input": payload,
                "output": [],
                "error": None,
            }
            outputs = web_search_subagent._collect_web_tool_outputs(task)

        tool_names = [item["tool_name"] for item in outputs]
        self.assertIn("playwright_mcp_fetch_page", tool_names)
        self.assertNotIn("devtools_mcp_inspect_page", tool_names)



    def test_serpapi_urls_dispatch_page_fetch_tools(self) -> None:
        """SerpAPI 返回 URL 后继续抓取页面正文，而不是只用搜索摘要。"""

        task = {
            "task_id": "T1",
            "question_id": "Q1",
            "query": "Cursor docs",
            "source_type": "blog",
        }

        def fake_call_tool(name, tool_obj, payload):
            if name == "serp_api_search":
                return {
                    "tool_name": name,
                    "ok": True,
                    "input": payload,
                    "output": {
                        "results": [
                            {"url": "https://example.com/a", "title": "A"},
                            {"url": "https://example.com/b", "title": "B"},
                            {"url": "https://example.com/c", "title": "C"},
                        ]
                    },
                    "error": None,
                }
            return {
                "tool_name": name,
                "ok": True,
                "input": payload,
                "output": {},
                "error": None,
            }

        with (
            patch.object(web_search_subagent, "_call_tool", side_effect=fake_call_tool),
            patch.dict("os.environ", {"WEB_SEARCH_FETCH_SERPAPI_TOP_N": "2", "ENABLE_BROWSER_MCP_TOOLS": "true"}),
        ):
            outputs = web_search_subagent._collect_web_tool_outputs(task)

        tool_names = [item["tool_name"] for item in outputs]
        self.assertIn("playwright_mcp_fetch_serpapi_result_page_1", tool_names)
        self.assertIn("playwright_mcp_fetch_serpapi_result_page_2", tool_names)
        self.assertNotIn("playwright_mcp_fetch_serpapi_result_page_3", tool_names)

    def test_dispatch_does_not_call_external_worker_in_web_search(self) -> None:
        """Web Search 工具池不包含 Claude 或 Codex。"""

        task = {
            "task_id": "T1",
            "question_id": "Q1",
            "query": "Cursor docs",
            "source_type": "official_docs",
        }
        with patch.object(web_search_subagent, "_call_tool") as call_tool:
            call_tool.side_effect = lambda name, tool_obj, payload: {
                "tool_name": name,
                "ok": name == "call_claude_code_worker",
                "input": payload,
                "output": {"ok": name == "call_claude_code_worker"},
                "error": None,
            }
            outputs = web_search_subagent._collect_web_tool_outputs(task)

        tool_names = [item["tool_name"] for item in outputs]
        self.assertNotIn("call_claude_code_worker", tool_names)
        self.assertNotIn("call_codex_worker", tool_names)


if __name__ == "__main__":
    unittest.main()
