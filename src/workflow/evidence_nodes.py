"""证据治理、HITL 观测占位、证据充足性与检索预算节点。"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Any

from src.artifacts.evidence_content import (
    extract_candidate_long_text,
    persist_evidence_content,
    shorten_snippet,
)
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
LOCAL_UMBRELLA_SOURCE_TYPE = "local"
LOCAL_CONCRETE_SOURCE_TYPES = frozenset(
    {"local_document", "local_rag", "structured_mock"}
)
SOURCE_AUTHORITY_SCORE = {
    "official_docs": 0.95,
    "pricing_page": 0.9,
    "changelog": 0.85,
    "github": 0.8,
    "structured_mock": 0.75,
    "local_rag": 0.92,
    "local_document": 0.7,
    "blog": 0.65,
    "product_directory": 0.6,
    "traffic_data": 0.55,
    "community": 0.45,
}
CONTEXT7_AUTHORITY_WEIGHT = 0.55
CONTEXT7_FRESHNESS_WEIGHT = 0.1
CONTEXT7_RELEVANCE_WEIGHT = 0.2
CONTEXT7_COVERAGE_WEIGHT = 0.15
CONTEXT7_DEFAULT_AUTHORITY = 0.95
CONTEXT7_DEFAULT_RELEVANCE = 0.5
SEMANTIC_CONFLICT_HITL_NEED_SCORE = 6.0
NUMERIC_CONFLICT_RELATIVE_THRESHOLD = 0.2
NUMERIC_CONFLICT_ABSOLUTE_MIN = 0.01
POLARITY_PAIRS: tuple[tuple[str, str], ...] = (
    ("支持", "不支持"),
    ("可用", "不可用"),
    ("开启", "关闭"),
    ("合规", "违规"),
    ("允许", "禁止"),
    ("通过", "失败"),
    ("上升", "下降"),
    ("增加", "减少"),
    ("available", "unavailable"),
    ("enabled", "disabled"),
    ("support", "unsupported"),
    ("allowed", "forbidden"),
    ("pass", "fail"),
    ("increase", "decrease"),
    ("open", "closed"),
)


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


def _normalize_url_key(value: Any) -> str | None:
    """规范化 URL/路径，供证据去重。"""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.startswith(("http://", "https://")):
        return normalized.rstrip("/")
    return normalized


def _candidate_richness_key(result: dict[str, Any]) -> tuple[Any, ...]:
    """同 URL 去重时保留更完整候选。"""

    body = result.get("body")
    docs = result.get("docs_result")
    snippet = result.get("snippet") or ""
    body_len = len(body) if isinstance(body, str) else 0
    docs_len = len(docs) if isinstance(docs, str) else 0
    snippet_len = len(snippet) if isinstance(snippet, str) else 0
    has_body = 1 if body_len > 0 else 0
    score = _score_or_default(
        result.get("score"),
        _score_or_default(result.get("relevance_score"), 0.0),
    )
    return (has_body, score, body_len + docs_len, snippet_len)


def _stable_unique_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按规范化 URL/路径去重；无 URL 时回退到标题+片段。"""

    best_by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for result in results:
        url_key = _normalize_url_key(result.get("url_or_path") or result.get("url"))
        if url_key is None:
            key = f"__meta__{(result.get('title', ''), result.get('snippet', ''))}"
        else:
            key = url_key
        existing = best_by_key.get(key)
        if existing is None:
            best_by_key[key] = result
            order.append(key)
            continue
        if _candidate_richness_key(result) > _candidate_richness_key(existing):
            best_by_key[key] = result
    return [best_by_key[key] for key in order]


def _score_or_default(value: Any, default: float) -> float:
    """读取 0 到 1 之间的候选分；缺失或非法时使用默认分。"""

    if isinstance(value, bool) or value is None:
        return default
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(score, 1.0))


def _table_authority(bucket: str, source_type: str) -> float:
    """来源权威权重，仅参与 reliability 公式，不写入 EvidenceItem。"""

    if bucket == "context7":
        return CONTEXT7_DEFAULT_AUTHORITY
    return SOURCE_AUTHORITY_SCORE.get(source_type, 0.5)


