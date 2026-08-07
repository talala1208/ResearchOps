"""本地 RAG 检索工具与节点路由测试。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document

from src.tools import local_rag_search
from src.workflow import search_nodes


class LocalRagSearchToolTest(unittest.TestCase):
    """验证本地 RAG 检索结果规范化。"""

    def tearDown(self) -> None:
        local_rag_search.clear_local_rag_retriever_cache()

    def test_search_local_rag_normalizes_documents(self) -> None:
        """检索结果应写入 title / snippet / body / url。"""

        fake_retriever = MagicMock()
        fake_retriever.invoke.return_value = [
            Document(
                page_content="# Tracing overview - Docs by LangChain\n\nUse traces to debug.",
                metadata={
                    "source": "https://docs.langchain.com/langsmith/trace",
                    "loc": "https://docs.langchain.com/langsmith/trace",
                },
            )
        ]
        fake_retriever.search_kwargs = {"k": 4}

        with patch.object(
            local_rag_search,
            "get_local_rag_retriever",
            return_value=fake_retriever,
        ):
            rows = local_rag_search.search_local_rag("how to trace")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Tracing overview")
        self.assertEqual(
            rows[0]["url_or_path"],
            "https://docs.langchain.com/langsmith/trace",
        )
        self.assertIn("Use traces to debug", rows[0]["body"])
        self.assertEqual(rows[0]["source_name"], "local_langsmith_docs_rag")

    def test_query_local_rag_for_task_shapes_tool_results(self) -> None:
        """SearchTask 结果字段应与本地资料候选契约对齐。"""

        with patch.object(
            local_rag_search,
            "search_local_rag",
            return_value=[
                {
                    "title": "Datasets",
                    "snippet": "Create datasets",
                    "body": "Create datasets for evaluation.",
                    "url_or_path": "https://docs.langchain.com/langsmith/datasets",
                    "source_name": "local_langsmith_docs_rag",
                    "score": None,
                    "metadata": {"source": "https://docs.langchain.com/langsmith/datasets"},
                }
            ],
        ):
            results = local_rag_search.query_local_rag_for_task(
                {
                    "task_id": "T1",
                    "question_id": "Q1",
                    "query": "LangSmith datasets",
                    "source_type": "local_rag",
                    "search_provider": "local_rag",
                    "attempt": 1,
                }
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["result_id"], "R_local_rag_T1_1")
        self.assertEqual(results[0]["source_type"], "local_rag")
        self.assertEqual(results[0]["collected_by"], "local_rag_search")
        self.assertEqual(results[0]["body"], "Create datasets for evaluation.")
        self.assertIsNone(results[0]["blocked_reason"])


class LocalDocumentSearchNodeRagTest(unittest.TestCase):
    """验证 local_document_search_tool 路由 local_rag。"""

    def test_local_document_search_tool_routes_rag_tasks(self) -> None:
        """具体 local_rag 任务应跳过路由并调用 query_local_rag_for_task。"""

        state = {
            "search_tasks": [
                {
                    "task_id": "T_rag",
                    "question_id": "Q1",
                    "query": "LangSmith tracing",
                    "source_type": "local_rag",
                    "search_provider": "local_document_search",
                    "attempt": 1,
                },
                {
                    "task_id": "T_md",
                    "question_id": "Q2",
                    "query": "notes",
                    "source_type": "local_document",
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
                "query_local_rag_for_task",
                return_value=[
                    {
                        "result_id": "R_rag",
                        "task_id": "T_rag",
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
                "query_local_documents_for_task",
                return_value=[
                    {
                        "result_id": "R_md",
                        "task_id": "T_md",
                        "question_id": "Q2",
                        "source_type": "local_document",
                        "source_name": "local_markdown_documents",
                        "url_or_path": "note.md",
                        "title": "Notes",
                        "snippet": "notes",
                        "published_at": None,
                        "collected_by": "local_document_search",
                        "requires_login": False,
                        "blocked_reason": None,
                    }
                ],
            ) as markdown_mock,
        ):
            result = search_nodes.local_document_search_tool(state)

        router_mock.assert_not_called()
        rag_mock.assert_called_once()
        markdown_mock.assert_called_once()
        self.assertEqual(
            {item["source_type"] for item in result["local_document_results"]},
            {"local_rag", "local_document"},
        )


if __name__ == "__main__":
    unittest.main()
