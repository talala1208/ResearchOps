"""Graph 输入 Schema 测试。"""

from __future__ import annotations

import unittest

from src.workflow.graph import graph


class GraphInputSchemaTest(unittest.TestCase):
    """验证 LangGraph Studio 入口只暴露自然语言问题字段。"""

    def test_graph_input_schema_exposes_user_query(self) -> None:
        """入口输入 Schema 包含 user_query 字符串字段。"""

        schema = graph.input_schema.model_json_schema()
        input_schema = schema["$defs"]["ResearchInput"]

        self.assertEqual(input_schema["required"], ["user_query"])
        self.assertEqual(input_schema["properties"]["user_query"]["type"], "string")


if __name__ == "__main__":
    unittest.main()
