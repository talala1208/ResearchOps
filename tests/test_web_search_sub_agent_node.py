"""Web Search SubAgent 节点任务级并发测试。"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from src.workflow import search_nodes


def _sample_task(task_id: str, question_id: str) -> dict:
    """构造测试用 Web 检索任务。"""

    return {
        "task_id": task_id,
        "question_id": question_id,
        "query": f"query-{task_id}",
        "source_type": "blog",
        "search_provider": "web",
        "attempt": 1,
    }


def _sample_subagent_result(
    *,
    title: str,
    hitl_required: bool = False,
    hitl_reason: str | None = None,
) -> dict:
    """构造测试用 SubAgent 返回值。"""

    return {
        "results": [
            {
                "title": title,
                "url_or_path": f"https://example.com/{title}",
                "snippet": f"snippet for {title}",
                "source_name": "serpapi",
                "published_at": None,
                "requires_login": False,
                "blocked_reason": None,
                "relevance_score": 0.8,
                "answer_coverage_score": 0.7,
                "source_confidence_score": 0.6,
                "freshness_score": 0.5,
                "score_reason": "测试打分",
            }
        ],
        "web_hitl_required": hitl_required,
        "hitl_reason": hitl_reason,
        "tool_evaluation_records": [
            {
                "tool_name": "serp_api_search",
                "ok": True,
                "valid_result_count": 1,
                "content_length": 0,
                "failure_type": None,
                "error": None,
            }
        ],
    }


class WebSearchSubAgentNodeConcurrencyTest(unittest.TestCase):
    """验证 web_search_sub_agent 的有界并发与确定性合并。"""

    def test_tasks_run_concurrently_and_preserve_input_order(self) -> None:
        """多个任务应重叠执行，结果仍按输入顺序输出。"""

        state = {
            "search_tasks": [
                _sample_task("T1", "Q1"),
                _sample_task("T2", "Q2"),
                _sample_task("T3", "Q3"),
            ],
            "sub_questions": {
                "Q1": {"question": "问题 1"},
                "Q2": {"question": "问题 2"},
                "Q3": {"question": "问题 3"},
            },
            "expected_evidence": {},
        }
        started = threading.Barrier(3, timeout=2)
        active_lock = threading.Lock()
        max_active = 0
        current_active = 0

        def fake_run(task: dict) -> dict:
            nonlocal max_active, current_active
            with active_lock:
                current_active += 1
                max_active = max(max_active, current_active)
            started.wait()
            time.sleep(0.05)
            with active_lock:
                current_active -= 1
            return _sample_subagent_result(title=task["task_id"])

        with (
            patch.object(search_nodes, "run_web_search_subagent_for_task", side_effect=fake_run),
            patch.dict("os.environ", {"WEB_SEARCH_TASK_CONCURRENCY": "3"}),
        ):
            result = search_nodes.web_search_sub_agent(state)

        self.assertEqual(max_active, 3)
        self.assertEqual(
            [item["task_id"] for item in result["web_search_results"]],
            ["T1", "T2", "T3"],
        )
        self.assertEqual(
            [item["title"] for item in result["web_search_results"]],
            ["T1", "T2", "T3"],
        )

    def test_hitl_reason_uses_first_task_in_input_order(self) -> None:
        """HITL 原因应按输入顺序取第一个非空值。"""

        state = {
            "search_tasks": [
                _sample_task("T1", "Q1"),
                _sample_task("T2", "Q2"),
            ],
            "sub_questions": {},
            "expected_evidence": {},
        }

        def fake_run(task: dict) -> dict:
            if task["task_id"] == "T1":
                time.sleep(0.05)
                return _sample_subagent_result(
                    title="T1",
                    hitl_required=True,
                    hitl_reason="第一个任务需要登录",
                )
            return _sample_subagent_result(
                title="T2",
                hitl_required=True,
                hitl_reason="第二个任务也需要登录",
            )

        with (
            patch.object(search_nodes, "run_web_search_subagent_for_task", side_effect=fake_run),
            patch.dict("os.environ", {"WEB_SEARCH_TASK_CONCURRENCY": "2"}),
        ):
            result = search_nodes.web_search_sub_agent(state)

        self.assertTrue(result["web_hitl_required"])
        self.assertEqual(result["web_hitl_reason"], "第一个任务需要登录")

    def test_task_exception_fails_the_node(self) -> None:
        """任一任务抛错时应使节点整体失败。"""

        state = {
            "search_tasks": [
                _sample_task("T1", "Q1"),
                _sample_task("T2", "Q2"),
            ],
            "sub_questions": {},
            "expected_evidence": {},
        }

        def fake_run(task: dict) -> dict:
            if task["task_id"] == "T2":
                raise ValueError("模拟任务失败")
            return _sample_subagent_result(title="T1")

        with (
            patch.object(search_nodes, "run_web_search_subagent_for_task", side_effect=fake_run),
            patch.dict("os.environ", {"WEB_SEARCH_TASK_CONCURRENCY": "2"}),
        ):
            with self.assertRaises(ValueError):
                search_nodes.web_search_sub_agent(state)


if __name__ == "__main__":
    unittest.main()
