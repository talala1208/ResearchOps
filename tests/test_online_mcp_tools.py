"""Online MCP 工具配置测试。"""

from __future__ import annotations

import os
import socket
import unittest
from unittest.mock import patch

from src.tools import online_mcp_tools
from src.tools.online_mcp_tools import _build_mcp_config


class OnlineMCPConfigTest(unittest.TestCase):
    """验证 Online MCP server 配置。"""

    def test_playwright_and_devtools_are_always_configured(self) -> None:
        """Playwright / DevTools 不依赖 API Key。"""

        with patch.dict(os.environ, {"TAVILY_API_KEY": "", "CONTEXT7_API_KEY": ""}):
            config = _build_mcp_config()

        self.assertEqual(config["playwright"]["transport"], "stdio")
        self.assertEqual(config["playwright"]["command"], "npx")
        self.assertEqual(
            config["playwright"]["args"],
            ["-y", "@playwright/mcp", "--headless", "--isolated", "--browser", "chrome"],
        )
        self.assertEqual(config["devtools"]["transport"], "stdio")
        self.assertEqual(config["devtools"]["command"], "npx")
        self.assertEqual(
            config["devtools"]["args"],
            [
                "chrome-devtools-mcp@latest",
                "--isolated",
                "--no-usage-statistics",
                "--no-performance-crux",
            ],
        )
        self.assertNotIn("tavily-remote-mcp", config)
        self.assertNotIn("context7", config)


class PlaywrightMCPSessionTest(unittest.IsolatedAsyncioTestCase):
    """验证 Playwright 连续调用复用同一个 MCP 会话。"""

    async def test_navigation_and_snapshot_share_one_session(self) -> None:
        """导航后应在仍然活跃的同一会话中读取快照。"""

        state = {"active": False}
        calls: list[tuple[str, dict]] = []

        class FakeSessionContext:
            async def __aenter__(self) -> object:
                state["active"] = True
                return object()

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                state["active"] = False

        class FakeClient:
            def session(self, server_name: str) -> FakeSessionContext:
                self.server_name = server_name
                return FakeSessionContext()

        class FakeTool:
            def __init__(self, name: str) -> None:
                self.name = name

            async def ainvoke(self, payload: dict) -> str:
                self.assert_session_active()
                calls.append((self.name, payload))
                return f"{self.name}:https://example.com"

            @staticmethod
            def assert_session_active() -> None:
                if not state["active"]:
                    raise AssertionError("Playwright MCP 会话已在工具调用前关闭")

        client = FakeClient()
        tools = [FakeTool("browser_navigate"), FakeTool("browser_snapshot")]
        with patch.object(
            online_mcp_tools,
            "_build_mcp_client",
            return_value=client,
        ), patch.object(
            online_mcp_tools,
            "_load_mcp_session_tools",
            return_value=tools,
        ), patch.object(
            online_mcp_tools.socket,
            "getaddrinfo",
            return_value=[
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    ("93.184.216.34", 443),
                )
            ],
        ):
            result = await online_mcp_tools._playwright_fetch_page_async(
                "https://example.com"
            )

        self.assertEqual(client.server_name, "playwright")
        self.assertFalse(state["active"])
        self.assertEqual(
            [name for name, _ in calls],
            ["browser_navigate", "browser_snapshot"],
        )
        self.assertIn("https://example.com", result["snapshot_result"])

    async def test_private_navigation_target_is_rejected(self) -> None:
        """导航前应拒绝解析到私网的目标。"""

        with patch.object(
            online_mcp_tools.socket,
            "getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
            ],
        ):
            with self.assertRaisesRegex(
                online_mcp_tools.MCPToolError,
                "非公网地址",
            ):
                await online_mcp_tools._playwright_fetch_page_async(
                    "https://internal.example"
                )

    async def test_private_redirect_target_is_rejected(self) -> None:
        """导航结果暴露私网重定向地址时应在读取快照前拒绝。"""

        state = {"snapshot_called": False}

        class FakeSessionContext:
            async def __aenter__(self) -> object:
                return object()

            async def __aexit__(self, exc_type, exc, traceback) -> None:
                return None

        class FakeClient:
            def session(self, server_name: str) -> FakeSessionContext:
                return FakeSessionContext()

        class FakeTool:
            def __init__(self, name: str) -> None:
                self.name = name

            async def ainvoke(self, payload: dict) -> str:
                if self.name == "browser_navigate":
                    return "已跳转到 http://127.0.0.1/admin"
                state["snapshot_called"] = True
                return "snapshot"

        def fake_getaddrinfo(host: str, port: int, **kwargs):
            address = "127.0.0.1" if host == "127.0.0.1" else "93.184.216.34"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

        with patch.object(
            online_mcp_tools,
            "_build_mcp_client",
            return_value=FakeClient(),
        ), patch.object(
            online_mcp_tools,
            "_load_mcp_session_tools",
            return_value=[FakeTool("browser_navigate"), FakeTool("browser_snapshot")],
        ), patch.object(
            online_mcp_tools.socket,
            "getaddrinfo",
            side_effect=fake_getaddrinfo,
        ):
            with self.assertRaisesRegex(
                online_mcp_tools.MCPToolError,
                "非公网地址",
            ):
                await online_mcp_tools._playwright_fetch_page_async(
                    "https://example.com"
                )

        self.assertFalse(state["snapshot_called"])

    def test_non_standard_port_is_rejected(self) -> None:
        """HTTP(S) 导航不允许使用非标准端口。"""

        with self.assertRaisesRegex(
            online_mcp_tools.MCPToolError,
            "非预期端口",
        ):
            online_mcp_tools._validate_public_http_url(
                "https://example.com:8443"
            )


if __name__ == "__main__":
    unittest.main()
