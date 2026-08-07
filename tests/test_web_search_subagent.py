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
    _finalize_playwright_only_hitl,
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

    def test_page_fetch_failure_does_not_force_hitl_without_login_signal(self) -> None:
        """普通 Playwright 抓取失败不应触发 Web HITL。"""

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
        tool_outputs = [
            {
                "tool_name": "playwright_mcp_fetch_serpapi_result_page_1",
                "ok": False,
                "error": "about:blank empty content",
                "input": {"url": "https://example.com/article"},
                "output": {},
            }
        ]

        normalized = _finalize_playwright_only_hitl(tool_outputs, result)

        self.assertFalse(normalized["web_hitl_required"])
        self.assertIsNone(normalized["hitl_reason"])

    def test_playwright_login_signal_triggers_hitl(self) -> None:
        """Playwright 检测到登录墙时应触发 Web HITL。"""

        result = {
            "results": [
                {
                    "title": "Private Doc",
                    "url": "https://example.com/login-required",
                    "url_or_path": "https://example.com/login-required",
                    "snippet": "teaser",
                    "source_name": "serpapi",
                    "requires_login": False,
                }
            ],
            "web_hitl_required": False,
            "hitl_reason": None,
        }
        tool_outputs = [
            {
                "tool_name": "playwright_mcp_fetch_serpapi_result_page_1",
                "ok": False,
                "error": "Please sign in to continue",
                "input": {"url": "https://example.com/login-required"},
                "output": {"snapshot_result": "login form"},
            }
        ]

        finalized = _finalize_playwright_only_hitl(tool_outputs, result)

        self.assertTrue(finalized["web_hitl_required"])
        self.assertTrue(finalized["results"][0]["requires_login"])
        self.assertTrue(finalized["results"][0]["playwright_hitl"])
        self.assertIn("登录", finalized["hitl_reason"])


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

    def test_compact_tool_outputs_for_summarize_only_keeps_serp(self) -> None:
        """汇总 LLM 只接收 SerpAPI 压缩结果。"""

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
                        for index in range(1, 4)
                    ],
                },
            },
            {
                "tool_name": "tavily_mcp_search",
                "ok": True,
                "error": None,
                "output": {"provider": "tavily_mcp", "results": []},
            },
            {
                "tool_name": "context7_mcp_query",
                "ok": True,
                "error": None,
                "output": {"provider": "context7_mcp", "docs_result": "docs"},
            },
            {
                "tool_name": "playwright_mcp_fetch_page",
                "ok": True,
                "error": None,
                "input": {"url": "https://example.com/page"},
                "output": {"title": "Page", "snapshot_result": "body"},
            },
        ]

        compact = web_search_subagent._compact_tool_outputs_for_summarize(tool_outputs)
        self.assertEqual([item["tool_name"] for item in compact], ["serp_api_search"])
        serp = compact[0]["output"]["results"]
        self.assertEqual(len(serp), 3)
        self.assertEqual(set(serp[0].keys()), {"title", "url", "snippet", "published_at"})

    def test_materialize_tavily_keeps_score_without_summarize(self) -> None:
        """Tavily 结果应保留 score 并由代码物化，不依赖 summarize。"""

        tool_outputs = [
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
                                    "title": "Tavily Doc",
                                    "url": "https://tavily.example/doc",
                                    "content": "答案相关摘要",
                                    "score": 0.91,
                                    "published_date": "2026-03-01",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                },
            }
        ]

        candidates = web_search_subagent._materialize_tavily_results_from_tool_outputs(
            tool_outputs
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["score"], 0.91)
        self.assertEqual(candidates[0]["url"], "https://tavily.example/doc")
        self.assertEqual(candidates[0]["snippet"], "答案相关摘要")
        self.assertEqual(candidates[0]["source_name"], "tavily")

    def test_materialize_context7_without_summarize(self) -> None:
        """Context7 应保留 docs_result/resolve_result，不进 summarize。"""

        tool_outputs = [
            {
                "tool_name": "context7_mcp_query",
                "ok": True,
                "error": None,
                "output": {
                    "provider": "context7_mcp",
                    "raw_result": json.dumps(
                        {
                            "library_id": "/demo/lib",
                            "resolve_result": "resolved",
                            "docs_result": "official docs body",
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
        self.assertEqual(candidates[0]["docs_result"], "official docs body")
        self.assertEqual(candidates[0]["resolve_result"], "resolved")
        self.assertEqual(candidates[0]["source_name"], "context7")
        self.assertEqual(candidates[0]["score_bucket"], "context7")
        self.assertEqual(candidates[0]["snippet"], "")
        self.assertEqual(candidates[0]["docs_result"], "official docs body")
        self.assertEqual(candidates[0]["relevance_score"], 0.5)

    def test_keep_serpapi_top_n_after_score(self) -> None:
        """打分后仅按 score 保留 SerpAPI top N。"""

        result = {
            "results": [
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
        result = web_search_subagent._attach_serpapi_composite_scores(result)

        with patch.dict("os.environ", {"WEB_SEARCH_SERPAPI_KEEP_TOP_N": "2"}):
            kept = web_search_subagent._keep_serpapi_top_n_after_score(result)

        titles = [item["title"] for item in kept["results"]]
        self.assertEqual(titles, ["Serp High", "Serp Mid"])
        self.assertIn("score", kept["results"][0])

    def test_resolve_fetch_url_uses_llm_decision_when_relevance_passes(self) -> None:
        """LLM 要求抓取且 URL 合法、relevance 超阈值时返回该 URL。"""

        result = {
            "needs_page_fetch": True,
            "fetch_url": "https://example.com/high",
            "fetch_reason": "缺少条款全文",
            "results": [
                {
                    "title": "Serp High",
                    "url": "https://example.com/high",
                    "url_or_path": "https://example.com/high",
                    "snippet": "high",
                    "source_name": "serpapi",
                    "relevance_score": 0.81,
                    "answer_coverage_score": 0.1,
                    "source_confidence_score": 0.1,
                    "freshness_score": 0.1,
                    "score_reason": "high relevance",
                    "score": 0.3,
                }
            ],
        }

        with patch.dict("os.environ", {"WEB_SEARCH_SERPAPI_TOP1_HIGH_SCORE": "0.7"}):
            self.assertEqual(
                web_search_subagent._resolve_serpapi_fetch_url(result),
                "https://example.com/high",
            )

    def test_resolve_fetch_url_vetoes_low_relevance(self) -> None:
        """LLM 要求抓取但 relevance 未超阈值时应否决。"""

        result = {
            "needs_page_fetch": True,
            "fetch_url": "https://example.com/edge",
            "fetch_reason": "想看正文",
            "results": [
                {
                    "title": "Serp Edge",
                    "url_or_path": "https://example.com/edge",
                    "snippet": "edge",
                    "source_name": "serpapi",
                    "relevance_score": 0.7,
                    "answer_coverage_score": 0.9,
                    "source_confidence_score": 0.9,
                    "freshness_score": 0.9,
                    "score_reason": "equal threshold",
                    "score": 0.8,
                }
            ],
        }

        with patch.dict("os.environ", {"WEB_SEARCH_SERPAPI_TOP1_HIGH_SCORE": "0.7"}):
            self.assertIsNone(web_search_subagent._resolve_serpapi_fetch_url(result))

    def test_resolve_fetch_url_respects_llm_false(self) -> None:
        """LLM 明确不抓时，即使高 relevance 也不抓。"""

        result = {
            "needs_page_fetch": False,
            "fetch_url": None,
            "fetch_reason": "snippet 已够",
            "results": [
                {
                    "title": "Serp High",
                    "url_or_path": "https://example.com/high",
                    "snippet": "enough",
                    "source_name": "serpapi",
                    "relevance_score": 0.95,
                    "answer_coverage_score": 0.9,
                    "source_confidence_score": 0.9,
                    "freshness_score": 0.9,
                    "score_reason": "enough",
                    "score": 0.9,
                }
            ],
        }

        with patch.dict("os.environ", {"WEB_SEARCH_SERPAPI_TOP1_HIGH_SCORE": "0.7"}):
            self.assertIsNone(web_search_subagent._resolve_serpapi_fetch_url(result))

    def test_resolve_fetch_url_falls_back_when_fetch_url_invalid(self) -> None:
        """LLM 要求抓取但 fetch_url 非法时，回退到最高 relevance。"""

        result = {
            "needs_page_fetch": True,
            "fetch_url": "https://evil.example/not-in-results",
            "fetch_reason": "缺数字",
            "results": [
                {
                    "title": "Serp Mid",
                    "url_or_path": "https://example.com/mid",
                    "snippet": "mid",
                    "source_name": "serpapi",
                    "relevance_score": 0.75,
                    "answer_coverage_score": 0.2,
                    "source_confidence_score": 0.5,
                    "freshness_score": 0.5,
                    "score_reason": "mid",
                    "score": 0.5,
                },
                {
                    "title": "Serp High",
                    "url_or_path": "https://example.com/high",
                    "snippet": "high",
                    "source_name": "serpapi",
                    "relevance_score": 0.9,
                    "answer_coverage_score": 0.2,
                    "source_confidence_score": 0.5,
                    "freshness_score": 0.5,
                    "score_reason": "high",
                    "score": 0.55,
                },
            ],
        }

        with patch.dict("os.environ", {"WEB_SEARCH_SERPAPI_TOP1_HIGH_SCORE": "0.7"}):
            self.assertEqual(
                web_search_subagent._resolve_serpapi_fetch_url(result),
                "https://example.com/high",
            )

    def test_maybe_fetch_serpapi_page_writes_body(self) -> None:
        """条件满足时应抓取正文并写入 body，保留原 snippet。"""

        tool_outputs = [{"tool_name": "serp_api_search", "ok": True}]
        first_result = {
            "needs_page_fetch": True,
            "fetch_url": "https://example.com/high",
            "fetch_reason": "缺长文细节",
            "results": [
                {
                    "title": "Serp High",
                    "url": "https://example.com/high",
                    "url_or_path": "https://example.com/high",
                    "snippet": "original snippet",
                    "source_name": "serpapi",
                    "relevance_score": 0.9,
                    "answer_coverage_score": 0.1,
                    "source_confidence_score": 0.1,
                    "freshness_score": 0.1,
                    "score_reason": "high",
                    "score": 0.3,
                }
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
            patch.dict("os.environ", {"WEB_SEARCH_SERPAPI_TOP1_HIGH_SCORE": "0.7"}),
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
        serp_item = updated_result["results"][0]
        self.assertEqual(serp_item["title"], "Page Title")
        self.assertEqual(serp_item["snippet"], "original snippet")
        self.assertEqual(serp_item["body"], "完整页面正文内容")

    def test_merge_dedupes_same_url_preferring_body(self) -> None:
        """合并时同 URL 去重，优先保留含 body 的候选。"""

        merged = web_search_subagent._merge_code_materialized_candidates(
            {
                "results": [
                    {
                        "title": "Serp",
                        "url": "https://example.com/same",
                        "url_or_path": "https://example.com/same",
                        "snippet": "short",
                        "source_name": "serpapi",
                        "relevance_score": 0.9,
                        "score": 0.8,
                    }
                ],
                "web_hitl_required": False,
                "hitl_reason": None,
            },
            tavily_candidates=[
                {
                    "title": "Tavily",
                    "url": "https://example.com/same/",
                    "url_or_path": "https://example.com/same/",
                    "snippet": "tavily snippet",
                    "body": "full body text",
                    "source_name": "tavily",
                    "relevance_score": 0.7,
                    "score": 0.7,
                }
            ],
            context7_candidates=[],
            playwright_candidates=[],
        )

        self.assertEqual(len(merged["results"]), 1)
        self.assertEqual(merged["results"][0]["source_name"], "tavily")
        self.assertEqual(merged["results"][0]["body"], "full body text")

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
