"""Online MCP 工具配置测试。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.tools.online_mcp_tools import _build_mcp_config


class OnlineMCPConfigTest(unittest.TestCase):
    """验证 Online MCP server 配置。"""

    def test_playwright_and_devtools_are_always_configured(self) -> None:
        """Playwright / DevTools 不依赖 API Key。"""

        with patch.dict(os.environ, {"TAVILY_API_KEY": "", "CONTEXT7_API_KEY": ""}):
            config = _build_mcp_config()

        self.assertEqual(config["playwright"]["transport"], "stdio")
        self.assertEqual(config["playwright"]["command"], "npx")
        self.assertEqual(config["playwright"]["args"], ["-y", "@playwright/mcp"])
        self.assertEqual(config["devtools"]["transport"], "stdio")
        self.assertEqual(config["devtools"]["command"], "npx")
        self.assertEqual(config["devtools"]["args"], ["chrome-devtools-mcp@latest"])
        self.assertNotIn("tavily-remote-mcp", config)
        self.assertNotIn("context7", config)


if __name__ == "__main__":
    unittest.main()