def _score_bucket(result: dict[str, Any]) -> str:
    """按来源分桶，避免跨工具把不可比分数混排。"""

    explicit = result.get("score_bucket")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    source_name = str(result.get("source_name") or "").lower()
    if "context7" in source_name:
        return "context7"
    if "tavily" in source_name:
        return "tavily"
    if (
        source_name in {"ydc", "you_com", "youcom"}
        or "you_com" in source_name
    ):
        return "ydc"
    if "serp" in source_name:
        return "serp"
    if "playwright" in source_name:
        return "playwright"
    return "other"


def _score_result(result: dict[str, Any]) -> dict[str, float | str]:
    """按来源分桶为候选打分。"""

    bucket = _score_bucket(result)
    source_type = result["source_type"]
    authority_weight = _table_authority(bucket, source_type)
    source_confidence_score = _score_or_default(
        result.get("source_confidence_score"),
        authority_weight,
    )
    freshness_score = _score_or_default(
        result.get("freshness_score"),
        0.6 if result.get("published_at") is None else 0.8,
    )

    if bucket == "context7":
        relevance_score = _score_or_default(
            result.get("relevance_score"),
            CONTEXT7_DEFAULT_RELEVANCE,
        )
        answer_coverage_score = _score_or_default(
            result.get("answer_coverage_score"),
            relevance_score,
        )
        reliability_score = round(
            authority_weight * 0.35
            + source_confidence_score * 0.2
            + freshness_score * CONTEXT7_FRESHNESS_WEIGHT
            + relevance_score * CONTEXT7_RELEVANCE_WEIGHT
            + answer_coverage_score * CONTEXT7_COVERAGE_WEIGHT,
            4,
        )
    elif bucket == "tavily":
        provider_score = _score_or_default(
            result.get("tavily_score"),
            _score_or_default(result.get("score"), 0.5),
        )
        relevance_score = _score_or_default(
            result.get("relevance_score"),
            provider_score,
        )
        answer_coverage_score = _score_or_default(
            result.get("answer_coverage_score"),
            provider_score,
        )
        reliability_score = round(
            authority_weight * 0.1
            + source_confidence_score * 0.15
            + freshness_score * 0.15
            + relevance_score * 0.35
            + answer_coverage_score * 0.25,
            4,
        )
    elif bucket in {"serp", "ydc", "serpapi", "playwright"}:
        relevance_score = _score_or_default(
            result.get("relevance_score"),
            0.75 if result.get("snippet") or result.get("body") else 0.0,
        )
        answer_coverage_score = _score_or_default(
            result.get("answer_coverage_score"),
            relevance_score,
        )
        body_boost = 0.05 if isinstance(result.get("body"), str) and result["body"].strip() else 0.0
        reliability_score = round(
            min(
                1.0,
                authority_weight * 0.2
                + source_confidence_score * 0.15
                + freshness_score * 0.15
                + relevance_score * 0.3
                + answer_coverage_score * 0.2
                + body_boost,
            ),
            4,
        )
    else:
        relevance_score = _score_or_default(
            result.get("relevance_score"),
            0.75 if result.get("snippet") else 0.0,
        )
        answer_coverage_score = _score_or_default(
            result.get("answer_coverage_score"),
            relevance_score,
        )
        reliability_score = round(
            authority_weight * 0.2
            + source_confidence_score * 0.15
            + freshness_score * 0.15
            + relevance_score * 0.3
            + answer_coverage_score * 0.2,
            4,
        )

    return {
        "freshness_score": freshness_score,
        "relevance_score": relevance_score,
        "answer_coverage_score": answer_coverage_score,
        "source_confidence_score": source_confidence_score,
        "reliability_score": reliability_score,
        "score_bucket": bucket,
    }


def _is_admissible_candidate(result: dict[str, Any]) -> bool:
    """判断候选是否可进入证据目录。"""

    if result.get("requires_login") is True:
        return False
    blocked_reason = result.get("blocked_reason")
    if isinstance(blocked_reason, str) and blocked_reason.strip():
        return False
    url_or_path = result.get("url_or_path") or result.get("url")
    if isinstance(url_or_path, str) and url_or_path.strip().startswith("placeholder://"):
        return False
    structured_payload = result.get("structured_payload")
    if isinstance(structured_payload, dict) and structured_payload.get(
        "matched_product_count"
    ) == 0:
        return False
    return True


