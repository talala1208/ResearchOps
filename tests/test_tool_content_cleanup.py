"""代码路径工具正文清洗测试。"""

from __future__ import annotations

import json
import unittest

from src.artifacts.tool_content_cleanup import (
    clean_context7_docs,
    clean_playwright_body,
    clean_tavily_snippet,
    compact_devtools_observation,
    unwrap_text_content,
)
from src.tools import web_search_subagent


class ToolContentCleanupTest(unittest.TestCase):
    """验证确定性清洗不把结构化噪声当正文。"""

    def test_unwrap_text_blocks(self) -> None:
        text = unwrap_text_content(
            [{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}]
        )
        self.assertIn("第一段", text)
        self.assertIn("第二段", text)

    def test_unwrap_keeps_json_payload_when_not_text_shaped(self) -> None:
        """Tavily MCP：text 内是含 results 的 JSON 时，不得解成空串。"""

        payload = {
            "results": [
                {
                    "title": "Doc",
                    "url": "https://example.com/a",
                    "content": "snippet body",
                    "score": 0.9,
                }
            ]
        }
        text = unwrap_text_content(
            [{"type": "text", "text": json.dumps(payload), "id": "x"}]
        )
        self.assertIn('"results"', text)
        self.assertIn("snippet body", text)
        parsed = json.loads(text)
        self.assertEqual(parsed["results"][0]["url"], "https://example.com/a")

    def test_context7_docs_rejects_payload_dict(self) -> None:
        cleaned = clean_context7_docs(
            {
                "library_id": "/websites/langchain_langsmith",
                "resolve_result": "Available Libraries",
            },
            max_chars=1000,
        )
        self.assertEqual(cleaned, "")

    def test_context7_docs_accepts_text_blocks(self) -> None:
        cleaned = clean_context7_docs(
            [{"type": "text", "text": "LangSmith tracing docs"}],
            max_chars=1000,
        )
        self.assertEqual(cleaned, "LangSmith tracing docs")

    def test_context7_materialize_does_not_use_raw_payload_as_docs(self) -> None:
        tool_outputs = [
            {
                "tool_name": "context7_mcp_query",
                "ok": True,
                "error": None,
                "output": {
                    "provider": "context7_mcp",
                    "raw_result": json.dumps(
                        {
                            "library_id": "/websites/langchain_langsmith",
                            "resolve_result": [
                                {
                                    "type": "text",
                                    "text": "Available Libraries:\n- LangSmith",
                                }
                            ],
                            "docs_result": [
                                {
                                    "type": "text",
                                    "text": "Official LangSmith observability docs body",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            }
        ]
        candidates = web_search_subagent._materialize_context7_results_from_tool_outputs(
            tool_outputs
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0]["docs_result"],
            "Official LangSmith observability docs body",
        )
        self.assertIn("Available Libraries", candidates[0]["resolve_result"])
        self.assertNotIn("library_id", candidates[0]["docs_result"])

    def test_context7_materialize_accepts_legacy_python_repr_raw_result(self) -> None:
        """旧版 str(dict) raw_result 仍应解析出 docs，而不是整包当正文。"""

        payload = {
            "library_id": "/websites/langchain_langsmith",
            "resolve_result": [{"type": "text", "text": "Available Libraries"}],
            "docs_result": [
                {"type": "text", "text": "Official LangSmith observability docs body"}
            ],
        }
        tool_outputs = [
            {
                "tool_name": "context7_mcp_query",
                "ok": True,
                "error": None,
                "output": {
                    "provider": "context7_mcp",
                    "raw_result": str(payload),
                },
            }
        ]
        candidates = web_search_subagent._materialize_context7_results_from_tool_outputs(
            tool_outputs
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0]["docs_result"],
            "Official LangSmith observability docs body",
        )
        self.assertNotIn("library_id", candidates[0]["docs_result"])

    def test_tavily_and_playwright_clean_whitespace(self) -> None:
        snippet = clean_tavily_snippet("hello   world\n\n\nnext", max_chars=100)
        self.assertEqual(snippet, "hello world\n\nnext")
        body = clean_playwright_body(
            "Skip to content\n\n正文段落\n\n\nSign in\n更多内容",
            max_chars=1000,
        )
        self.assertIn("正文段落", body)
        self.assertIn("更多内容", body)
        self.assertNotIn("Skip to content", body)
        self.assertNotIn("Sign in", body)

    def test_devtools_observation_is_compact(self) -> None:
        compact = compact_devtools_observation(
            {
                "ok": True,
                "provider": "devtools_mcp",
                "raw_result": {
                    "navigation_result": "nav " * 500,
                    "inspect_result": [{"type": "text", "text": "login form visible"}],
                },
            },
            max_field_chars=80,
        )
        self.assertTrue(compact["ok"])
        self.assertLessEqual(len(compact["observation"]["navigation_summary"]), 80)
        self.assertEqual(
            compact["observation"]["inspect_summary"],
            "login form visible",
        )
        self.assertNotIn("raw_result", compact)


if __name__ == "__main__":
    unittest.main()
