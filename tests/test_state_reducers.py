"""State reducer 并发写入测试。"""

from __future__ import annotations

import unittest

from langgraph.graph import END, START, StateGraph

from src.schemas.state import ResearchState
from src.workflow.node_utils import record_node


class StateReducerTest(unittest.TestCase):
    """验证并行节点可以同时追加执行记录。"""

    def test_executed_nodes_supports_parallel_updates(self) -> None:
        """并行节点同时写 executed_nodes 时应通过 reducer 合并。"""

        builder = StateGraph(ResearchState)
        builder.add_node("parallel_a", lambda state: record_node(state, "parallel_a"))
        builder.add_node("parallel_b", lambda state: record_node(state, "parallel_b"))
        builder.add_edge(START, "parallel_a")
        builder.add_edge(START, "parallel_b")
        builder.add_edge(["parallel_a", "parallel_b"], END)
        graph = builder.compile()

        result = graph.invoke({"user_query": "测试并发 executed_nodes"})

        self.assertCountEqual(result["executed_nodes"], ["parallel_a", "parallel_b"])

    def test_evidence_items_last_write_replaces_dict(self) -> None:
        """evidence_items 无 merge reducer：后写整表覆盖，避免迭代残留。"""

        builder = StateGraph(ResearchState)
        builder.add_node(
            "first",
            lambda state: {"evidence_items": {"E1": {"evidence_id": "E1"}}},
        )
        builder.add_node(
            "second",
            lambda state: {"evidence_items": {"E2": {"evidence_id": "E2"}}},
        )
        builder.add_edge(START, "first")
        builder.add_edge("first", "second")
        builder.add_edge("second", END)
        graph = builder.compile()

        result = graph.invoke({"user_query": "测试 evidence_items 整表替换"})

        self.assertEqual(set(result["evidence_items"]), {"E2"})


if __name__ == "__main__":
    unittest.main()
