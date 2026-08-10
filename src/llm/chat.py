"""LLM 客户端封装。"""

from __future__ import annotations

import os
from typing import Any

from langchain_openai import ChatOpenAI

from src.config.settings import get_model_config, uses_native_deepseek_provider


def _dashscope_enable_thinking() -> bool:
    """读取是否启用 DashScope 混合思考；默认关闭。

    qwen3.7-max 等模型默认开启思考；非流式 invoke 会长时间无输出，
    对结构化 JSON 节点表现为卡住。显式默认关闭，需要时再开。
    """

    raw = os.getenv("DASHSCOPE_ENABLE_THINKING")
    if raw is None or not str(raw).strip():
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def build_chat_model(role: str) -> ChatOpenAI:
    """创建 OpenAI 兼容 Chat 模型。"""

    config = get_model_config(role)
    kwargs: dict[str, Any] = {
        "model": config.model,
        "api_key": config.api_key,
        "base_url": config.base_url,
        "temperature": 0,
    }
    # DashScope 兼容接口：对混合思考模型传入 enable_thinking，避免默认思考拖死节点。
    if not uses_native_deepseek_provider(config.model):
        kwargs["extra_body"] = {"enable_thinking": _dashscope_enable_thinking()}
    return ChatOpenAI(**kwargs)