def _evidence_conflict_text(item: dict[str, Any]) -> str:
    """冲突检测用文本：标题 + 摘要。"""

    title = item.get("title") or ""
    snippet = item.get("snippet") or ""
    return f"{title}\n{snippet}".strip().lower()


def _contains_polarity_term(text: str, term: str, opposing: str) -> bool:
    """判断文本是否包含极性词；若对立词更长且已命中，则从残文再判。"""

    if term not in text:
        return False
    if opposing in text and len(opposing) > len(term):
        residual = text.replace(opposing, " ")
        return term in residual
    return True


def _has_polarity_conflict(text_a: str, text_b: str) -> bool:
    """两条文本是否分别命中同一对立词对的两侧。"""

    for left, right in POLARITY_PAIRS:
        left_l = left.lower()
        right_l = right.lower()
        a_left = _contains_polarity_term(text_a, left_l, right_l)
        a_right = _contains_polarity_term(text_a, right_l, left_l)
        b_left = _contains_polarity_term(text_b, left_l, right_l)
        b_right = _contains_polarity_term(text_b, right_l, left_l)
        if (a_left and b_right) or (a_right and b_left):
            return True
    return False


def _extract_anchored_numbers(text: str) -> list[tuple[str, float]]:
    """抽取「邻近关键词 + 数值」对，用于同锚点数字矛盾。"""

    pattern = re.compile(
        r"([A-Za-z\u4e00-\u9fff]{1,12})\s*[:=：]?\s*"
        r"(-?\d+(?:\.\d+)?)\s*%?",
        re.UNICODE,
    )
    anchored: list[tuple[str, float]] = []
    for match in pattern.finditer(text):
        anchor = match.group(1).strip().lower()
        if not anchor:
            continue
        try:
            value = float(match.group(2))
        except ValueError:
            continue
        anchored.append((anchor, value))
    return anchored


def _has_numeric_conflict(text_a: str, text_b: str) -> bool:
    """同锚点数值相对差异过大则视为冲突。"""

    numbers_a = _extract_anchored_numbers(text_a)
    numbers_b = _extract_anchored_numbers(text_b)
    if not numbers_a or not numbers_b:
        return False
    for anchor_a, value_a in numbers_a:
        for anchor_b, value_b in numbers_b:
            if anchor_a != anchor_b:
                continue
            denom = max(abs(value_a), abs(value_b), NUMERIC_CONFLICT_ABSOLUTE_MIN)
            relative = abs(value_a - value_b) / denom
            if (
                relative >= NUMERIC_CONFLICT_RELATIVE_THRESHOLD
                and abs(value_a - value_b) >= NUMERIC_CONFLICT_ABSOLUTE_MIN
            ):
                return True
    return False


def _detect_semantic_conflicts(
    question_id: str,
    evidence_items: dict[str, dict[str, Any]],
    evidence_ids: list[str],
    *,
    conflict_counter_start: int,
    hitl_conflict_threshold: float,
) -> tuple[dict[str, dict[str, Any]], int]:
    """对同题证据做极简语义冲突检测；返回新增冲突与下一计数。"""

    conflicts: dict[str, dict[str, Any]] = {}
    conflict_counter = conflict_counter_start
    if len(evidence_ids) < 2:
        return conflicts, conflict_counter

    texts = {
        evidence_id: _evidence_conflict_text(evidence_items[evidence_id])
        for evidence_id in evidence_ids
    }
    emitted_pairs: set[frozenset[str]] = set()

    for index, left_id in enumerate(evidence_ids):
        for right_id in evidence_ids[index + 1 :]:
            pair_key = frozenset({left_id, right_id})
            if pair_key in emitted_pairs:
                continue
            left_text = texts[left_id]
            right_text = texts[right_id]
            reasons: list[str] = []
            if _has_polarity_conflict(left_text, right_text):
                reasons.append("对立极性词命中")
            if _has_numeric_conflict(left_text, right_text):
                reasons.append("同锚点数值矛盾")
            if not reasons:
                continue
            emitted_pairs.add(pair_key)
            related = [left_id, right_id]
            preferred = max(
                related,
                key=lambda eid: float(evidence_items[eid]["reliability_score"]),
            )
            conflict_id = f"C{conflict_counter}"
            conflict_counter += 1
            hitl_need_score = SEMANTIC_CONFLICT_HITL_NEED_SCORE
            conflicts[conflict_id] = {
                "conflict_id": conflict_id,
                "question_id": question_id,
                "evidence_ids": related,
                "conflict_summary": (
                    f"极简语义冲突（{'、'.join(reasons)}）："
                    f"{left_id} 与 {right_id}"
                ),
                "preferred_evidence_id": preferred,
                "hitl_need_score": hitl_need_score,
                "hitl_triggered": hitl_need_score > hitl_conflict_threshold,
            }

    return conflicts, conflict_counter


