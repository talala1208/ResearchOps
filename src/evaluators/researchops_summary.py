"""ResearchOps 实验级 Summary Evaluator。

用途：
- 在 LangSmith experiment 结束后，对整批 runs 做聚合评价。
- 统计报告产出率、证据充足率、Review 通过率、安全通过率、降级率、HITL 未完成率等。

该 evaluator 是规则型 summary evaluator，不调用 LLM。
"""

from __future__ import annotations

import json
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field


class ResearchOpsSummaryResult(BaseModel):
    """ResearchOps 批量运行汇总结果。"""

    total_runs: int = Field(ge=0, description="总运行数量")
    final_report_rate: float = Field(ge=0.0, le=1.0, description="最终报告产出率")
    persist_success_rate: float = Field(ge=0.0, le=1.0, description="产物保存成功率")
    evidence_sufficient_rate: float = Field(ge=0.0, le=1.0, description="证据充足率")
    average_evidence_sufficiency_score: float = Field(ge=0.0, le=1.0, description="平均证据充足分")
    review_pass_rate: float = Field(ge=0.0, le=1.0, description="Review 通过率")
    average_review_score: float = Field(ge=0.0, le=1.0, description="平均 Review 分")
    safety_pass_rate: float = Field(ge=0.0, le=1.0, description="安全审查通过率")
    non_degraded_rate: float = Field(ge=0.0, le=1.0, description="非降级输出比例")
    unresolved_hitl_rate: float = Field(ge=0.0, le=1.0, description="未完成 HITL 比例")
    average_final_report_length: float = Field(ge=0.0, description="平均最终报告长度")
    overall_summary_score: float = Field(ge=0.0, le=1.0, description="实验整体汇总分")
    blocking_issues: list[str] = Field(default_factory=list, description="批量运行中的主要问题")


def _as_dict(value: Any) -> dict[str, Any]:
    """把 JSON 字符串或 dict 统一为 dict。"""

    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw_text": value}
        return parsed if isinstance(parsed, dict) else {"raw_value": parsed}
    return {"raw_value": value}


def _normalize_output(output: dict[str, Any]) -> dict[str, Any]:
    """兼容 LangSmith 不同 target 返回结构。"""

    if not isinstance(output, dict):
        return {"output": output}
    nested_output = output.get("output")
    if isinstance(nested_output, dict):
        return {**output, **nested_output}
    return output


