"""外部 Agent Worker 稳定性 LangSmith evaluator。

评价目标：
- Claude Code / Codex worker 是否被正确调用。
- worker 是否启用、是否返回可用文本、是否发生超时 / 缺依赖 / 权限 / 命令缺失 / 空响应等问题。

该 evaluator 是规则型 evaluator，不调用 LLM。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field


WorkerFailureType = Literal[
    "none",
    "no_attempt",
    "disabled",
    "missing_command",
    "missing_dependency",
    "permission_denied",
    "timeout",
    "empty_response",
    "runtime_error",
    "unknown",
]


class WorkerValidity(BaseModel):
    """单个外部 worker 稳定性。"""

    worker: Literal["claude_code", "codex"]
    attempted: bool = Field(description="是否有调用记录")
    enabled: bool = Field(description="worker 是否启用")
    ok: bool = Field(description="worker 是否稳定返回可用文本")
    failure_type: WorkerFailureType = Field(description="主要失败类型")
    text_length: int = Field(ge=0, description="返回文本长度")
    session_id_present: bool = Field(description="是否返回 session_id")
    reason: str = Field(description="简短判断理由")


class ExternalAgentWorkerStabilityResult(BaseModel):
    """外部 Agent Worker 稳定性评估结果。"""

    claude_code: WorkerValidity
    codex: WorkerValidity
    overall_stability_score: float = Field(ge=0.0, le=1.0, description="整体稳定性分数")
    blocking_issues: list[str] = Field(default_factory=list, description="阻塞问题列表")


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


def _as_list(value: Any) -> list[Any]:
    """把常见结果容器统一为 list。"""

    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _text_of(value: Any) -> str:
    """提取规则判断文本。"""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _extract_worker_outputs(outputs: dict[str, Any]) -> list[dict[str, Any]]:
    """从 Run.outputs 中提取 external worker 调用结果。"""

    candidates: list[Any] = []
    direct_keys = [
        "external_worker_outputs",
        "worker_outputs",
        "agent_worker_outputs",
        "tool_outputs",
    ]
    for key in direct_keys:
        if key in outputs:
            candidates.extend(_as_list(outputs[key]))

    output = outputs.get("output")
    if isinstance(output, dict):
        for key in direct_keys:
            if key in output:
                candidates.extend(_as_list(output[key]))

    search_iteration_context = outputs.get("search_iteration_context")
    if isinstance(search_iteration_context, dict):
        external_worker_result = search_iteration_context.get("external_worker_result")
        if external_worker_result is not None:
            candidates.append(external_worker_result)

    extracted = []
    for candidate in candidates:
        item = _as_dict(candidate)
        tool_name = str(item.get("tool_name") or item.get("name") or "")
        if tool_name in {"call_claude_code_worker", "call_codex_worker"}:
            item = _as_dict(item.get("output"))
        if item.get("worker") in {"claude_code", "codex"}:
            extracted.append(item)
    return extracted


def _classify_failure(worker_output: dict[str, Any]) -> WorkerFailureType:
    """识别外部 worker 失败类型。"""

    if worker_output.get("enabled") is False:
        return "disabled"

    error_text = _text_of(worker_output.get("error"))
    text = " ".join([error_text, _text_of(worker_output.get("text"))])
    lowered = text.lower()

    if "超时" in lowered or "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "找不到" in lowered or "not found" in lowered or "no such file" in lowered:
        return "missing_command"
    if "缺少" in lowered or "no module named" in lowered or "importerror" in lowered:
        return "missing_dependency"
    if "permission" in lowered or "权限" in lowered or "denied" in lowered:
        return "permission_denied"
    if worker_output.get("ok") and not _text_of(worker_output.get("text")).strip():
        return "empty_response"
    if error_text:
        return "runtime_error"
    return "unknown"


def _evaluate_worker(
    worker_outputs: list[dict[str, Any]],
    *,
    worker: Literal["claude_code", "codex"],
) -> WorkerValidity:
    """评价单个外部 worker。"""

    matched = [item for item in worker_outputs if item.get("worker") == worker]
    if not matched:
        return WorkerValidity(
            worker=worker,
            attempted=False,
            enabled=False,
            ok=False,
            failure_type="no_attempt",
            text_length=0,
            session_id_present=False,
            reason="没有 worker 调用记录",
        )

    latest = matched[-1]
    text = _text_of(latest.get("text")).strip()
    text_length = len(text)
    enabled = bool(latest.get("enabled"))
    ok = bool(latest.get("ok")) and text_length > 0
    if ok:
        return WorkerValidity(
            worker=worker,
            attempted=True,
            enabled=enabled,
            ok=True,
            failure_type="none",
            text_length=text_length,
            session_id_present=bool(latest.get("session_id")),
            reason="worker 启用且返回了可用文本",
        )

    failure_type = _classify_failure(latest)
    return WorkerValidity(
        worker=worker,
        attempted=True,
        enabled=enabled,
        ok=False,
        failure_type=failure_type,
        text_length=text_length,
        session_id_present=bool(latest.get("session_id")),
        reason=str(latest.get("error") or f"worker 未返回可用文本：{failure_type}"),
    )


def evaluate_external_agent_worker_stability_from_outputs(
    outputs: dict[str, Any],
) -> ExternalAgentWorkerStabilityResult:
    """从 Run.outputs 计算外部 Agent Worker 稳定性。"""

    worker_outputs = _extract_worker_outputs(outputs)
    claude_code = _evaluate_worker(worker_outputs, worker="claude_code")
    codex = _evaluate_worker(worker_outputs, worker="codex")

    attempted_workers = [worker for worker in [claude_code, codex] if worker.attempted]
    if not attempted_workers:
        score = 0.0
    else:
        score = sum(1.0 for worker in attempted_workers if worker.ok) / len(attempted_workers)

    blocking_issues = []
    for worker in [claude_code, codex]:
        if worker.attempted and not worker.ok:
            blocking_issues.append(
                f"{worker.worker} 不稳定：{worker.failure_type}；{worker.reason}"
            )

    return ExternalAgentWorkerStabilityResult(
        claude_code=claude_code,
        codex=codex,
        overall_stability_score=round(score, 4),
        blocking_issues=blocking_issues,
    )


def external_agent_worker_stability_evaluator(
    inputs: dict,
    reference_outputs: dict,
    outputs: dict,
) -> dict:
    """LangSmith 外部 Agent Worker 稳定性 evaluator。

    LangSmith 会自动传入：
    - inputs: Example.inputs
    - reference_outputs: Example.outputs
    - outputs: Run.outputs
    """

    result = evaluate_external_agent_worker_stability_from_outputs(outputs)
    question = inputs.get("question") or inputs.get("user_query") or ""
    comment = {
        "question": question,
        "claude_code": result.claude_code.model_dump(),
        "codex": result.codex.model_dump(),
        "blocking_issues": result.blocking_issues,
    }
    if not result.claude_code.attempted and not result.codex.attempted:
        return {
            "key": "external_agent_worker_stability",
            "value": "not_applicable",
            "comment": json.dumps(comment, ensure_ascii=False),
        }
    return {
        "key": "external_agent_worker_stability",
        "score": result.overall_stability_score,
        "comment": json.dumps(comment, ensure_ascii=False),
    }
