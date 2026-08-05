"""外部 Agent worker 配置与权限测试。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from acp.schema import PermissionOption

from src.tools.external_agent_workers import (
    AgentWorkerResult,
    _MinimalAcpClient,
    _normalize_claude_args,
    call_claude_code_worker,
)


class ClaudeCodeWorkerTest(unittest.TestCase):
    """验证 Claude Code worker 调用边界。"""

    def test_normalize_allowed_tools_does_not_swallow_prompt(self) -> None:
        """Claude 可变参数不能吞掉最终 prompt。"""

        args = _normalize_claude_args(
            ["--print", "--allowedTools", "WebFetch,WebSearch"]
        )

        self.assertEqual(args, ["--print", "--allowedTools=WebFetch,WebSearch"])

    def test_claude_code_worker_returns_runner_result(self) -> None:
        """Claude Code worker 返回底层 runner 的结构化结果。"""

        with (
            patch.dict(
                "os.environ",
                {
                    "ENABLE_CLAUDE_CODE_WORKER": "true",
                    "CLAUDE_CODE_COMMAND": "claude",
                    "CLAUDE_CODE_TIMEOUT_SECONDS": "1",
                    "LANGSMITH_TRACING": "false",
                    "LANGCHAIN_TRACING_V2": "false",
                },
                clear=False,
            ),
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch(
                "src.tools.external_agent_workers._run_claude_code_command",
                return_value=AgentWorkerResult(
                    worker="claude_code",
                    enabled=True,
                    ok=True,
                    text="WORKER_OK",
                ),
            ),
        ):
            result_json = call_claude_code_worker.invoke("测试")

        self.assertIn("WORKER_OK", result_json)


class CodexWorkerPermissionTest(unittest.TestCase):
    """验证 Codex worker 权限白名单。"""

    def test_collect_agent_message_chunk(self) -> None:
        """Codex ACP session_update 能收集文本消息。"""

        client = _MinimalAcpClient()
        update = SimpleNamespace(
            session_update="agent_message_chunk",
            content=SimpleNamespace(type="text", text="CODEX_OK"),
        )

        asyncio.run(client.session_update("session-1", update))

        self.assertEqual(client.text, "CODEX_OK")
        self.assertEqual(client.update_kinds, ["agent_message_chunk"])

    def test_allow_fetch_permission_once(self) -> None:
        """fetch 类型工具允许一次。"""

        client = _MinimalAcpClient()
        response = asyncio.run(
            client.request_permission(
                options=[
                    PermissionOption(
                        optionId="allow-fetch-once",
                        name="允许一次",
                        kind="allow_once",
                    ),
                    PermissionOption(
                        optionId="reject-fetch",
                        name="拒绝",
                        kind="reject_once",
                    ),
                ],
                tool_call=SimpleNamespace(kind="fetch"),
            )
        )

        self.assertEqual(response.outcome.outcome, "selected")
        self.assertEqual(response.outcome.option_id, "allow-fetch-once")

    def test_reject_execute_permission(self) -> None:
        """execute 类型工具继续拒绝。"""

        client = _MinimalAcpClient()
        response = asyncio.run(
            client.request_permission(
                options=[
                    PermissionOption(
                        optionId="allow-execute-once",
                        name="允许一次",
                        kind="allow_once",
                    ),
                    PermissionOption(
                        optionId="reject-execute",
                        name="拒绝",
                        kind="reject_once",
                    ),
                ],
                tool_call=SimpleNamespace(kind="execute"),
            )
        )

        self.assertEqual(response.outcome.outcome, "cancelled")
        self.assertEqual(client.permission_decisions, ["reject-execute"])


if __name__ == "__main__":
    unittest.main()
