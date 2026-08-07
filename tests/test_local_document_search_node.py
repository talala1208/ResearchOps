"""本地资料检索节点测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.llm.structured_outputs import (
    LocalSearchBatchRouteOutput,
    LocalSearchTaskRouteOutput,
    LocalStructuredSearchQueryOutput,
)
from src.workflow import search_nodes


class LocalDocumentSearchNodeTest(unittest.TestCase):
    """验证 local_document_search_tool 同时承载 Markdown 和结构化本地资料检索。"""

    def test_local_document_search_tool_routes_markdown_and_structured_tasks(self) -> None:
        """具体类型任务跳过 LLM 路由，直达 Markdown / 结构化查询。"""

        state = {
            "search_tasks": [
                {
                    "task_id": "T_md",
                    "question_id": "Q1",
                    "query": "LangGraph reducer",
                    "source_type": "local_document",
                    "search_provider": "local_document_search",
                    "attempt": 1,
                },
                {
                    "task_id": "T_sql",
                    "question_id": "Q2",
                    "query": "Cursor",
                    "source_type": "structured_mock",
                    "search_provider": "local_document_search",
                    "attempt": 1,
                },
            ]
        }

        with (
            patch.object(
                search_nodes,
                "route_local_search_tools",
            ) as router_mock,
            patch.object(
                search_nodes,
                "query_local_documents_for_task",
                return_value=[
                    {
                        "result_id": "R_md",
                        "task_id": "T_md",
                        "question_id": "Q1",
                        "source_type": "local_document",
                        "source_name": "local_markdown_documents",
                        "url_or_path": "note.md",
                        "title": "LangGraph",
                        "snippet": "reducer",
                        "published_at": None,
                        "collected_by": "local_document_search",
                        "requires_login": False,
                        "blocked_reason": None,
                    }
                ],
            ) as markdown_mock,
            patch.object(
                search_nodes,
                "query_structured_products_for_task",
                return_value=[
                    {
                        "result_id": "R_sql",
                        "task_id": "T_sql",
                        "question_id": "Q2",
                        "source_type": "structured_mock",
                        "source_name": "local_mock_ai_products",
                        "url_or_path": "ai_products.sqlite",
                        "title": "Cursor",
                        "snippet": "AI IDE",
                        "published_at": None,
                        "collected_by": "local_structured_search",
                        "requires_login": False,
                        "blocked_reason": None,
                    }
                ],
            ) as structured_mock,
        ):
            result = search_nodes.local_document_search_tool(state)

        router_mock.assert_not_called()
        markdown_mock.assert_called_once()
        structured_mock.assert_called_once()
        self.assertEqual(result["executed_nodes"], ["local_document_search_tool"])
        self.assertEqual(len(result["local_document_results"]), 2)
        self.assertEqual(
            {item["source_type"] for item in result["local_document_results"]},
            {"local_document", "structured_mock"},
        )

    def test_local_umbrella_task_uses_router_then_concrete_tool(self) -> None:
        """伞类型 local 先批量路由，再执行具体工具。"""

        state = {
            "search_tasks": [
                {
                    "task_id": "T_local",
                    "question_id": "Q1",
                    "query": "LangSmith tracing docs",
                    "source_type": "local",
                    "search_provider": "local_document_search",
                    "attempt": 1,
                }
            ]
        }
        route = LocalSearchBatchRouteOutput(
            task_routes=[
                LocalSearchTaskRouteOutput(
                    task_id="T_local",
                    tools=["local_rag"],
                    reasoning="LangSmith 文档优先向量库",
                    local_rag_query="LangSmith tracing",
                )
            ]
        )

        with (
            patch.object(
                search_nodes,
                "route_local_search_tools",
                return_value=route,
            ) as router_mock,
            patch.object(
                search_nodes,
                "query_local_rag_for_task",
                return_value=[
                    {
                        "result_id": "R_rag",
                        "task_id": "T_local",
                        "question_id": "Q1",
                        "source_type": "local_rag",
                        "source_name": "local_langsmith_docs_rag",
                        "url_or_path": "https://docs.langchain.com/langsmith/trace",
                        "title": "Tracing",
                        "snippet": "trace",
                        "body": "trace body",
                        "published_at": None,
                        "collected_by": "local_rag_search",
                        "requires_login": False,
                        "blocked_reason": None,
                    }
                ],
            ) as rag_mock,
        ):
            result = search_nodes.local_document_search_tool(state)

        router_mock.assert_called_once()
        rag_mock.assert_called_once()
        called_task = rag_mock.call_args.args[0]
        self.assertEqual(called_task["source_type"], "local_rag")
        self.assertEqual(called_task["query"], "LangSmith tracing")
        self.assertEqual(called_task["local_route"]["tools"], ["local_rag"])
        self.assertEqual(
            result["local_document_results"][0]["source_type"],
            "local_rag",
        )

    def test_multiple_umbrella_tasks_call_router_once(self) -> None:
        """多个伞类型 local 任务只调用一次批量路由。"""

        state = {
            "search_tasks": [
                {
                    "task_id": "T1",
                    "question_id": "Q1",
                    "query": "LangSmith tracing",
                    "source_type": "local",
                    "search_provider": "local_document_search",
                    "attempt": 1,
                },
                {
                    "task_id": "T2",
                    "question_id": "Q2",
                    "query": "Cursor 价格",
                    "source_type": "local",
                    "search_provider": "local_document_search",
                    "attempt": 1,
                },
            ]
        }
        route = LocalSearchBatchRouteOutput(
            task_routes=[
                LocalSearchTaskRouteOutput(
                    task_id="T1",
                    tools=["local_rag"],
                    reasoning="文档",
                    local_rag_query="LangSmith tracing API",
                ),
                LocalSearchTaskRouteOutput(
                    task_id="T2",
                    tools=["structured_mock"],
                    reasoning="产品表",
                    structured_search=LocalStructuredSearchQueryOutput(
                        search_terms=["Cursor"],
                        sql_search_statement=(
                            "SELECT * FROM ai_products WHERE name LIKE '%Cursor%'"
                        ),
                        reasoning="提取产品名",
                    ),
                ),
            ]
        )

        with (
            patch.object(
                search_nodes,
                "route_local_search_tools",
                return_value=route,
            ) as router_mock,
            patch.object(
                search_nodes,
                "query_local_rag_for_task",
                return_value=[
                    {
                        "result_id": "R_rag",
                        "task_id": "T1",
                        "question_id": "Q1",
                        "source_type": "local_rag",
                        "source_name": "local_langsmith_docs_rag",
                        "url_or_path": "https://docs.langchain.com/langsmith/trace",
                        "title": "Tracing",
                        "snippet": "trace",
                        "body": "trace body",
                        "published_at": None,
                        "collected_by": "local_rag_search",
                        "requires_login": False,
                        "blocked_reason": None,
                    }
                ],
            ) as rag_mock,
            patch.object(
                search_nodes,
                "query_structured_products_for_task",
                return_value=[
                    {
                        "result_id": "R_sql",
                        "task_id": "T2",
                        "question_id": "Q2",
                        "source_type": "structured_mock",
                        "source_name": "local_mock_ai_products",
                        "url_or_path": "ai_products.sqlite",
                        "title": "Cursor",
                        "snippet": "AI IDE",
                        "published_at": None,
                        "collected_by": "local_structured_search",
                        "requires_login": False,
                        "blocked_reason": None,
                    }
                ],
            ) as structured_mock,
        ):
            result = search_nodes.local_document_search_tool(state)

        router_mock.assert_called_once()
        called_tasks = router_mock.call_args.args[0]
        self.assertEqual([task["task_id"] for task in called_tasks], ["T1", "T2"])
        rag_mock.assert_called_once()
        self.assertEqual(
            rag_mock.call_args.args[0]["query"], "LangSmith tracing API"
        )
        structured_mock.assert_called_once()
        self.assertEqual(
            structured_mock.call_args.args[0]["structured_search_plan"]["search_terms"],
            ["Cursor"],
        )
        self.assertEqual(
            [item["source_type"] for item in result["local_document_results"]],
            ["local_rag", "structured_mock"],
        )

    def test_local_umbrella_task_runs_multiple_tools_in_parallel(self) -> None:
        """伞类型 local 可路由多个工具并合并结果。"""

        state = {
            "search_tasks": [
                {
                    "task_id": "T_local",
                    "question_id": "Q1",
                    "query": "LangSmith 与 Cursor 对比",
                    "source_type": "local",
                    "search_provider": "local_document_search",
                    "attempt": 1,
                }
            ]
        }
        route = LocalSearchBatchRouteOutput(
            task_routes=[
                LocalSearchTaskRouteOutput(
                    task_id="T_local",
                    tools=["local_rag", "structured_mock"],
                    reasoning="文档与结构化产品资料都相关",
                    local_rag_query="LangSmith docs overview",
                    structured_search=LocalStructuredSearchQueryOutput(
                        search_terms=["Cursor", "LangSmith"],
                        sql_search_statement=(
                            "SELECT * FROM ai_products WHERE name LIKE '%Cursor%'"
                        ),
                        reasoning="提取产品名",
                    ),
                )
            ]
        )

        with (
            patch.object(
                search_nodes,
                "route_local_search_tools",
                return_value=route,
            ) as router_mock,
            patch.object(
                search_nodes,
                "query_local_rag_for_task",
                return_value=[
                    {
                        "result_id": "R_rag",
                        "task_id": "T_local",
                        "question_id": "Q1",
                        "source_type": "local_rag",
                        "source_name": "local_langsmith_docs_rag",
                        "url_or_path": "https://docs.langchain.com/langsmith/trace",
                        "title": "Tracing",
                        "snippet": "trace",
                        "body": "trace body",
                        "published_at": None,
                        "collected_by": "local_rag_search",
                        "requires_login": False,
                        "blocked_reason": None,
                    }
                ],
            ) as rag_mock,
            patch.object(
                search_nodes,
                "query_structured_products_for_task",
                return_value=[
                    {
                        "result_id": "R_sql",
                        "task_id": "T_local",
                        "question_id": "Q1",
                        "source_type": "structured_mock",
                        "source_name": "local_mock_ai_products",
                        "url_or_path": "ai_products.sqlite",
                        "title": "Cursor",
                        "snippet": "AI IDE",
                        "published_at": None,
                        "collected_by": "local_structured_search",
                        "requires_login": False,
                        "blocked_reason": None,
                    }
                ],
            ) as structured_mock,
        ):
            result = search_nodes.local_document_search_tool(state)

        router_mock.assert_called_once()
        rag_mock.assert_called_once()
        structured_mock.assert_called_once()
        self.assertEqual(
            rag_mock.call_args.args[0]["query"], "LangSmith docs overview"
        )
        structured_task = structured_mock.call_args.args[0]
        self.assertEqual(
            structured_task["structured_search_plan"]["search_terms"],
            ["Cursor", "LangSmith"],
        )
        self.assertEqual(
            [item["source_type"] for item in result["local_document_results"]],
            ["local_rag", "structured_mock"],
        )
        self.assertEqual(
            rag_mock.call_args.args[0]["local_route"]["tools"],
            ["local_rag", "structured_mock"],
        )


if __name__ == "__main__":
    unittest.main()
