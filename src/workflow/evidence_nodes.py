"""证据治理、HITL、证据充足性与检索预算节点。"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any

from src.config.settings import get_workflow_config
from src.schemas.state import ResearchState
from src.tools.external_agent_workers import call_claude_code_worker, call_codex_worker
from src.workflow.node_utils import record_node


PRIORITY_WEIGHT = {
    "high": 3,
    "medium": 2,
    "low": 1,
}
HIGH_QUALITY_RELIABILITY_THRESHOLD = 0.7
EVIDENCE_SUFFICIENCY_THRESHOLD = 0.75
SOURCE_AUTHORITY_SCORE = {
    "official_docs": 0.95,
    "pricing_page": 0.9,
    "changelog": 0.85,
    "github": 0.8,
    "structured_mock": 0.75,
    "local_document": 0.7,
    "blog": 0.65,
    "product_directory": 0.6,
    "traffic_data": 0.55,
    "community": 0.45,
}


def _strategy_external_worker_enabled() -> bool:
    """判断策略迭代是否启用外部 Agent worker。"""

    return os.getenv("ENABLE_STRATEGY_EXTERNAL_WORKER", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _call_strategy_external_worker(prompt: str) -> dict[str, Any]:
    """调用策略迭代外部 worker。"""

    provider = os.getenv("STRATEGY_EXTERNAL_WORKER", "codex").strip()
    if provider == "claude_code":
        raw_result = call_claude_code_worker.invoke({"prompt": prompt})
    else:
        raw_result = call_codex_worker.invoke({"prompt": prompt})
    return json.loads(raw_result)


def _stable_unique_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 URL / 路径、标题、片段去重并保持原顺序。"""

    seen_keys = set()
    unique_results = []
    for result in results:
        key = (
            result.get("url_or_path", ""),
            result.get("title", ""),
            result.get("snippet", ""),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_results.append(result)
    return unique_results


def _score_or_default(value: Any, default: float) -> float:
    """读取 0 到 1 之间的候选分；缺失或非法时使用默认分。"""

    if isinstance(value, bool) or value is None:
        return default
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(score, 1.0))


def _score_result(result: dict[str, Any]) -> dict[str, float]:
    """用确定性规则为替代结果打分。"""

    source_type = result["source_type"]
    authority_score = _score_or_default(
        result.get("source_confidence_score"),
        SOURCE_AUTHORITY_SCORE.get(source_type, 0.5),
    )
    freshness_score = _score_or_default(
        result.get("freshness_score"),
        0.6 if result.get("published_at") is None else 0.8,
    )
    relevance_score = _score_or_default(
        result.get("relevance_score"),
        0.75 if result.get("snippet") else 0.0,
    )
    answer_coverage_score = _score_or_default(
        result.get("answer_coverage_score"),
        relevance_score,
    )
    reliability_score = round(
        authority_score * 0.35
        + freshness_score * 0.15
        + relevance_score * 0.3
        + answer_coverage_score * 0.2,
        4,
    )

    return {
        "authority_score": authority_score,
        "freshness_score": freshness_score,
        "relevance_score": relevance_score,
        "answer_coverage_score": answer_coverage_score,
        "source_confidence_score": authority_score,
        "reliability_score": reliability_score,
    }


def deduplicate_and_cluster(state: ResearchState) -> dict[str, Any]:
    """对清洗后的候选结果去重并按 `question_id` 聚类。

    读取：`sanitized_results`
    写入：`evidence_clusters`
    """

    sanitized_results = state.get("sanitized_results", [])
    unique_results = _stable_unique_results(sanitized_results)

    clusters_by_question_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in unique_results:
        question_id = result["question_id"]
        clusters_by_question_id[question_id].append(result)

    evidence_clusters = [
        {
            "cluster_id": f"CL_{question_id}",
            "question_id": question_id,
            "candidate_count": len(candidates),
            "candidates": candidates,
        }
        for question_id, candidates in clusters_by_question_id.items()
    ]

    return {
        **record_node(state, "deduplicate_and_cluster"),
        "evidence_clusters": evidence_clusters,
    }