def sanitize_and_cluster(state: ResearchState) -> dict[str, Any]:
    """合并 Web 与本地检索结果，准入过滤、标记不可信、去重并按 `question_id` 聚类。

    读取：`web_search_results`、`local_document_results`
    写入：`evidence_clusters`、`discarded_candidate_count`，并清空两类上游结果
    """

    raw_candidates: list[dict[str, Any]] = []
    discarded_candidate_count = 0
    for result in (
        *state.get("web_search_results", []),
        *state.get("local_document_results", []),
    ):
        if not _is_admissible_candidate(result):
            discarded_candidate_count += 1
            continue
        raw_candidates.append(
            {
                **result,
                "trust_boundary": "untrusted_tool_output",
                "sanitized": True,
            }
        )

    unique_results = _stable_unique_results(raw_candidates)

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
        **record_node(state, "sanitize_and_cluster"),
        "evidence_clusters": evidence_clusters,
        "discarded_candidate_count": discarded_candidate_count,
        "web_search_results": [],
        "local_document_results": [],
    }


def evaluate_evidence_quality(state: ResearchState) -> dict[str, Any]:
    """把候选结果转换为标准证据，并识别极简语义冲突。

    `evidence_items` / `conflicts` 整表替换本轮结果。
    """

    sub_questions = state["sub_questions"]
    evidence_items = {}
    evidence_ids_by_question_id: dict[str, list[str]] = {
        question_id: [] for question_id in sub_questions
    }
    conflicts: dict[str, dict[str, Any]] = {}
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

        for candidate in cluster["candidates"]:
            evidence_id = f"E{evidence_counter}"
            evidence_counter += 1
            scores = _score_result(candidate)
            evidence_item: dict[str, Any] = {
                "evidence_id": evidence_id,
                "question_id": question_id,
                "source_type": candidate["source_type"],
                "source_name": candidate["source_name"],
                "url_or_path": candidate["url_or_path"],
                "title": candidate["title"],
                "published_at": candidate.get("published_at"),
                "collected_by": candidate["collected_by"],
                "freshness_score": scores["freshness_score"],
                "relevance_score": scores["relevance_score"],
                "answer_coverage_score": scores["answer_coverage_score"],
                "source_confidence_score": scores["source_confidence_score"],
                "reliability_score": scores["reliability_score"],
                "score_reason": candidate.get("score_reason"),
                "used_in_final_report": False,
            }
            long_text = extract_candidate_long_text(candidate)
            existing_snippet = candidate.get("snippet") or ""
            if long_text:
                evidence_item["content_path"] = persist_evidence_content(
                    evidence_id,
                    long_text,
                )
                evidence_item["snippet"] = shorten_snippet(
                    existing_snippet or long_text
                )
            else:
                evidence_item["snippet"] = shorten_snippet(existing_snippet)
            library_id = candidate.get("library_id")
            if isinstance(library_id, str) and library_id.strip():
                evidence_item["library_id"] = library_id
            if scores.get("score_bucket"):
                evidence_item["score_bucket"] = scores["score_bucket"]
            scored_by = candidate.get("scored_by")
            if isinstance(scored_by, str) and scored_by.strip():
                evidence_item["scored_by"] = scored_by
            evidence_items[evidence_id] = evidence_item
            evidence_ids_by_question_id[question_id].append(evidence_id)

        question_conflicts, conflict_counter = _detect_semantic_conflicts(
            question_id,
            evidence_items,
            evidence_ids_by_question_id[question_id],
            conflict_counter_start=conflict_counter,
            hitl_conflict_threshold=workflow_config.hitl_conflict_threshold,
        )
        conflicts.update(question_conflicts)
        conflict_ids_by_question_id[question_id].extend(question_conflicts.keys())

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
        "evidence_clusters": [],
    }


