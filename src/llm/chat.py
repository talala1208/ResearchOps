"""LLM 客户端封装。"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from src.config.settings import get_model_config


def build_chat_model(role: str) -> ChatOpenAI:
    """创建 OpenAI 兼容 Chat 模型。"""

    config = get_model_config(role)
    return ChatOpenAI(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=0,
    )