def evaluate_evidence_quality(state: ResearchState) -> dict[str, Any]:
    """把候选结果转换为标准证据，并识别简单冲突。

    当前用确定性规则替代 LLM judge。后续可接入
    `evidence_quality_evaluator.yml`。
    """

    sub_questions = state["sub_questions"]
    evidence_items = {}
    evidence_ids_by_question_id: dict[str, list[str]] = {
        question_id: [] for question_id in sub_questions
    }
    conflicts = {}
    conflict_ids_by_question_id: dict[str, list[str]] = {
        question_id: [] for question_id in sub_questions
    }
    evidence_counter = 1
    conflict_counter = 1
    workflow_config = get_workflow_config()

    for cluster in state.get("evidence_clusters", []):
        question_id = cluster["question_id"]
        if question_id not in sub_questions:
            raise ValueError(f"证据聚类引用了不存在的 question_id：{question_id}")

        source_types_seen: dict[str, list[str]] = defaultdict(list)
        for candidate in cluster["candidates"]:
            evidence_id = f"E{evidence_counter}"
            evidence_counter += 1
            scores = _score_result(candidate)
            evidence_items[evidence_id] = {
                "evidence_id": evidence_id,
                "question_id": question_id,
                "source_type": candidate["source_type"],
                "source_name": candidate["source_name"],
                "url_or_path": candidate["url_or_path"],
                "title": candidate["title"],
                "snippet": candidate["snippet"],
                "published_at": candidate.get("published_at"),
                "collected_by": candidate["collected_by"],
                "authority_score": scores["authority_score"],
                "freshness_score": scores["freshness_score"],
                "relevance_score": scores["relevance_score"],
                "answer_coverage_score": scores["answer_coverage_score"],
                "source_confidence_score": scores["source_confidence_score"],
                "reliability_score": scores["reliability_score"],
                "score_reason": candidate.get("score_reason"),
                "used_in_final_report": False,
            }
            evidence_ids_by_question_id[question_id].append(evidence_id)
            source_types_seen[candidate["source_type"]].append(evidence_id)

        if len(source_types_seen) >= 2:
            conflict_id = f"C{conflict_counter}"
            conflict_counter += 1
            related_evidence_ids = [
                evidence_id
                for evidence_ids in source_types_seen.values()
                for evidence_id in evidence_ids
            ]
            hitl_need_score = 6.0
            conflicts[conflict_id] = {
                "conflict_id": conflict_id,
                "question_id": question_id,
                "evidence_ids": related_evidence_ids,
                "conflict_summary": "替代规则：同一子问题存在多个来源类型，后续真实实现需判断是否语义冲突。",
                "preferred_evidence_id": related_evidence_ids[0] if related_evidence_ids else None,
                "hitl_need_score": hitl_need_score,
                "hitl_triggered": hitl_need_score > workflow_config.hitl_conflict_threshold,
            }
            conflict_ids_by_question_id[question_id].append(conflict_id)

    hitl_required = any(
        conflict["hitl_triggered"] for conflict in conflicts.values()
    )
    entity_index = {
        **state["entity_index"],
        "evidence_ids_by_question_id": evidence_ids_by_question_id,
        "conflict_ids_by_question_id": conflict_ids_by_question_id,
    }

    return {
        **record_node(state, "evaluate_evidence_quality"),
        "evidence_items": evidence_items,
        "conflicts": conflicts,
        "entity_index": entity_index,
        "hitl_decisions": [],
        "hitl_required": hitl_required,
    }


def request_human_review(state: ResearchState) -> dict[str, Any]:
    """动态 HITL 人工确认替代节点。

    当前不真正暂停，只把需要 HITL 的冲突记录为未完成决策。
    """

    decisions = []
    for conflict in state.get("conflicts", {}).values():
        if not conflict["hitl_triggered"]:
            continue
        decisions.append(
            {
                "hitl_type": "evidence_conflict",
                "conflict_id": conflict["conflict_id"],
                "question_id": conflict["question_id"],
                "completed": False,
                "decision": "placeholder_no_user_decision",
                "reason": conflict["conflict_summary"],
            }
        )

    return {
        **record_node(state, "request_human_review"),
        "hitl_decisions": decisions,
    }


def build_evidence_matrix(state: ResearchState) -> dict[str, Any]:
    """按子问题构建证据矩阵。

    读取：`sub_questions`、`evidence_items`、`entity_index`
    写入：`evidence_matrix`
    """

    sub_questions = state["sub_questions"]
    evidence_items = state["evidence_items"]
    evidence_ids_by_question_id = state["entity_index"]["evidence_ids_by_question_id"]
    conflict_ids_by_question_id = state["entity_index"].get(
        "conflict_ids_by_question_id", {}
    )

    evidence_matrix = {}
    used_evidence_ids = []
    for question_id, sub_question in sub_questions.items():
        evidence_ids = evidence_ids_by_question_id.get(question_id, [])
        for evidence_id in evidence_ids:
            if evidence_id not in evidence_items:
                raise ValueError(f"证据矩阵引用了不存在的 evidence_id：{evidence_id}")

        high_quality_evidence_ids = [
            evidence_id
            for evidence_id in evidence_ids
            if evidence_items[evidence_id]["reliability_score"]
            >= HIGH_QUALITY_RELIABILITY_THRESHOLD
        ]
        source_types = sorted(
            {evidence_items[evidence_id]["source_type"] for evidence_id in evidence_ids}
        )
        used_evidence_ids.extend(evidence_ids)

        evidence_matrix[question_id] = {
            "question_id": question_id,
            "question": sub_question["question"],
            "priority": sub_question["priority"],
            "evidence_ids": evidence_ids,
            "high_quality_evidence_ids": high_quality_evidence_ids,
            "covered_source_types": source_types,
            "conflict_ids": conflict_ids_by_question_id.get(question_id, []),
        }

    evidence_items_with_usage = {
        evidence_id: {
            **evidence_item,
            "used_in_final_report": evidence_id in used_evidence_ids,
        }
        for evidence_id, evidence_item in evidence_items.items()
    }
    entity_index = {
        **state["entity_index"],
        "used_evidence_ids": used_evidence_ids,
    }

    return {
        **record_node(state, "build_evidence_matrix"),
        "evidence_items": evidence_items_with_usage,
        "evidence_matrix": evidence_matrix,
        "entity_index": entity_index,
    }