def _float_or_none(value: Any) -> float | None:
    """读取浮点数。"""

    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_value(value: Any) -> bool:
    """把常见 truthy 值转为 bool。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "passed"}
    return bool(value)


def _has_final_report(output: dict[str, Any]) -> bool:
    """判断是否产出最终报告。"""

    final_report = output.get("final_report") or output.get("report_draft")
    return isinstance(final_report, str) and bool(final_report.strip())


def _final_report_length(output: dict[str, Any]) -> int:
    """读取最终报告长度。"""

    final_report = output.get("final_report") or output.get("report_draft") or ""
    return len(str(final_report).strip())


def _persist_success(output: dict[str, Any]) -> bool:
    """判断运行产物是否保存成功。"""

    artifacts = output.get("output_artifacts")
    if isinstance(artifacts, dict) and artifacts:
        return True
    return False


def _review_score(output: dict[str, Any]) -> float | None:
    """读取 Review 分。"""

    review_result = _as_dict(output.get("review_result"))
    return _float_or_none(review_result.get("report_score"))


def _review_passed(output: dict[str, Any]) -> bool:
    """判断 Review 是否通过。"""

    review_result = _as_dict(output.get("review_result"))
    return _bool_value(review_result.get("passed"))


def _safety_passed(output: dict[str, Any]) -> bool:
    """判断 Safety Review 是否通过。"""

    safety_result = _as_dict(output.get("safety_review_result"))
    return _bool_value(safety_result.get("safety_pass"))


def _has_unresolved_hitl(output: dict[str, Any]) -> bool:
    """判断是否存在未完成 HITL。"""

    if _bool_value(output.get("web_hitl_required")) or _bool_value(output.get("hitl_required")):
        return True
    hitl_decisions = []
    for key in ["web_hitl_decisions", "hitl_decisions"]:
        value = output.get(key)
        if isinstance(value, list):
            hitl_decisions.extend(value)
    return any(not _bool_value(_as_dict(item).get("completed")) for item in hitl_decisions)


def evaluate_researchops_summary(outputs: list[dict[str, Any]]) -> ResearchOpsSummaryResult:
    """对一批 ResearchOps outputs 做汇总评价。"""

    normalized_outputs = [_normalize_output(output) for output in outputs]
    total_runs = len(normalized_outputs)
    if total_runs == 0:
        return ResearchOpsSummaryResult(
            total_runs=0,
            final_report_rate=0.0,
            persist_success_rate=0.0,
            evidence_sufficient_rate=0.0,
            average_evidence_sufficiency_score=0.0,
            review_pass_rate=0.0,
            average_review_score=0.0,
            safety_pass_rate=0.0,
            non_degraded_rate=0.0,
            unresolved_hitl_rate=0.0,
            average_final_report_length=0.0,
            overall_summary_score=0.0,
            blocking_issues=["没有可评价的 runs"],
        )

    final_report_rate = mean(1.0 if _has_final_report(output) else 0.0 for output in normalized_outputs)
    persist_success_rate = mean(1.0 if _persist_success(output) else 0.0 for output in normalized_outputs)
    evidence_outputs = [
        output
        for output in normalized_outputs
        if "evidence_sufficient" in output
        or isinstance(output.get("evidence_sufficiency_result"), dict)
    ]

    def _is_evidence_sufficient(output: dict[str, Any]) -> bool:
        if "evidence_sufficient" in output:
            return _bool_value(output["evidence_sufficient"])
        result = output.get("evidence_sufficiency_result")
        if isinstance(result, dict):
            return _bool_value(result.get("sufficient"))
        return False

    evidence_sufficient_rate = (
        mean(1.0 if _is_evidence_sufficient(output) else 0.0 for output in evidence_outputs)
        if evidence_outputs
        else 0.0
    )

    def _evidence_score(output: dict[str, Any]) -> float | None:
        direct = _float_or_none(output.get("evidence_sufficiency_score"))
        if direct is not None:
            return direct
        result = output.get("evidence_sufficiency_result")
        if isinstance(result, dict):
            return _float_or_none(result.get("overall_score"))
        return None

    evidence_scores = [
        score
        for score in (_evidence_score(output) for output in normalized_outputs)
        if score is not None
    ]
    average_evidence_sufficiency_score = mean(evidence_scores) if evidence_scores else 0.0
    review_outputs = [
        output for output in normalized_outputs if "review_result" in output
    ]
    review_pass_rate = (
        mean(1.0 if _review_passed(output) else 0.0 for output in review_outputs)
        if review_outputs
        else 0.0
    )
    review_scores = [score for score in (_review_score(output) for output in normalized_outputs) if score is not None]
    average_review_score = mean(review_scores) if review_scores else 0.0
    safety_outputs = [
        output for output in normalized_outputs if "safety_review_result" in output
    ]
    safety_pass_rate = (
        mean(1.0 if _safety_passed(output) else 0.0 for output in safety_outputs)
        if safety_outputs
        else 0.0
    )
    degradation_outputs = [
        output for output in normalized_outputs if "degraded" in output
    ]
    non_degraded_rate = (
        mean(
            0.0 if _bool_value(output["degraded"]) else 1.0
            for output in degradation_outputs
        )
        if degradation_outputs
        else 0.0
    )
    unresolved_hitl_rate = mean(
        1.0 if _has_unresolved_hitl(output) else 0.0
        for output in normalized_outputs
    )
    average_final_report_length = mean(_final_report_length(output) for output in normalized_outputs)

    overall_summary_score = round(
        final_report_rate * 0.15
        + persist_success_rate * 0.05
        + evidence_sufficient_rate * 0.2
        + average_evidence_sufficiency_score * 0.1
        + review_pass_rate * 0.15
        + average_review_score * 0.1
        + safety_pass_rate * 0.15
        + non_degraded_rate * 0.1
        - unresolved_hitl_rate * 0.1,
        4,
    )
    overall_summary_score = max(0.0, min(overall_summary_score, 1.0))

    blocking_issues = []
    if final_report_rate < 1.0:
        blocking_issues.append(f"存在未产出最终报告的 runs：final_report_rate={final_report_rate:.2f}")
    if evidence_sufficient_rate < 0.8:
        blocking_issues.append(f"证据充足率偏低：evidence_sufficient_rate={evidence_sufficient_rate:.2f}")
    if review_pass_rate < 0.8:
        blocking_issues.append(f"Review 通过率偏低：review_pass_rate={review_pass_rate:.2f}")
    if safety_pass_rate < 1.0:
        blocking_issues.append(f"存在安全审查未通过 runs：safety_pass_rate={safety_pass_rate:.2f}")
    if unresolved_hitl_rate > 0.0:
        blocking_issues.append(f"存在未完成 HITL：unresolved_hitl_rate={unresolved_hitl_rate:.2f}")

    return ResearchOpsSummaryResult(
        total_runs=total_runs,
        final_report_rate=round(final_report_rate, 4),
        persist_success_rate=round(persist_success_rate, 4),
        evidence_sufficient_rate=round(evidence_sufficient_rate, 4),
        average_evidence_sufficiency_score=round(average_evidence_sufficiency_score, 4),
        review_pass_rate=round(review_pass_rate, 4),
        average_review_score=round(average_review_score, 4),
        safety_pass_rate=round(safety_pass_rate, 4),
        non_degraded_rate=round(non_degraded_rate, 4),
        unresolved_hitl_rate=round(unresolved_hitl_rate, 4),
        average_final_report_length=round(average_final_report_length, 2),
        overall_summary_score=overall_summary_score,
        blocking_issues=blocking_issues,
    )


def researchops_summary_evaluator(
    outputs: list[dict],
    reference_outputs: list[dict] | None = None,
    inputs: list[dict] | None = None,
) -> dict:
    """LangSmith summary evaluator。

    LangSmith 会在 experiment 结束后传入：
    - outputs: 所有 run.outputs
    - reference_outputs: 所有 Example.outputs，可选
    - inputs: 所有 Example.inputs，可选
    """

    del reference_outputs, inputs
    result = evaluate_researchops_summary(outputs)
    return {
        "key": "researchops_summary_score",
        "score": result.overall_summary_score,
        "comment": result.model_dump_json(),
    }
