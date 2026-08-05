"""Prompt YAML 加载。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.config.settings import get_prompts_dir


REQUIRED_PROMPT_FIELDS = {
    "name",
    "version",
    "owner_node",
    "system_prompt",
    "user_prompt_template",
}


def load_prompt(prompt_file_name: str) -> dict[str, Any]:
    """读取并校验 Prompt YAML。"""

    prompt_path = get_prompts_dir() / prompt_file_name
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt 文件不存在：{prompt_path}")

    data = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Prompt YAML 必须是对象：{prompt_path}")

    missing_fields = REQUIRED_PROMPT_FIELDS - set(data)
    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise ValueError(f"Prompt YAML 缺少必需字段：{prompt_path} -> {missing}")

    return data


def render_prompt_template(template: str, variables: dict[str, Any]) -> str:
    """渲染 Prompt 模板。"""

    try:
        return template.format(**variables)
    except KeyError as exc:
        raise ValueError(f"Prompt 模板缺少变量：{exc.args[0]}") from exc


def load_yaml_config(file_name: str) -> dict[str, Any]:
    """读取普通 YAML 配置，不强制要求 Prompt 字段。"""

    config_path = get_prompts_dir() / file_name
    if not config_path.exists():
        raise FileNotFoundError(f"YAML 配置文件不存在：{config_path}")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML 配置必须是对象：{config_path}")
    return data
