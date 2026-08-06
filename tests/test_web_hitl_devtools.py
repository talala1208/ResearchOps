"""Web HITL DevTools 登录确认测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.workflow import search_nodes


class WebHITLDevToolsTest(unittest.TestCase):
    """验证 DevTools 只配合 Web HITL 使用。"""

    def test_web_hitl_request_uses_devtools_for_login_page(self) -> None:
        """requires_login 的结果会进入 DevTools 登录页观察。"""

        state = {
            "web_hitl_reason": "需要登录确认",
            "web_search_results": [
                {
                    "result_id": "R1",
                    "task_id": "T1",
                    "question_id": "Q1",
                    "url_or_path": "https://example.com/login",
                    "requires_login": True,
                    "blocked_reason": "检测到登录页",
                }
            ],
        }
        observation = {
            "ok": True,
            "provider": "devtools_mcp",
            "raw_result": {"inspect_result": "login page"},
        }

        with patch.object(
            search_nodes,
            "_inspect_login_page_with_devtools",
            return_value=observation,
        ) as inspect_mock:
            result = search_nodes.web_search_hitl_request(state)

        inspect_mock.assert_called_once_with("https://example.com/login")
        self.assertTrue(result["web_hitl_required"])
        self.assertEqual(
            result["web_hitl_decisions"][0]["devtools_observation"],
            observation,
        )

    def test_web_hitl_request_skips_non_login_results(self) -> None:
        """普通 Web 结果不会调用 DevTools。"""

        state = {
            "web_search_results": [
                {
                    "result_id": "R1",
                    "task_id": "T1",
                    "question_id": "Q1",
                    "url_or_path": "https://example.com",
                    "requires_login": False,
                }
            ],
        }
        with patch.object(search_nodes, "_inspect_login_page_with_devtools") as inspect_mock:
            result = search_nodes.web_search_hitl_request(state)

        inspect_mock.assert_not_called()
        self.assertEqual(result["web_hitl_decisions"], [])


if __name__ == "__main__":
    unittest.main()
