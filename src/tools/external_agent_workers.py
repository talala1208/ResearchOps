"""外部 Agent worker 工具。

用途：给 Web Search SubAgent 在复杂问题或策略迭代时调用 Claude Code / Codex。

安全边界：
- 默认不真实调用外部 Agent。
- 必须显式设置环境变量启用。
- Claude Code 通过本地 CLI 只传入 prompt，并通过 `CLAUDE_CODE_ARGS` 控制允许工具。
- Codex 参考 `reference/scripts` 的 ACP 思路，通过 `agent-client-protocol` 包提供的 `acp` Python SDK 调用。
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from langchain.tools import tool
from langsmith import traceable

from src.config.settings import get_project_root


CODEX_BIN_DIR = Path("/Applications/Codex.app/Contents/Resources")
ACP_ENV = os.environ.copy()
if CODEX_BIN_DIR.is_dir():
    ACP_ENV["PATH"] = f"{CODEX_BIN_DIR}:{ACP_ENV.get('PATH', '')}"

NPX_COMMAND = os.getenv("ACP_NPX_COMMAND", "npx")
CODEX_ACP_PACKAGE = os.getenv(
    "CODEX_ACP_PACKAGE",
    "@agentclientprotocol/codex-acp@1.1.0",
)
CODEX_WORKER_ALLOWED_TOOL_KINDS = {
    item.strip()
    for item in os.getenv(
        "CODEX_WORKER_ALLOWED_TOOL_KINDS",
        "search,fetch,think,other",
    ).split(",")
    if item.strip()
}
CODEX_WORKER_TIMEOUT_SECONDS = int(os.getenv("CODEX_WORKER_TIMEOUT_SECONDS", "60"))


@dataclass(frozen=True)
class AgentWorkerResult:
    """外部 Agent worker 返回结果。"""

    worker: str
    enabled: bool
    ok: bool
    text: str
    error: str | None = None
    session_id: str | None = None

    def to_json(self) -> str:
        """序列化为 JSON。"""

        return json.dumps(
            {
                "worker": self.worker,
                "enabled": self.enabled,
                "ok": self.ok,
                "text": self.text,
                "error": self.error,
                "session_id": self.session_id,
            },
            ensure_ascii=False,
        )


def _env_enabled(name: str) -> bool:
    """判断布尔环境变量是否开启。"""

    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _compact_prompt(prompt: str, limit: int = 4000) -> str:
    """限制传给外部 worker 的上下文长度。"""

    normalized = "\n".join(line.rstrip() for line in prompt.strip().splitlines())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "\n...(已截断)"


@tool
def call_claude_code_worker(prompt: str) -> str:
    """调用 Claude Code worker 处理复杂网页研究或策略迭代问题。"""

    if not _env_enabled("ENABLE_CLAUDE_CODE_WORKER"):
        return AgentWorkerResult(
            worker="claude_code",
            enabled=False,
            ok=False,
            text="",
            error="Claude Code worker 默认禁用；设置 ENABLE_CLAUDE_CODE_WORKER=true 后才会真实调用。",
        ).to_json()

    command = os.getenv("CLAUDE_CODE_COMMAND", "claude")
    args = shlex.split(
        os.getenv(
            "CLAUDE_CODE_ARGS",
            "--print --permission-mode auto --allowedTools WebFetch,WebSearch",
        )
    )
    timeout_seconds = int(os.getenv("CLAUDE_CODE_TIMEOUT_SECONDS", "60"))
    if shutil.which(command) is None and not Path(command).is_file():
        return AgentWorkerResult(
            worker="claude_code",
            enabled=True,
            ok=False,
            text="",
            error=f"找不到 Claude Code 命令：{command}",
        ).to_json()

    try:
        completed = subprocess.run(
            [command, *args, _compact_prompt(prompt)],
            cwd=get_project_root(),
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        return AgentWorkerResult(
            worker="claude_code",
            enabled=True,
            ok=False,
            text=str(stdout).strip(),
            error=(
                str(stderr).strip()
                or f"Claude Code worker 超时：{timeout_seconds} 秒"
            ),
        ).to_json()
    if completed.returncode != 0:
        return AgentWorkerResult(
            worker="claude_code",
            enabled=True,
            ok=False,
            text=completed.stdout.strip(),
            error=completed.stderr.strip() or f"Claude Code 退出码：{completed.returncode}",
        ).to_json()

    return AgentWorkerResult(
        worker="claude_code",
        enabled=True,
        ok=True,
        text=completed.stdout.strip(),
    ).to_json()


class _MinimalAcpClient:
    """Codex ACP 最小 Client。

    只收公开文本；按白名单允许搜索、网页抓取和思考类能力，
    继续拒绝文件写入和本地命令执行类能力。
    """

    def __init__(self) -> None:
        self.text_chunks: list[str] = []
        self.update_kinds: list[str] = []
        self.permission_decisions: list[str] = []

    def begin_turn(self) -> None:
        self.text_chunks = []
        self.update_kinds = []
        self.permission_decisions = []

    async def session_update(
        self,
        session_id: str | None = None,
        update: Any | None = None,
        **kwargs: Any,
    ) -> None:
        """收集 Codex worker 的消息流。"""

        del session_id
        if update is None:
            update = kwargs.get("update")
        update_kind = str(getattr(update, "session_update", ""))
        self.update_kinds.append(update_kind)
        if update_kind != "agent_message_chunk":
            return
        content = getattr(update, "content", None)
        if content is None or getattr(content, "type", "") != "text":
            return
        text = getattr(content, "text", "")
        if text:
            self.text_chunks.append(str(text))

    async def request_permission(
        self,
        options: list[Any],
        session_id: str | None = None,
        tool_call: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        """按允许的工具类型授予 Codex worker 一次性权限。"""

        from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse

        del session_id
        tool_call = tool_call or kwargs.get("tool_call")
        tool_kind = str(getattr(tool_call, "kind", ""))
        if tool_kind in CODEX_WORKER_ALLOWED_TOOL_KINDS:
            allow_option = next(
                (
                    option
                    for option in options
                    if str(getattr(option, "kind", "")).startswith("allow")
                ),
                None,
            )
            if allow_option is not None:
                option_id = str(getattr(allow_option, "option_id"))
                self.permission_decisions.append(option_id)
                return RequestPermissionResponse(
                    outcome=AllowedOutcome(
                        outcome="selected",
                        option_id=option_id,
                    )
                )

        reject_option = next(
            (
                option
                for option in options
                if str(getattr(option, "kind", "")).startswith("reject")
            ),
            None,
        )
        if reject_option is not None:
            self.permission_decisions.append(str(getattr(reject_option, "option_id")))
        else:
            self.permission_decisions.append("cancelled")
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def write_text_file(self, **kwargs: Any) -> None:
        raise RuntimeError("Codex worker 未声明文件写入能力")

    async def read_text_file(self, **kwargs: Any) -> Any:
        raise RuntimeError("Codex worker 未声明文件读取能力")

    @property
    def text(self) -> str:
        return "".join(self.text_chunks).strip()


@traceable(name="call_codex_worker_async", run_type="tool")
async def _call_codex_worker_async(prompt: str) -> AgentWorkerResult:
    """通过 ACP 调用 Codex worker。"""

    try:
        from acp import PROTOCOL_VERSION, Client, spawn_agent_process, text_block
        from acp.schema import ClientCapabilities, Implementation
    except ImportError as exc:
        return AgentWorkerResult(
            worker="codex",
            enabled=True,
            ok=False,
            text="",
            error=f"缺少 acp Python SDK，无法按 reference/scripts 的 ACP 方式调用 Codex：{exc}",
        )

    if (
        not Path(NPX_COMMAND).is_file()
        and shutil.which(NPX_COMMAND, path=ACP_ENV.get("PATH")) is None
    ):
        return AgentWorkerResult(
            worker="codex",
            enabled=True,
            ok=False,
            text="",
            error=f"找不到 Codex ACP 启动器：{NPX_COMMAND}",
        )

    client = _MinimalAcpClient()
    process_cm = spawn_agent_process(
        cast(Client, client),
        NPX_COMMAND,
        "-y",
        CODEX_ACP_PACKAGE,
        cwd=get_project_root(),
        env=ACP_ENV,
    )
    async with process_cm as (connection, _process):
        init = await connection.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
            client_info=Implementation(
                name="researchops-web-search-subagent",
                title="ResearchOps Web Search SubAgent",
                version="0.1.0",
            ),
        )
        session = await connection.new_session(
            cwd=str(get_project_root()),
            mcp_servers=[],
        )
        client.begin_turn()
        response = await connection.prompt(
            prompt=[text_block(_compact_prompt(prompt))],
            session_id=session.session_id,
        )
        return AgentWorkerResult(
            worker="codex",
            enabled=True,
            ok=True,
            text=client.text,
            error=None if client.text else f"Codex stop_reason={response.stop_reason}",
            session_id=session.session_id,
        )


@tool
def call_codex_worker(prompt: str) -> str:
    """调用 Codex worker 处理复杂网页研究或策略迭代问题。"""

    if not _env_enabled("ENABLE_CODEX_WORKER"):
        return AgentWorkerResult(
            worker="codex",
            enabled=False,
            ok=False,
            text="",
            error="Codex worker 默认禁用；设置 ENABLE_CODEX_WORKER=true 后才会真实调用。",
        ).to_json()

    try:
        result = asyncio.run(
            asyncio.wait_for(
                _call_codex_worker_async(prompt),
                timeout=CODEX_WORKER_TIMEOUT_SECONDS,
            )
        )
    except TimeoutError:
        return AgentWorkerResult(
            worker="codex",
            enabled=True,
            ok=False,
            text="",
            error=f"Codex worker 超时：{CODEX_WORKER_TIMEOUT_SECONDS} 秒",
        ).to_json()
    except RuntimeError as exc:
        return AgentWorkerResult(
            worker="codex",
            enabled=True,
            ok=False,
            text="",
            error=f"Codex worker 调用失败：{exc}",
        ).to_json()
    return result.to_json()
