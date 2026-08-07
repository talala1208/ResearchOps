"""项目运行配置读取与校验。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class ModelConfig:
    """单个模型调用配置。"""

    model: str
    api_key: str
    base_url: str | None


@dataclass(frozen=True)
class WorkflowConfig:
    """工作流运行阈值与预算配置。"""

    max_search_steps: int
    max_review_revisions: int
    max_safety_revisions: int
    hitl_conflict_threshold: float
    review_pass_score: float


DASHSCOPE_MODEL_FIELDS = {
    "input_guard": "SAFETY_GUARD_MODEL",
    "research_planner": "RESEARCH_PLANNER_MODEL",
    "search_task_planner": "SEARCH_TASK_PLANNER_MODEL",
    "web_search_subagent": "WEB_SEARCH_SUBAGENT_MODEL",
    "local_document_search": "LOCAL_DOCUMENT_SEARCH_MODEL",
    "research_report": "RESEARCH_REPORT_MODEL",
    "research_report_review": "RESEARCH_REPORT_REVIEW_MODEL",
}

DASHSCOPE_DEEPSEEK_PREFIXES = ("deepseek-v4-",)
NATIVE_DEEPSEEK_MODELS = {
    "deepseek-chat",
    "deepseek-reasoner",
}


def get_project_root() -> Path:
    """返回项目根目录。"""

    return PROJECT_ROOT


def get_prompts_dir() -> Path:
    """返回 Prompt 目录。"""

    return PROJECT_ROOT / "prompts"


def _read_required_env(name: str) -> str:
    """读取必需环境变量。"""

    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise RuntimeError(f"缺少必要环境变量：{name}")
    return value.strip()


def _read_optional_env(name: str) -> str | None:
    """读取可选环境变量。"""

    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _read_required_int_env(name: str) -> int:
    """读取必需整数环境变量。"""

    raw_value = _read_required_env(name)
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 必须是整数，当前值：{raw_value}") from exc
    if value < 0:
        raise RuntimeError(f"环境变量 {name} 不能小于 0，当前值：{raw_value}")
    return value


def _read_required_float_env(name: str) -> float:
    """读取必需浮点数环境变量。"""

    raw_value = _read_required_env(name)
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 必须是数字，当前值：{raw_value}") from exc
    if value < 0:
        raise RuntimeError(f"环境变量 {name} 不能小于 0，当前值：{raw_value}")
    return value


def get_workflow_config() -> WorkflowConfig:
    """读取工作流预算与阈值配置。"""

    return WorkflowConfig(
        max_search_steps=_read_required_int_env("MAX_SEARCH_STEPS"),
        max_review_revisions=_read_required_int_env("MAX_REVIEW_REVISIONS"),
        max_safety_revisions=_read_required_int_env("MAX_SAFETY_REVISIONS"),
        hitl_conflict_threshold=_read_required_float_env("HITL_CONFLICT_THRESHOLD"),
        review_pass_score=_read_required_float_env("REVIEW_PASS_SCORE"),
    )


def _uses_native_deepseek_provider(model: str) -> bool:
    """判断模型是否应走 DeepSeek 官方接口。

    注意：DashScope 也提供以 `deepseek` 开头的模型名，例如
    `deepseek-v4-flash`，这类模型仍应使用 DashScope 的 API Key 和 base_url。
    """

    if model.startswith(DASHSCOPE_DEEPSEEK_PREFIXES):
        return False
    return model in NATIVE_DEEPSEEK_MODELS


def get_model_config(role: str) -> ModelConfig:
    """按节点角色读取模型配置。

    规则：
    - 节点专用模型变量优先，例如 `RESEARCH_PLANNER_MODEL`。
    - 未配置节点专用模型时，使用 `DASHSCOPE_MODEL`。
    - DashScope 托管的 DeepSeek 模型仍使用 DashScope 配置。
    - DeepSeek 官方模型名才使用 DeepSeek 配置。
    - 其他模型默认使用 DashScope / OpenAI 兼容配置。
    """

    role_model_env = DASHSCOPE_MODEL_FIELDS.get(role)
    role_model = _read_optional_env(role_model_env) if role_model_env else None
    model = role_model or _read_required_env("DASHSCOPE_MODEL")

    if _uses_native_deepseek_provider(model):
        return ModelConfig(
            model=model,
            api_key=_read_required_env("DEEPSEEK_API_KEY"),
            base_url=_read_optional_env("DEEPSEEK_BASE_URL"),
        )

    return ModelConfig(
        model=model,
        api_key=_read_required_env("DASHSCOPE_API_KEY"),
        base_url=_read_optional_env("DASHSCOPE_BASE_URL"),
    )
