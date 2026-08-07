"""Web Search SubAgent 输出解析与调度测试。"""

from __future__ import annotations

import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
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
        self.assertIn("playwright_mcp_fetch_page", tool_names)
        self.assertNotIn("devtools_mcp_inspect_page", tool_names)
        self.assertNotIn("playwright_mcp_fetch_serpapi_result_page_1", tool_names)

    def test_collect_does_not_fetch_serpapi_result_pages(self) -> None:
        """首次收集阶段不再抓取 SerpAPI 结果页正文。"""

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
                            {"url": "https://example.com/a", "title": "A", "snippet": "短"},
                            {"url": "https://example.com/b", "title": "B", "snippet": "短"},
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

        with patch.object(web_search_subagent, "_call_tool", side_effect=fake_call_tool):
            outputs = web_search_subagent._collect_web_tool_outputs(task)

        tool_names = [item["tool_name"] for item in outputs]
        self.assertEqual(
            tool_names,
            ["tavily_mcp_search", "serp_api_search"],
        )

    def test_compact_tool_outputs_for_summarize_trims_payload(self) -> None:
        """汇总前保留全部搜索结果字段裁剪，并截断 Playwright / Context7 正文。"""

        long_body = "正文" * 2000
        tool_outputs = [
            {
                "tool_name": "serp_api_search",
                "ok": True,
                "error": None,
                "output": {
                    "provider": "serpapi",
                    "results": [
                        {
                            "title": f"T{index}",
                            "url": f"https://example.com/{index}",
                            "snippet": f"S{index}",
                            "published_at": "2026-01-01",
                            "source": "example",
                            "position": index,
                        }
                        for index in range(1, 8)
                    ],
                },
            },
            {
                "tool_name": "tavily_mcp_search",
                "ok": True,
                "error": None,
                "output": {
                    "provider": "tavily_mcp",
                    "raw_result": json.dumps(
                        {
                            "results": [
                                {
                                    "title": f"Tv{index}",
                                    "url": f"https://tavily.example/{index}",
                                    "content": f"C{index}",
                                    "published_date": "2026-02-01",
                                }
                                for index in range(1, 6)
                            ]
                        },
                        ensure_ascii=False,
                    ),
                },
            },
            {
                "tool_name": "context7_mcp_query",
                "ok": True,
                "error": None,
                "output": {
                    "provider": "context7_mcp",
                    "raw_result": json.dumps(
                        {
                            "library_id": "/demo/lib",
                            "resolve_result": "resolve " + ("r" * 1000),
                            "docs_result": long_body,
                        },
                        ensure_ascii=False,
                    ),
                },
            },
            {
                "tool_name": "playwright_mcp_fetch_serpapi_result_page_1",
                "ok": True,
                "error": None,
                "input": {"url": "https://example.com/page"},
                "output": {
                    "navigation_result": "ok",
                    "snapshot_result": long_body,
                    "title": "Page",
                },
            },
        ]

        with patch.dict(
            "os.environ",
            {
                "WEB_SEARCH_CONTEXT7_MAX_CHARS": "1000",
                "WEB_SEARCH_PLAYWRIGHT_MAX_CHARS": "800",
            },
        ):
            compact = web_search_subagent._compact_tool_outputs_for_summarize(tool_outputs)

        serp = compact[0]["output"]["results"]
        self.assertEqual(len(serp), 7)
        self.assertEqual(set(serp[0].keys()), {"title", "url", "snippet", "published_at"})

        tavily = compact[1]["output"]["results"]
        self.assertEqual(len(tavily), 5)

        context7_docs = compact[2]["output"]["docs_result"]
        self.assertLessEqual(len(context7_docs), 1000 + len("...[truncated]"))
        self.assertTrue(context7_docs.endswith("...[truncated]"))

        playwright = compact[3]["output"]
        self.assertEqual(playwright["url"], "https://example.com/page")
        self.assertEqual(playwright["title"], "Page")
        self.assertLessEqual(len(playwright["content"]), 800 + len("...[truncated]"))
        self.assertNotIn("snapshot_result", playwright)
        self.assertNotIn("navigation_result", playwright)

    def test_keep_serpapi_top_n_after_score(self) -> None:
        """打分后仅按综合分保留 SerpAPI top N，Tavily 结果不受影响。"""

        result = {
            "results": [
                {
                    "title": "Tavily",
                    "url_or_path": "https://tavily.example/1",
                    "snippet": "tavily",
                    "source_name": "tavily",
                    "relevance_score": 0.5,
                    "answer_coverage_score": 0.5,
                    "source_confidence_score": 0.5,
                    "freshness_score": 0.5,
                    "score_reason": "tavily",
                },
                {
                    "title": "Serp Low",
                    "url_or_path": "https://example.com/low",
                    "snippet": "low",
                    "source_name": "serpapi",
                    "relevance_score": 0.2,
                    "answer_coverage_score": 0.2,
                    "source_confidence_score": 0.2,
                    "freshness_score": 0.2,
                    "score_reason": "low",
                },
                {
                    "title": "Serp High",
                    "url_or_path": "https://example.com/high",
                    "snippet": "high",
                    "source_name": "serpapi",
                    "relevance_score": 0.9,
                    "answer_coverage_score": 0.9,
                    "source_confidence_score": 0.9,
                    "freshness_score": 0.9,
                    "score_reason": "high",
                },
                {
                    "title": "Serp Mid",
                    "url_or_path": "https://example.com/mid",
                    "snippet": "mid",
                    "source_name": "serpapi",
                    "relevance_score": 0.6,
                    "answer_coverage_score": 0.6,
                    "source_confidence_score": 0.6,
                    "freshness_score": 0.6,
                    "score_reason": "mid",
                },
            ],
            "web_hitl_required": False,
            "hitl_reason": None,
        }

        with patch.dict("os.environ", {"WEB_SEARCH_SERPAPI_KEEP_TOP_N": "2"}):
            kept = web_search_subagent._keep_serpapi_top_n_after_score(result)

        titles = [item["title"] for item in kept["results"]]
        self.assertEqual(titles, ["Tavily", "Serp High", "Serp Mid"])

    def test_serpapi_top1_fetch_skipped_when_question_passes(self) -> None:
        """问题整体分达标时不抓取 SerpAPI 正文。"""

        result = {
            "results": [
                {
                    "title": "Serp High",
                    "url_or_path": "https://example.com/high",
                    "snippet": "high",
                    "source_name": "serpapi",
                    "relevance_score": 0.9,
                    "answer_coverage_score": 0.9,
                    "source_confidence_score": 0.9,
                    "freshness_score": 0.9,
                    "score_reason": "high",
                }
            ]
        }

        with patch.dict(
            "os.environ",
            {
                "WEB_SEARCH_QUESTION_PASS_SCORE": "0.75",
                "WEB_SEARCH_SERPAPI_TOP1_HIGH_SCORE": "0.7",
            },
        ):
            self.assertIsNone(web_search_subagent._serpapi_top1_fetch_url(result))

    def test_serpapi_top1_fetch_when_overall_low_and_top1_high(self) -> None:
        """整体分未达标且 SerpAPI top1（relevance/confidence）够高时返回抓取 URL。"""

        result = {
            "results": [
                {
                    "title": "Weak",
                    "url_or_path": "https://example.com/weak",
                    "snippet": "weak",
                    "source_name": "tavily",
                    "relevance_score": 0.2,
                    "answer_coverage_score": 0.2,
                    "source_confidence_score": 0.2,
                    "freshness_score": 0.2,
                    "score_reason": "weak",
                },
                {
                    "title": "Serp High",
                    "url_or_path": "https://example.com/high",
                    "snippet": "high",
                    "source_name": "serpapi",
                    "relevance_score": 0.9,
                    "answer_coverage_score": 0.1,
                    "source_confidence_score": 0.8,
                    "freshness_score": 0.1,
                    "score_reason": "high gate",
                },
            ]
        }

        with patch.dict(
            "os.environ",
            {
                "WEB_SEARCH_QUESTION_PASS_SCORE": "0.75",
                "WEB_SEARCH_SERPAPI_TOP1_HIGH_SCORE": "0.7",
            },
        ):
            self.assertEqual(
                web_search_subagent._serpapi_top1_fetch_url(result),
                "https://example.com/high",
            )

    def test_serpapi_top1_fetch_ignores_coverage_and_freshness(self) -> None:
        """top1 门槛只看 relevance/confidence，综合分高但两分低时不抓取。"""

        result = {
            "results": [
                {
                    "title": "Serp Composite High",
                    "url_or_path": "https://example.com/composite",
                    "snippet": "composite",
                    "source_name": "serpapi",
                    "relevance_score": 0.4,
                    "answer_coverage_score": 1.0,
                    "source_confidence_score": 0.4,
                    "freshness_score": 1.0,
                    "score_reason": "coverage heavy",
                }
            ]
        }

        with patch.dict(
            "os.environ",
            {
                "WEB_SEARCH_QUESTION_PASS_SCORE": "0.95",
                "WEB_SEARCH_SERPAPI_TOP1_HIGH_SCORE": "0.7",
            },
        ):
            self.assertIsNone(web_search_subagent._serpapi_top1_fetch_url(result))

    def test_maybe_fetch_serpapi_page_and_enrich(self) -> None:
        """条件满足时应抓取 top1 正文并由代码写入该条，不二次汇总。"""

        tool_outputs = [{"tool_name": "serp_api_search", "ok": True}]
        first_result = {
            "results": [
                {
                    "title": "Weak",
                    "url_or_path": "https://example.com/weak",
                    "snippet": "weak",
                    "source_name": "tavily",
                    "relevance_score": 0.2,
                    "answer_coverage_score": 0.2,
                    "source_confidence_score": 0.2,
                    "freshness_score": 0.2,
                    "score_reason": "weak",
                },
                {
                    "title": "Serp High",
                    "url_or_path": "https://example.com/high",
                    "snippet": "high",
                    "source_name": "serpapi",
                    "relevance_score": 0.9,
                    "answer_coverage_score": 0.1,
                    "source_confidence_score": 0.8,
                    "freshness_score": 0.1,
                    "score_reason": "high",
                },
            ],
            "web_hitl_required": False,
            "hitl_reason": None,
        }

        with (
            patch.object(
                web_search_subagent,
                "_call_tool",
                return_value={
                    "tool_name": "playwright_mcp_fetch_serpapi_result_page_1",
                    "ok": True,
                    "input": {"url": "https://example.com/high"},
                    "output": {
                        "title": "Page Title",
                        "snapshot_result": "完整页面正文内容",
                    },
                    "error": None,
                },
            ) as call_tool,
            patch.object(
                web_search_subagent,
                "_summarize_web_tool_outputs",
            ) as summarize,
            patch.dict(
                "os.environ",
                {
                    "WEB_SEARCH_QUESTION_PASS_SCORE": "0.75",
                    "WEB_SEARCH_SERPAPI_TOP1_HIGH_SCORE": "0.7",
                },
            ),
        ):
            updated_outputs, updated_result = (
                web_search_subagent._maybe_fetch_serpapi_page_and_enrich(
                    tool_outputs,
                    first_result,
                )
            )

        call_tool.assert_called_once()
        summarize.assert_not_called()
        self.assertEqual(len(updated_outputs), 2)
        serp_item = updated_result["results"][1]
        self.assertEqual(serp_item["title"], "Page Title")
        self.assertEqual(serp_item["snippet"], "完整页面正文内容")
        self.assertEqual(serp_item["relevance_score"], 0.9)
        self.assertEqual(updated_result["results"][0]["snippet"], "weak")

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


