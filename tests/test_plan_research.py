"""研究规划节点模式路由测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.workflow import planning_nodes


class PlanResearchModeTest(unittest.TestCase):
    """验证 plan_research 根据 planning_mode 选择不同规划路径。"""

    def test_plan_research_uses_initial_planning_by_default(self) -> None:
        """默认走初始规划 Prompt 路径。"""

        with (
            patch.object(
                planning_nodes,
                "_run_initial_planning",
                return_value={"planning_mode": "initial", "search_tasks": []},
            ) as initial_mock,
            patch.object(planning_nodes, "_run_iteration_planning") as iteration_mock,
        ):
            result = planning_nodes.plan_research(
                {"user_query": "研究 Cursor", "executed_nodes": [], "search_steps": 0}
            )

        initial_mock.assert_called_once()
        iteration_mock.assert_not_called()
        self.assertEqual(result["planning_mode"], "initial")
        self.assertEqual(result["search_steps"], 1)
        self.assertEqual(result["executed_nodes"], ["plan_research"])

    def test_plan_research_uses_iteration_planning_when_mode_is_iteration(self) -> None:
        """迭代模式走迭代检索 Prompt 路径。"""

        with (
            patch.object(planning_nodes, "_run_initial_planning") as initial_mock,
            patch.object(
                planning_nodes,
                "_run_iteration_planning",
                return_value={"planning_mode": "iteration", "search_tasks": []},
            ) as iteration_mock,
        ):
            result = planning_nodes.plan_research(
                {"planning_mode": "iteration", "executed_nodes": [], "search_steps": 1}
            )

        initial_mock.assert_not_called()
        iteration_mock.assert_called_once()
        self.assertEqual(result["planning_mode"], "iteration")
        self.assertEqual(result["search_steps"], 2)
        self.assertEqual(result["executed_nodes"], ["plan_research"])


if __name__ == "__main__":
    unittest.main()