def check_evidence_sufficiency(state: ResearchState) -> dict[str, Any]:
    """按最低证据标准和 Priority 判断整体证据是否充足。"""

    sub_questions = state["sub_questions"]
    minimum_evidence_standard = state["minimum_evidence_standard"]
    evidence_matrix = state["evidence_matrix"]

    question_evidence_status = {}
    insufficient_question_ids = []
    weighted_score_sum = 0.0
    total_weight = 0
    high_priority_all_met = True

    for question_id, sub_question in sub_questions.items():
        if question_id not in minimum_evidence_standard:
            raise ValueError(f"缺少最低证据标准：{question_id}")
        if question_id not in evidence_matrix:
            raise ValueError(f"缺少证据矩阵：{question_id}")

        standard = minimum_evidence_standard[question_id]
        matrix_item = evidence_matrix[question_id]
        priority = sub_question["priority"]
        priority_weight = PRIORITY_WEIGHT[priority]
        required_source_types = standard["must_include_source_types"]
        covered_source_types = matrix_item["covered_source_types"]
        collected_evidence_count = len(matrix_item["evidence_ids"])
        high_quality_evidence_count = len(matrix_item["high_quality_evidence_ids"])

        total_required = max(standard["min_total_evidence"], 1)
        high_quality_required = standard["min_high_quality_sources"]
        count_score = min(collected_evidence_count / total_required, 1.0)
        high_quality_score = (
            1.0
            if high_quality_required == 0
            else min(high_quality_evidence_count / high_quality_required, 1.0)
        )
        source_score = (
            1.0
            if not required_source_types
            else len(set(covered_source_types) & set(required_source_types))
            / len(set(required_source_types))
        )
        weighted_score = round(
            count_score * 0.5 + high_quality_score * 0.3 + source_score * 0.2,
            4,
        )
        minimum_standard_met = (
            collected_evidence_count >= standard["min_total_evidence"]
            and high_quality_evidence_count >= standard["min_high_quality_sources"]
            and set(required_source_types).issubset(set(covered_source_types))
        )

        missing_parts = []
        if collected_evidence_count < standard["min_total_evidence"]:
            missing_parts.append("证据数量不足")
        if high_quality_evidence_count < standard["min_high_quality_sources"]:
            missing_parts.append("高质量证据不足")
        missing_source_types = sorted(set(required_source_types) - set(covered_source_types))
        if missing_source_types:
            missing_parts.append(f"缺少来源类型：{', '.join(missing_source_types)}")

        if not minimum_standard_met:
            insufficient_question_ids.append(question_id)
            if priority == "high":
                high_priority_all_met = False

        question_evidence_status[question_id] = {
            "question_id": question_id,
            "priority": priority,
            "priority_weight": priority_weight,
            "required_evidence_count": standard["min_total_evidence"],
            "collected_evidence_count": collected_evidence_count,
            "high_quality_evidence_count": high_quality_evidence_count,
            "required_source_types": required_source_types,
            "covered_source_types": covered_source_types,
            "minimum_standard_met": minimum_standard_met,
            "weighted_score": weighted_score,
            "missing_reason": "；".join(missing_parts) if missing_parts else None,
        }
        weighted_score_sum += weighted_score * priority_weight
        total_weight += priority_weight

    evidence_sufficiency_score = (
        round(weighted_score_sum / total_weight, 4) if total_weight else 0.0
    )
    evidence_sufficient = evidence_sufficiency_score >= EVIDENCE_SUFFICIENCY_THRESHOLD
    degradation_reason = None
    if not evidence_sufficient:
        degradation_reason = "证据不足：整体加权证据分低于阈值"

    return {
        **record_node(state, "check_evidence_sufficiency"),
        "question_evidence_status": question_evidence_status,
        "evidence_sufficiency_score": evidence_sufficiency_score,
        "evidence_sufficiency_threshold": EVIDENCE_SUFFICIENCY_THRESHOLD,
        "evidence_sufficient": evidence_sufficient,
        "insufficient_question_ids": insufficient_question_ids,
        "evidence_sufficiency_result": {
            "sufficient": evidence_sufficient,
            "overall_score": evidence_sufficiency_score,
            "threshold": EVIDENCE_SUFFICIENCY_THRESHOLD,
            "high_priority_all_met": high_priority_all_met,
            "question_status": question_evidence_status,
            "insufficient_question_ids": insufficient_question_ids,
            "degradation_recommended": not evidence_sufficient,
            "degradation_reason": degradation_reason,
        },
        "degradation_reason": degradation_reason,
    }


