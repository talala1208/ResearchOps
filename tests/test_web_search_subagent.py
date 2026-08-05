"""Web Search SubAgent 输出解析测试。"""

from __future__ import annotations

import unittest

from src.tools.web_search_subagent import _parse_json_object_from_model_output


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


if __name__ == "__main__":
    unittest.main()
