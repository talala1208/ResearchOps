"""本地检索工具路由测试。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.llm.structured_outputs import (
    LocalSearchBatchRouteOutput,
    LocalSearchTaskRouteOutput,
    LocalStructuredSearchQueryOutput,
)
from src.tools import local_search_router


class LocalSearchRouterTest(unittest.TestCase):
    """验证本地工具批量路由调用 structured output。"""

    def test_route_local_search_tools_returns_batch_choice(self) -> None:
        """批量路由应一次 invoke，并规范化多任务工具与入参。"""

        expected = LocalSearchBatchRouteOutput(
            task_routes=[
                LocalSearchTaskRouteOutput(
                    task_id="T1",
                    tools=["structured_mock", "local_rag", "structured_mock"],
                    reasoning="查询产品价格与竞品，并补充文档",
                    local_rag_query="Cursor Copilot pricing docs",
                    local_document_query=None,
                    structured_search=LocalStructuredSearchQueryOutput(
                        search_terms=["Cursor", "Copilot"],
                        sql_search_statement=(
                            "SELECT * FROM ai_products WHERE name LIKE '%Cursor%'"
                        ),
                        reasoning="提取产品名",
                    ),
                ),
                LocalSearchTaskRouteOutput(
                    task_id="T2",
                    tools=["local_document"],
                    reasoning="本地笔记",
                    local_rag_query=None,
                    local_document_query="LangGraph reducer notes",
                    structured_search=None,
                ),
            ]
        )
        fake_model = MagicMock()
        fake_model.invoke.return_value = expected
        fake_chat = MagicMock()
        fake_chat.with_structured_output.return_value = fake_model

        with (
            patch.object(
                local_search_router,
                "load_prompt",
                return_value={
                    "system_prompt": "system json",
                    "user_prompt_template": "local_tasks:\n{local_tasks_json}",
                },
            ),
            patch.object(
                local_search_router,
                "build_chat_model",
                return_value=fake_chat,
            ),
        ):
            result = local_search_router.route_local_search_tools(
                [
                    {
                        "task_id": "T1",
                        "question_id": "Q1",
                        "query": "比较 Cursor 和 Copilot 价格",
                        "source_type": "local",
                        "search_provider": "local_document_search",
                        "attempt": 1,
                    },
                    {
                        "task_id": "T2",
                        "question_id": "Q2",
                        "query": "LangGraph reducer",
                        "source_type": "local",
                        "search_provider": "local_document_search",
                        "attempt": 1,
                    },
                ]
            )

        self.assertEqual(len(result.task_routes), 2)
        self.assertEqual(result.task_routes[0].tools, ["structured_mock", "local_rag"])
        self.assertEqual(
            result.task_routes[0].local_rag_query, "Cursor Copilot pricing docs"
        )
        self.assertIsNotNone(result.task_routes[0].structured_search)
        self.assertEqual(result.task_routes[1].tools, ["local_document"])
        self.assertEqual(
            result.task_routes[1].local_document_query, "LangGraph reducer notes"
        )
        fake_chat.with_structured_output.assert_called_once_with(
            LocalSearchBatchRouteOutput
        )
        fake_model.invoke.assert_called_once()

    def test_route_rejects_missing_task(self) -> None:
        """缺少输入 task_id 时应失败。"""

        expected = LocalSearchBatchRouteOutput(
            task_routes=[
                LocalSearchTaskRouteOutput(
                    task_id="T1",
                    tools=["local_rag"],
                    reasoning="文档",
                    local_rag_query="tracing",
                )
            ]
        )
        fake_model = MagicMock()
        fake_model.invoke.return_value = expected
        fake_chat = MagicMock()
        fake_chat.with_structured_output.return_value = fake_model

        with (
            patch.object(
                local_search_router,
                "load_prompt",
                return_value={
                    "system_prompt": "system json",
                    "user_prompt_template": "local_tasks:\n{local_tasks_json}",
                },
            ),
            patch.object(
                local_search_router,
                "build_chat_model",
                return_value=fake_chat,
            ),
        ):
            with self.assertRaises(ValueError) as ctx:
                local_search_router.route_local_search_tools(
                    [
                        {
                            "task_id": "T1",
                            "question_id": "Q1",
                            "query": "a",
                            "source_type": "local",
                            "search_provider": "local_document_search",
                            "attempt": 1,
                        },
                        {
                            "task_id": "T2",
                            "question_id": "Q2",
                            "query": "b",
                            "source_type": "local",
                            "search_provider": "local_document_search",
                            "attempt": 1,
                        },
                    ]
                )
        self.assertIn("T2", str(ctx.exception))

    def test_route_rejects_extra_task(self) -> None:
        """返回未知 task_id 时应失败。"""

        expected = LocalSearchBatchRouteOutput(
            task_routes=[
                LocalSearchTaskRouteOutput(
                    task_id="T1",
                    tools=["local_rag"],
                    reasoning="文档",
                    local_rag_query="tracing",
                ),
                LocalSearchTaskRouteOutput(
                    task_id="TX",
                    tools=["local_document"],
                    reasoning="笔记",
                    local_document_query="notes",
                ),
            ]
        )
        fake_model = MagicMock()
        fake_model.invoke.return_value = expected
        fake_chat = MagicMock()
        fake_chat.with_structured_output.return_value = fake_model

        with (
            patch.object(
                local_search_router,
                "load_prompt",
                return_value={
                    "system_prompt": "system json",
                    "user_prompt_template": "local_tasks:\n{local_tasks_json}",
                },
            ),
            patch.object(
                local_search_router,
                "build_chat_model",
                return_value=fake_chat,
            ),
        ):
            with self.assertRaises(ValueError) as ctx:
                local_search_router.route_local_search_tools(
                    [
                        {
                            "task_id": "T1",
                            "question_id": "Q1",
                            "query": "tracing",
                            "source_type": "local",
                            "search_provider": "local_document_search",
                            "attempt": 1,
                        }
                    ]
                )
        self.assertIn("TX", str(ctx.exception))

    def test_route_rejects_missing_tool_query(self) -> None:
        """选中工具但未给对应入参时应失败。"""

        expected = LocalSearchBatchRouteOutput(
            task_routes=[
                LocalSearchTaskRouteOutput(
                    task_id="T1",
                    tools=["structured_mock"],
                    reasoning="需要结构化数据",
                    structured_search=None,
                )
            ]
        )
        fake_model = MagicMock()
        fake_model.invoke.return_value = expected
        fake_chat = MagicMock()
        fake_chat.with_structured_output.return_value = fake_model

        with (
            patch.object(
                local_search_router,
                "load_prompt",
                return_value={
                    "system_prompt": "system json",
                    "user_prompt_template": "local_tasks:\n{local_tasks_json}",
                },
            ),
            patch.object(
                local_search_router,
                "build_chat_model",
                return_value=fake_chat,
            ),
        ):
            with self.assertRaises(ValueError):
                local_search_router.route_local_search_tools(
                    [
                        {
                            "task_id": "T1",
                            "question_id": "Q1",
                            "query": "Cursor 价格",
                            "source_type": "local",
                            "search_provider": "local_document_search",
                            "attempt": 1,
                        }
                    ]
                )


if __name__ == "__main__":
    unittest.main()
