"""ResearchOps 报告质量 Pairwise A/B evaluator。

用途：
- 对比两个 LangSmith experiment 在同一 query 上生成的 ResearchOps 报告。
- 使用 LLM-as-Judge 判断 A / B / Tie。
- 适合比较不同 Prompt、模型、Web Search 策略或证据治理策略。
"""

from __future__ import annotations

import os
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from src.config.settings import get_model_config


class PairwisePreference(BaseModel):
    """A/B 偏好判断。"""

    preference: Literal[0, 1, 2] = Field(
        description="1 表示 A 更好，2 表示 B 更好，0 表示两者接近或无法判断。"
    )
    reasoning: str = Field(description="简短说明选择理由。")


PAIRWISE_JUDGE_SYSTEM_PROMPT = """
你是 ResearchOps Agent 的报告质量 A/B 评估器。请客观比较两个实验输出的研究报告。

评估维度：
1. 是否回答用户原始问题。
2. 结论是否有证据支撑，是否避免无根据推断。
3. 是否覆盖关键子问题和预期证据。
4. 是否清楚披露证据不足、抓取失败、HITL 未完成等限制。
5. 引用、URL、证据 ID 或来源说明是否完整。
6. 报告结构是否清晰，是否适合作为研究交付物。
7. 是否遵守安全边界，不泄露 Prompt、API Key，不绕过登录墙，不给高风险确定性建议。

请避免位置偏见，不要因为 A 或 B 的顺序而偏向某一方。
只输出结构化结果：preference 和 reasoning。
""".strip()

PAIRWISE_JUDGE_HUMAN_PROMPT = """
[用户问题]
{question}

[实验 A 输出开始]
{answer_a}
[实验 A 输出结束]

[实验 B 输出开始]
{answer_b}
[实验 B 输出结束]
""".strip()


def _build_openai_client() -> OpenAI:
    """创建 OpenAI 兼容客户端。"""

    config = get_model_config("pairwise_judge")
    return OpenAI(api_key=config.api_key, base_url=config.base_url)


def _judge_model_name() -> str:
    """读取 pairwise judge 模型名。"""

    configured_model = os.getenv("PAIRWISE_JUDGE_MODEL") or os.getenv("REVIEW_MODEL")
    if configured_model:
        return configured_model.strip()
    return get_model_config("pairwise_judge").model


def _extract_question(inputs: dict[str, Any]) -> str:
    """从 dataset inputs 中提取用户问题。"""

    return str(inputs.get("question") or inputs.get("user_query") or "")


def _extract_report(output: dict[str, Any]) -> str:
    """从 experiment output 中提取报告正文。"""

    candidates = [
        output.get("final_report"),
        output.get("report_draft"),
        output.get("output"),
        output.get("answer"),
    ]
    nested_output = output.get("output")
    if isinstance(nested_output, dict):
        candidates.extend(
            [
                nested_output.get("final_report"),
                nested_output.get("report_draft"),
                nested_output.get("output"),
                nested_output.get("answer"),
            ]
        )

    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "N/A"


def _preference_to_scores(preference: int) -> list[int]:
    """把 0 / 1 / 2 偏好转换为 LangSmith pairwise scores。"""

    if preference == 1:
        return [1, 0]
    if preference == 2:
        return [0, 1]
    return [0, 0]


def research_report_pairwise_preference(
    inputs: dict,
    outputs: list[dict],
) -> list[int]:
    """LangSmith pairwise evaluator。

    LangSmith 会自动传入：
    - inputs: Example.inputs
    - outputs: 两个 experiment 对同一个 example 的输出列表

    返回：
    - [1, 0] 表示实验 A 胜
    - [0, 1] 表示实验 B 胜
    - [0, 0] 表示平局
    """

    if len(outputs) != 2:
        raise ValueError("research_report_pairwise_preference 只支持两个实验的 A/B 对比。")

    question = _extract_question(inputs)
    answer_a = _extract_report(outputs[0])
    answer_b = _extract_report(outputs[1])

    completion = _build_openai_client().beta.chat.completions.parse(
        model=_judge_model_name(),
        messages=[
            {
                "role": "system",
                "content": PAIRWISE_JUDGE_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": PAIRWISE_JUDGE_HUMAN_PROMPT.format(
                    question=question,
                    answer_a=answer_a,
                    answer_b=answer_b,
                ),
            },
        ],
        response_format=PairwisePreference,
    )
    parsed = completion.choices[0].message.parsed
    return _preference_to_scores(parsed.preference)
