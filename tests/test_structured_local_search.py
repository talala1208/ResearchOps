"""本地结构化资料搜索测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.tools import structured_data


class StructuredLocalSearchTest(unittest.TestCase):
    """验证 structured_mock 优先使用路由关键词，否则确定性分词兜底。"""

    def test_query_structured_products_uses_routed_plan(self) -> None:
        """任务已带 structured_search_plan 时按路由关键词查询。"""

        task = {
            "task_id": "T1",
            "question_id": "Q1",
            "query": "查询 Cursor 的定位和价格",
            "source_type": "structured_mock",
            "search_provider": "local_document_search",
            "attempt": 1,
            "structured_search_plan": {
                "search_terms": ["Cursor"],
                "sql_search_statement": "SELECT * FROM ai_products WHERE name LIKE '%Cursor%'",
                "reasoning": "路由同轮规划",
            },
        }

        with patch.object(
            structured_data,
            "_fallback_structured_search_plan",
        ) as fallback_mock:
            results = structured_data.query_structured_products_for_task(task)

        fallback_mock.assert_not_called()
        self.assertTrue(results)
        self.assertEqual(results[0]["structured_payload"]["search_terms"], ["Cursor"])
        self.assertEqual(
            results[0]["structured_payload"]["search_reasoning"],
            "路由同轮规划",
        )

    def test_query_structured_products_falls_back_to_query_tokens(self) -> None:
        """直达 structured_mock 无路由计划时，用 query 分词兜底。"""

        task = {
            "task_id": "T1",
            "question_id": "Q1",
            "query": "Cursor 价格",
            "source_type": "structured_mock",
            "search_provider": "local",
            "attempt": 1,
        }

        results = structured_data.query_structured_products_for_task(task)

        self.assertTrue(results)
        self.assertEqual(results[0]["collected_by"], "local_structured_search")
        self.assertEqual(
            results[0]["structured_payload"]["search_terms"],
            ["Cursor", "价格"],
        )
        self.assertIn("分词兜底", results[0]["structured_payload"]["search_reasoning"])

    def test_multiple_rows_have_unique_mock_paths(self) -> None:
        """多条结构化记录必须保留独立地址，避免证据清洗误去重。"""

        task = {
            "task_id": "T_hitl",
            "question_id": "Q1",
            "query": "星云笔记 SSO",
            "source_type": "structured_mock",
            "search_provider": "local_document_search",
            "attempt": 1,
        }

        results = structured_data.query_structured_products_for_task(task)

        self.assertGreaterEqual(len(results), 2)
        paths = [result["url_or_path"] for result in results]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertTrue(all(path.startswith("mock://ai_products/") for path in paths))


if __name__ == "__main__":
    unittest.main()
