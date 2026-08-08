"""LangChain tool 返回结果单元测试。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.tools import online_mcp_tools, web_search_subagent
from src.tools.external_agent_workers import call_claude_code_worker, call_codex_worker
from src.tools.local_document_search import local_markdown_search
from src.tools.structured_data import structured_ai_product_search
from src.tools.web_search_subagent import serp_api_search, web_search_subagent_tool


class ToolReturnsTest(unittest.TestCase):
    """确保每个工具都能返回可解析或非空结果。"""

    def _close_and_return(self, coroutine_obj: object, result: object) -> object:
        """关闭 wait_for 收到的未执行协程，避免单元测试产生未 await 警告。"""

        close = getattr(coroutine_obj, "close", None)
        if callable(close):
            close()
        return result

    def _mock_online_mcp_success(self, result: object) -> None:
        """模拟 asyncio.wait_for 和同步异步桥，避免真实启动 MCP。"""

        wait_for_patch = patch.object(
            online_mcp_tools.asyncio,
            "wait_for",
            new=lambda coroutine_obj, timeout: self._close_and_return(
                coroutine_obj,
                result,
            ),
        )
        run_async_patch = patch.object(
            online_mcp_tools,
            "_run_async",
            new=lambda wrapped_result: wrapped_result,
        )
        wait_for_patch.start()
        run_async_patch.start()
        self.addCleanup(wait_for_patch.stop)
        self.addCleanup(run_async_patch.stop)

    def test_local_markdown_search_returns_text(self) -> None:
        """本地 Markdown 搜索工具返回文本。"""

        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            (base / "note.md").write_text("# Cursor\n\nCursor 是 AI IDE。", encoding="utf-8")
            with patch.dict(os.environ, {"LOCAL_DOCUMENTS_BASE_PATH": str(base)}):
                result = local_markdown_search.invoke("Cursor")

        self.assertIn("Cursor", result)

    def test_structured_ai_product_search_returns_text(self) -> None:
        """结构化产品搜索工具返回文本。"""

        result = structured_ai_product_search.invoke("Cursor")

        self.assertIsInstance(result, str)
        self.assertTrue(result.strip())

    def test_serp_api_search_returns_json(self) -> None:
        """SerpAPI 工具返回 JSON。"""

        class FakeGoogleSearch:
            """模拟 SerpAPI JSON 搜索响应。"""

            def __init__(self, params: dict) -> None:
                self.params = params

            def get_dict(self) -> dict:
                return {
                    "search_metadata": {"id": "test-search", "status": "Success"},
                    "organic_results": [
                        {
                            "position": 1,
                            "title": "Cursor official docs",
                            "link": "https://docs.cursor.com",
                            "snippet": "Cursor 官方文档。",
                            "source": "docs.cursor.com",
                            "date": "2026-01-01",
                        }
                    ],
                }

        with patch.object(
            web_search_subagent,
            "GoogleSearch",
            new=FakeGoogleSearch,
        ), patch.dict(
            os.environ,
            {"SERPAPI_API_KEY": "test-key"},
        ):
            result = serp_api_search.invoke("Cursor docs")

        payload = json.loads(result)
        self.assertEqual(payload["provider"], "serp")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["results"][0]["url"], "https://docs.cursor.com")
        self.assertEqual(payload["results"][0]["published_at"], "2026-01-01")

    def test_tavily_mcp_search_returns_json(self) -> None:
        """Tavily MCP 工具返回 JSON。"""

        self._mock_online_mcp_success("tavily result")
        result = online_mcp_tools.tavily_mcp_search.invoke("Cursor docs")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "tavily_mcp")

    def test_you_com_api_search_returns_json(self) -> None:
        """you.com Search 工具应解析 web 结果为统一 results 形态。"""

        fake_payload = {
            "results": {
                "web": [
                    {
                        "url": "https://example.com/a",
                        "title": "Example A",
                        "description": "desc",
                        "snippets": ["more detail"],
                        "page_age": "2026-04-01T19:00:51",
                    }
                ]
            },
            "metadata": {"query": "Cursor docs", "latency": 0.1},
        }

        class FakeResponse:
            def read(self) -> bytes:
                return json.dumps(fake_payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
                return False

        with (
            patch.dict(os.environ, {"YDC_API_KEY": "test-ydc-key"}),
            patch.object(
                web_search_subagent,
                "urlopen",
                return_value=FakeResponse(),
            ) as mock_urlopen,
        ):
            result = web_search_subagent.you_com_api_search.invoke("Cursor docs")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "ydc")
        self.assertEqual(payload["results"][0]["url"], "https://example.com/a")
        self.assertIn("desc", payload["results"][0]["snippet"])
        self.assertEqual(payload["results"][0]["published_at"], "2026-04-01T19:00:51")
        request = mock_urlopen.call_args.args[0]
        self.assertIn("ydc-index.io/v1/search", request.full_url)
        self.assertEqual(request.get_header("X-api-key"), "test-ydc-key")

    def test_context7_mcp_query_returns_json(self) -> None:
        """Context7 MCP 工具返回 JSON，且 raw_result 可再解析。"""

        self._mock_online_mcp_success(
            {
                "library_id": "/demo/lib",
                "resolve_result": "resolved",
                "docs_result": "docs body",
            }
        )
        result = online_mcp_tools.context7_mcp_query.invoke("LangChain")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "context7_mcp")
        self.assertEqual(payload["library_id"], "/demo/lib")
        self.assertEqual(payload["docs_result"], "docs body")
        self.assertIsInstance(payload["raw_result"], dict)
        self.assertEqual(payload["raw_result"]["docs_result"], "docs body")

    def test_playwright_mcp_fetch_page_returns_json(self) -> None:
        """Playwright MCP 页面读取工具返回 JSON，正文可从顶层读取。"""

        self._mock_online_mcp_success(
            {
                "navigation_result": "ok",
                "snapshot_result": "page text",
            }
        )
        result = online_mcp_tools.playwright_mcp_fetch_page.invoke("https://example.com")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "playwright_mcp")
        self.assertEqual(payload["snapshot_result"], "page text")
        self.assertEqual(payload["raw_result"]["snapshot_result"], "page text")

    def test_tavily_mcp_search_raw_result_is_json_ready(self) -> None:
        """Tavily raw_result 不得是 Python dict 的 str()。"""

        self._mock_online_mcp_success(
            {"results": [{"title": "T", "url": "https://t.example", "content": "c"}]}
        )
        result = online_mcp_tools.tavily_mcp_search.invoke("Cursor docs")
        payload = json.loads(result)
        self.assertIsInstance(payload["raw_result"], dict)
        self.assertEqual(payload["raw_result"]["results"][0]["url"], "https://t.example")

    def test_devtools_mcp_inspect_page_returns_json(self) -> None:
        """Chrome DevTools MCP 页面观察工具返回 JSON。"""

        self._mock_online_mcp_success({"inspect_result": "page state"})
        result = online_mcp_tools.devtools_mcp_inspect_page.invoke("https://example.com")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "devtools_mcp")

    def test_claude_code_worker_returns_json_when_disabled(self) -> None:
        """Claude Code worker 禁用时返回结构化 JSON。"""

        with patch.dict(os.environ, {"ENABLE_CLAUDE_CODE_WORKER": "false"}):
            result = call_claude_code_worker.invoke("测试")

        payload = json.loads(result)
        self.assertEqual(payload["worker"], "claude_code")
        self.assertFalse(payload["enabled"])

    def test_codex_worker_returns_json_when_disabled(self) -> None:
        """Codex worker 禁用时返回结构化 JSON。"""

        with patch.dict(os.environ, {"ENABLE_CODEX_WORKER": "false"}):
            result = call_codex_worker.invoke("测试")

        payload = json.loads(result)
        self.assertEqual(payload["worker"], "codex")
        self.assertFalse(payload["enabled"])

    def test_web_search_subagent_tool_returns_json(self) -> None:
        """Web Search SubAgent 外层 tool 返回 JSON。"""

        task = {
            "task_id": "T1",
            "question_id": "Q1",
            "query": "Cursor docs",
            "source_type": "official_docs",
        }
        summary = {
            "results": [
                {
                    "title": "Cursor Docs",
                    "url_or_path": "https://docs.cursor.com",
                    "snippet": "Cursor docs summary",
                    "source_name": "serp",
                    "published_at": None,
                    "requires_login": False,
                    "blocked_reason": None,
                }
            ],
            "web_hitl_required": False,
            "hitl_reason": None,
        }
        with (
            patch.object(web_search_subagent, "_collect_web_tool_outputs", return_value=[]),
            patch.object(web_search_subagent, "_summarize_web_tool_outputs", return_value=summary),
        ):
            result = web_search_subagent_tool.invoke(json.dumps(task, ensure_ascii=False))

        payload = json.loads(result)
        self.assertFalse(payload["web_hitl_required"])
        self.assertEqual(payload["results"][0]["title"], "Cursor Docs")
        self.assertEqual(payload["tool_evaluation_records"], [])
        self.assertNotIn("tool_outputs", payload)


if __name__ == "__main__":
    unittest.main()
