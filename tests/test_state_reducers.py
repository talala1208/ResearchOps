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

    def test_evidence_items_supports_parallel_dict_updates(self) -> None:
        """并行节点同时写 evidence_items 时应通过 reducer 合并。"""

        builder = StateGraph(ResearchState)
        builder.add_node("parallel_a", lambda state: {"evidence_items": {"E1": {}}})
        builder.add_node("parallel_b", lambda state: {"evidence_items": {"E2": {}}})
        builder.add_edge(START, "parallel_a")
        builder.add_edge(START, "parallel_b")
        builder.add_edge(["parallel_a", "parallel_b"], END)
        graph = builder.compile()

        result = graph.invoke({"user_query": "测试并发 evidence_items"})

        self.assertEqual(set(result["evidence_items"]), {"E1", "E2"})


if __name__ == "__main__":
    unittest.main()