def request_human_review(state: ResearchState) -> dict[str, Any]:
    """冲突 HITL 观测占位节点。

    不真正暂停，不改写证据，不因未完成而降级；仅记录观测事实。
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
                "decision": "observation_placeholder_no_user_decision",
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
    不在此标记 `used_in_final_report`（由报告生成按实际引用写入）。
    """

    sub_questions = state["sub_questions"]
    evidence_items = state["evidence_items"]
    evidence_ids_by_question_id = state["entity_index"]["evidence_ids_by_question_id"]
    conflict_ids_by_question_id = state["entity_index"].get(
        "conflict_ids_by_question_id", {}
    )

    evidence_matrix = {}
    for question_id in sub_questions:
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

        evidence_matrix[question_id] = {
            "question_id": question_id,
            "evidence_ids": evidence_ids,
            "high_quality_evidence_ids": high_quality_evidence_ids,
            "covered_source_types": source_types,
            "conflict_ids": conflict_ids_by_question_id.get(question_id, []),
        }

    return {
        **record_node(state, "build_evidence_matrix"),
        "evidence_matrix": evidence_matrix,
    }


def _source_type_covered(
    required_source_type: str,
    covered_source_types: list[str] | set[str],
) -> bool:
    """判断 required 来源是否被 covered 满足；伞类型 local 可由任一具体本地类型覆盖。"""

    covered = set(covered_source_types)
    if required_source_type == LOCAL_UMBRELLA_SOURCE_TYPE:
        return bool(covered & LOCAL_CONCRETE_SOURCE_TYPES)
    return required_source_type in covered


def _missing_required_source_types(
    required_source_types: list[str],
    covered_source_types: list[str],
) -> list[str]:
    """返回尚未被覆盖的 required 来源类型。"""

    return sorted(
        source_type
        for source_type in set(required_source_types)
        if not _source_type_covered(source_type, covered_source_types)
    )


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
        required_unique = list(dict.fromkeys(required_source_types))
        covered_required_count = sum(
            1
            for source_type in required_unique
            if _source_type_covered(source_type, covered_source_types)
        )
        source_score = (
            1.0
            if not required_unique
            else covered_required_count / len(required_unique)
        )
        weighted_score = round(
            count_score * 0.5 + high_quality_score * 0.3 + source_score * 0.2,
            4,
        )
        missing_source_types = _missing_required_source_types(
            required_source_types,
            covered_source_types,
        )
        minimum_standard_met = (
            collected_evidence_count >= standard["min_total_evidence"]
            and high_quality_evidence_count >= standard["min_high_quality_sources"]
            and not missing_source_types
        )

        missing_parts = []
        if collected_evidence_count < standard["min_total_evidence"]:
            missing_parts.append("证据数量不足")
        if high_quality_evidence_count < standard["min_high_quality_sources"]:
            missing_parts.append("高质量证据不足")
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

    读取：`search_steps`、`max_search_steps`、`evidence_sufficiency_result`
    写入：`step_budget_exhausted`、`search_budget_remaining`、`step_budget_reason`
    """

    workflow_config = get_workflow_config()
    search_steps = state.get("search_steps", 0)
    max_search_steps = state.get("max_search_steps", workflow_config.max_search_steps)
    search_budget_remaining = max(max_search_steps - search_steps, 0)
    insufficient_question_ids = state["evidence_sufficiency_result"][
        "insufficient_question_ids"
    ]
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
    sufficiency = state["evidence_sufficiency_result"]
    insufficient_question_ids = sufficiency["insufficient_question_ids"]
    question_evidence_status = sufficiency["question_status"]
    previous_search_tasks = [
        {
            "task_id": task["task_id"],
            "query": task["query"],
            "source_type": task["source_type"],
        }
        for task in state.get("search_tasks", [])
    ]

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