def check_step_budget(state: ResearchState) -> dict[str, Any]:
    """检索预算检查节点。

    读取：`search_steps`、`max_search_steps`、`insufficient_question_ids`
    写入：`step_budget_exhausted`、`search_budget_remaining`、`step_budget_reason`
    """

    workflow_config = get_workflow_config()
    search_steps = state.get("search_steps", 0)
    max_search_steps = state.get("max_search_steps", workflow_config.max_search_steps)
    search_budget_remaining = max(max_search_steps - search_steps, 0)
    insufficient_question_ids = state["insufficient_question_ids"]
    step_budget_exhausted = search_budget_remaining <= 0

    step_budget_reason = None
    if step_budget_exhausted:
        step_budget_reason = (
            "检索预算已耗尽，无法继续为证据不足的子问题追加检索："
            + ", ".join(insufficient_question_ids)
        )

    return {
        **record_node(state, "check_step_budget"),
        "step_budget_exhausted": step_budget_exhausted,
        "search_budget_remaining": search_budget_remaining,
        "step_budget_reason": step_budget_reason,
        "degradation_reason": step_budget_reason or state.get("degradation_reason"),
    }


def strategy_iteration(state: ResearchState) -> dict[str, Any]:
    """证据不足时的策略迭代节点。

    该节点不直接生成检索任务，只准备下一次 `plan_research` 所需的
    迭代上下文，明确区分初始规划和策略迭代。
    """

    sub_questions = state["sub_questions"]
    insufficient_question_ids = state["insufficient_question_ids"]
    question_evidence_status = state["question_evidence_status"]
    previous_search_tasks = state.get("search_tasks", [])

    for question_id in insufficient_question_ids:
        if question_id not in sub_questions:
            raise ValueError(f"策略迭代引用了不存在的 question_id：{question_id}")
        if question_id not in question_evidence_status:
            raise ValueError(f"策略迭代缺少子问题证据状态：{question_id}")

    iteration_count = state.get("iteration_count", 0) + 1
    repeated_action_count = dict(state.get("repeated_action_count", {}))
    for question_id in insufficient_question_ids:
        repeated_action_count[question_id] = repeated_action_count.get(question_id, 0) + 1

    search_iteration_context = {
        "iteration_count": iteration_count,
        "dispatch_mode": "iteration",
        "reason": state.get("degradation_reason") or "证据不足，需要补充检索",
        "insufficient_question_ids": insufficient_question_ids,
        "question_evidence_status": {
            question_id: question_evidence_status[question_id]
            for question_id in insufficient_question_ids
        },
        "previous_search_tasks": previous_search_tasks,
        "strategy_suggestions": [
            "为证据不足的 high / medium 子问题优先补充 must_include_source_types",
            "避免重复上一轮完全相同的 query 和 source_type 组合",
        ],
    }
    if _strategy_external_worker_enabled():
        worker_prompt = (
            "你是 ResearchOps 的策略迭代 worker。请只基于下面 JSON，给出下一轮检索策略建议；"
            "不要写最终报告，不要修改文件。\n"
            + json.dumps(search_iteration_context, ensure_ascii=False, indent=2)
        )
        external_worker_result = _call_strategy_external_worker(worker_prompt)
        search_iteration_context["external_worker_result"] = external_worker_result
        if external_worker_result.get("ok") and external_worker_result.get("text"):
            search_iteration_context["strategy_suggestions"].append(
                "外部 worker 建议：" + str(external_worker_result["text"])[:1000]
            )

    return {
        **record_node(state, "strategy_iteration"),
        "iteration_count": iteration_count,
        "repeated_action_count": repeated_action_count,
        "active_question_ids": insufficient_question_ids,
        "planning_mode": "iteration",
        "search_dispatch_mode": "iteration",
        "search_iteration_context": search_iteration_context,
    }
