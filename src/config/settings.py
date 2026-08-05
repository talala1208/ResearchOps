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


DASHSCOPE_MODEL_FIELDS = {
    "input_guard": "SAFETY_GUARD_MODEL",
    "research_planner": "RESEARCH_PLANNER_MODEL",
    "search_task_planner": "SEARCH_TASK_PLANNER_MODEL",
    "web_search_subagent": "WEB_SEARCH_SUBAGENT_MODEL",
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


def get_model_config(role: str) -> ModelConfig:
    """按节点角色读取模型配置。

    规则：
    - 节点专用模型变量优先，例如 `RESEARCH_PLANNER_MODEL`。
    - 未配置节点专用模型时，使用 `DASHSCOPE_MODEL`。
    - 如果模型名以 `deepseek` 开头，则使用 DeepSeek 配置。
    - 其他模型默认使用 DashScope / OpenAI 兼容配置。
    """

    role_model_env = DASHSCOPE_MODEL_FIELDS.get(role)
    role_model = _read_optional_env(role_model_env) if role_model_env else None
    model = role_model or _read_required_env("DASHSCOPE_MODEL")

    if model.startswith("deepseek"):
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
