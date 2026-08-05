"""Web Search SubAgent 输出解析与调度测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.tools import web_search_subagent
from src.tools.web_search_subagent import _parse_json_object_from_model_output


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


class WebSearchSubAgentDispatchTest(unittest.TestCase):
    """验证 Web Search 采用确定性调度，不默认调用外部 worker。"""

    def test_default_dispatch_does_not_call_external_workers(self) -> None:
        """普通模式只调用 SerpAPI/MCP，不调用 Claude 或 Codex。"""

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
            outputs = web_search_subagent._collect_web_tool_outputs(
                task,
                allow_external_workers=False,
            )

        tool_names = [item["tool_name"] for item in outputs]
        self.assertEqual(
            tool_names,
            ["tavily_mcp_search", "context7_mcp_query", "serp_api_search"],
        )


    def test_url_query_dispatches_page_observation_tools(self) -> None:
        """query 中包含 URL 时调用 Playwright 和 DevTools 页面观察工具。"""

        task = {
            "task_id": "T1",
            "question_id": "Q1",
            "query": "https://docs.cursor.com",
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
            outputs = web_search_subagent._collect_web_tool_outputs(
                task,
                allow_external_workers=False,
            )

        tool_names = [item["tool_name"] for item in outputs]
        self.assertIn("playwright_mcp_fetch_page", tool_names)
        self.assertIn("devtools_mcp_inspect_page", tool_names)

    def test_iteration_dispatch_calls_at_most_one_external_worker_chain(self) -> None:
        """迭代模式允许进入一个外部 worker 决策链。"""

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
            outputs = web_search_subagent._collect_web_tool_outputs(
                task,
                allow_external_workers=True,
            )

        tool_names = [item["tool_name"] for item in outputs]
        self.assertEqual(tool_names.count("call_claude_code_worker"), 1)
        self.assertNotIn("call_codex_worker", tool_names)


if __name__ == "__main__":
    unittest.main()
