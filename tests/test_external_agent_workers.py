"""外部 Agent worker 配置与权限测试。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from acp.schema import PermissionOption

from src.tools.external_agent_workers import _MinimalAcpClient


class CodexWorkerPermissionTest(unittest.TestCase):
    """验证 Codex worker 权限白名单。"""

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
