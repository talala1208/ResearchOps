"""本地结构化资料搜索测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.llm.structured_outputs import LocalStructuredSearchQueryOutput
from src.tools import structured_data


class StructuredLocalSearchTest(unittest.TestCase):
    """验证 structured_mock 通过本地资料检索 LLM 规划关键词后查询。"""

    def test_query_structured_products_uses_planned_terms(self) -> None:
        """结构化任务应先规划搜索关键词，再执行参数化 SQL 查询。"""

        task = {
            "task_id": "T1",
            "question_id": "Q1",
            "query": "查询 Cursor 的定位和价格",
            "source_type": "structured_mock",
            "search_provider": "local",
            "attempt": 1,
        }
        planned = LocalStructuredSearchQueryOutput(
            search_terms=["Cursor"],
            sql_search_statement="SELECT * FROM ai_products WHERE name LIKE '%Cursor%'",
            reasoning="用户明确查询 Cursor。",
        )

        with patch.object(
            structured_data,
            "_plan_structured_search_query",
            return_value=planned,
        ):
            results = structured_data.query_structured_products_for_task(task)

        self.assertTrue(results)
        self.assertEqual(results[0]["collected_by"], "local_structured_search")
        self.assertEqual(results[0]["structured_payload"]["search_terms"], ["Cursor"])
        self.assertIn(
            "sql_search_statement",
            results[0]["structured_payload"],
        )


if __name__ == "__main__":
    unittest.main()
