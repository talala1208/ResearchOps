"""证据冲突 interrupt + resume 测试。"""

from __future__ import annotations

import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.schemas.state import ResearchState
from src.workflow.evidence_nodes import request_human_review
from src.workflow.report_nodes import _build_conflicts_summary


def _evidence(evidence_id: str, title: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "question_id": "Q1",
        "source_type": "official_docs",
        "source_name": "官方文档",
        "url_or_path": f"https://example.com/{evidence_id}",
        "title": title,
        "snippet": f"{title}的摘要",
        "published_at": None,
        "collected_by": "test",
        "freshness_score": 0.9,
        "relevance_score": 0.9,
        "answer_coverage_score": 0.9,
        "source_confidence_score": 0.9,
        "reliability_score": 0.9,
        "score_reason": None,
        "used_in_final_report": False,
    }


def _state() -> dict:
    return {
        "evidence_items": {
            "E1": _evidence("E1", "证据 A"),
            "E2": _evidence("E2", "证据 B"),
        },
        "conflicts": {
            "C1": {
                "conflict_id": "C1",
                "question_id": "Q1",
                "evidence_ids": ["E1", "E2"],
                "conflict_summary": "两条证据结论相反",
                "preferred_evidence_id": "E1",
                "hitl_need_score": 9.0,
                "hitl_triggered": True,
            }
        },
        "hitl_required": True,
        "hitl_decisions": [],
        "executed_nodes": [],
    }


def _graph():
    builder = StateGraph(ResearchState)
    builder.add_node("request_human_review", request_human_review)
    builder.add_edge(START, "request_human_review")
    builder.add_edge("request_human_review", END)
    return builder.compile(checkpointer=InMemorySaver())


class ConflictHitlTest(unittest.TestCase):
    """验证冲突暂停、恢复、决策映射和输入校验。"""

    def test_interrupt_payload_and_resume_prefer_evidence_b(self) -> None:
        graph = _graph()
        config = {"configurable": {"thread_id": "prefer-b"}}

        interrupted = graph.invoke(_state(), config)

        self.assertIn("__interrupt__", interrupted)
        payload = interrupted["__interrupt__"][0].value
        self.assertEqual(payload["conflict_id"], "C1")
        self.assertEqual(
            [item["evidence_id"] for item in payload["evidence"]],
            ["E1", "E2"],
        )
        self.assertEqual(len(payload["options"]), 4)

        resumed = graph.invoke(
            Command(
                resume={
                    "conflict_id": "C1",
                    "decision": "prefer_evidence_b",
                    "reason": "B 的来源更可靠",
                }
            ),
            config,
        )

        self.assertFalse(resumed["hitl_required"])
        self.assertEqual(resumed["conflicts"]["C1"]["preferred_evidence_id"], "E2")
        self.assertTrue(resumed["conflicts"]["C1"]["reviewed_by_human"])
        self.assertTrue(resumed["hitl_decisions"][0]["completed"])

    def test_keep_both_clears_preference(self) -> None:
        graph = _graph()
        config = {"configurable": {"thread_id": "keep-both"}}
        graph.invoke(_state(), config)

        resumed = graph.invoke(
            Command(
                resume={
                    "conflict_id": "C1",
                    "decision": "keep_both_and_disclose",
                }
            ),
            config,
        )

        self.assertIsNone(resumed["conflicts"]["C1"]["preferred_evidence_id"])

    def test_ignore_conflict_keeps_automatic_preference(self) -> None:
        graph = _graph()
        config = {"configurable": {"thread_id": "ignore"}}
        graph.invoke(_state(), config)

        resumed = graph.invoke(
            Command(
                resume={
                    "conflict_id": "C1",
                    "decision": "ignore_conflict",
                }
            ),
            config,
        )

        self.assertEqual(resumed["conflicts"]["C1"]["preferred_evidence_id"], "E1")

    def test_rejects_mismatched_conflict_id(self) -> None:
        graph = _graph()
        config = {"configurable": {"thread_id": "invalid-id"}}
        graph.invoke(_state(), config)

        with self.assertRaisesRegex(ValueError, "conflict_id"):
            graph.invoke(
                Command(
                    resume={
                        "conflict_id": "C999",
                        "decision": "prefer_evidence_a",
                    }
                ),
                config,
            )

    def test_no_triggered_conflict_does_not_interrupt(self) -> None:
        state = _state()
        state["conflicts"]["C1"]["hitl_triggered"] = False

        result = request_human_review(state)

        self.assertFalse(result["hitl_required"])
        self.assertEqual(result["hitl_decisions"], [])

    def test_report_context_contains_human_resolution(self) -> None:
        state = _state()
        state["conflicts"]["C1"].update(
            {
                "resolution": "keep_both_and_disclose",
                "reviewed_by_human": True,
                "resolution_reason": "两个来源时间范围不同",
            }
        )

        summary = _build_conflicts_summary(state)

        self.assertIn("resolution=keep_both_and_disclose", summary)
        self.assertIn("reviewed_by_human=True", summary)
        self.assertIn("两个来源时间范围不同", summary)


if __name__ == "__main__":
    unittest.main()
