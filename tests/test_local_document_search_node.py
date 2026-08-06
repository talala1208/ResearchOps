"""本地资料检索节点测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.workflow import search_nodes


class LocalDocumentSearchNodeTest(unittest.TestCase):
    """验证 local_document_search_tool 同时承载 Markdown 和结构化本地资料检索。"""

    def test_local_document_search_tool_routes_markdown_and_structured_tasks(self) -> None:
        """local_document 走 Markdown 搜索，structured_mock 走结构化查询。"""

        state = {
            "search_tasks": [
                {
                    "task_id": "T_md",
                    "question_id": "Q1",
                    "query": "LangGraph reducer",
                    "source_type": "local_document",
                    "search_provider": "local",
                    "attempt": 1,
                },
                {
                    "task_id": "T_sql",
                    "question_id": "Q2",
                    "query": "Cursor",
                    "source_type": "structured_mock",
                    "search_provider": "local",
                    "attempt": 1,
                },
            ]
        }

        with (
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
                        "is_placeholder": False,
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
                        "is_placeholder": False,
                        "requires_login": False,
                        "blocked_reason": None,
                    }
                ],
            ) as structured_mock,
        ):
            result = search_nodes.local_document_search_tool(state)

        markdown_mock.assert_called_once()
        structured_mock.assert_called_once()
        self.assertEqual(result["executed_nodes"], ["local_document_search_tool"])
        self.assertEqual(len(result["local_document_results"]), 2)
        self.assertEqual(
            {item["source_type"] for item in result["local_document_results"]},
            {"local_document", "structured_mock"},
        )


if __name__ == "__main__":
    unittest.main()