class WebSearchToolConcurrencyLimitTest(unittest.TestCase):
    """验证高成本工具的分级并发限流。"""

    def test_playwright_and_tavily_do_not_overlap(self) -> None:
        """Playwright 与 Tavily 各自并发上限应为 1。"""

        active_lock = threading.Lock()
        active_counts = {
            "playwright_mcp_fetch_page": 0,
            "tavily_mcp_search": 0,
        }
        max_counts = {
            "playwright_mcp_fetch_page": 0,
            "tavily_mcp_search": 0,
        }

        class FakeTool:
            def __init__(self, tool_name: str) -> None:
                self.tool_name = tool_name

            def invoke(self, payload: dict) -> str:
                with active_lock:
                    active_counts[self.tool_name] += 1
                    max_counts[self.tool_name] = max(
                        max_counts[self.tool_name],
                        active_counts[self.tool_name],
                    )
                time.sleep(0.05)
                with active_lock:
                    active_counts[self.tool_name] -= 1
                return json.dumps({"ok": True, "payload": payload}, ensure_ascii=False)

        def run_calls(tool_name: str) -> None:
            tool_obj = FakeTool(tool_name)
            futures = []
            with ThreadPoolExecutor(max_workers=3) as executor:
                for index in range(3):
                    futures.append(
                        executor.submit(
                            web_search_subagent._call_tool,
                            tool_name,
                            tool_obj,
                            {"index": index},
                        )
                    )
                for future in futures:
                    result = future.result()
                    self.assertTrue(result["ok"])

        playwright_thread = threading.Thread(
            target=run_calls,
            args=("playwright_mcp_fetch_page",),
        )
        tavily_thread = threading.Thread(
            target=run_calls,
            args=("tavily_mcp_search",),
        )
        playwright_thread.start()
        tavily_thread.start()
        playwright_thread.join(timeout=5)
        tavily_thread.join(timeout=5)

        self.assertEqual(max_counts["playwright_mcp_fetch_page"], 1)
        self.assertEqual(max_counts["tavily_mcp_search"], 1)


if __name__ == "__main__":
    unittest.main()
