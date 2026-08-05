"""工作流配置读取测试。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.config.settings import get_model_config, get_workflow_config


class ModelConfigTest(unittest.TestCase):
    """验证模型供应商配置选择。"""

    def test_dashscope_deepseek_v4_model_uses_dashscope_provider(self) -> None:
        """DashScope 托管的 deepseek-v4 模型不走 DeepSeek 官方配置。"""

        env = {
            "DASHSCOPE_API_KEY": "dashscope-key",
            "DASHSCOPE_BASE_URL": "https://dashscope.example/v1",
            "DASHSCOPE_MODEL": "qwen3.7-plus",
            "RESEARCH_PLANNER_MODEL": "deepseek-v4-flash",
            "DEEPSEEK_API_KEY": "deepseek-key",
            "DEEPSEEK_BASE_URL": "https://deepseek.example/v1",
        }
        with patch.dict(os.environ, env, clear=False):
            config = get_model_config("research_planner")

        self.assertEqual(config.model, "deepseek-v4-flash")
        self.assertEqual(config.api_key, "dashscope-key")
        self.assertEqual(config.base_url, "https://dashscope.example/v1")

    def test_native_deepseek_model_uses_deepseek_provider(self) -> None:
        """DeepSeek 官方模型名走 DeepSeek 官方配置。"""

        env = {
            "DASHSCOPE_API_KEY": "dashscope-key",
            "DASHSCOPE_BASE_URL": "https://dashscope.example/v1",
            "DASHSCOPE_MODEL": "qwen3.7-plus",
            "RESEARCH_PLANNER_MODEL": "deepseek-chat",
            "DEEPSEEK_API_KEY": "deepseek-key",
            "DEEPSEEK_BASE_URL": "https://deepseek.example/v1",
        }
        with patch.dict(os.environ, env, clear=False):
            config = get_model_config("research_planner")

        self.assertEqual(config.model, "deepseek-chat")
        self.assertEqual(config.api_key, "deepseek-key")
        self.assertEqual(config.base_url, "https://deepseek.example/v1")


class WorkflowConfigTest(unittest.TestCase):
    """验证预算与阈值从环境变量读取。"""

    def test_get_workflow_config_reads_required_env_values(self) -> None:
        """读取检索、Review、安全预算和评分阈值。"""

        env = {
            "MAX_SEARCH_STEPS": "5",
            "MAX_REVIEW_REVISIONS": "2",
            "MAX_SAFETY_REVISIONS": "3",
            "HITL_CONFLICT_THRESHOLD": "8.5",
            "REVIEW_PASS_SCORE": "0.82",
        }
        with patch.dict(os.environ, env, clear=False):
            config = get_workflow_config()

        self.assertEqual(config.max_search_steps, 5)
        self.assertEqual(config.max_review_revisions, 2)
        self.assertEqual(config.max_safety_revisions, 3)
        self.assertEqual(config.hitl_conflict_threshold, 8.5)
        self.assertEqual(config.review_pass_score, 0.82)


if __name__ == "__main__":
    unittest.main()
