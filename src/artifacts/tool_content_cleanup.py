"""代码路径工具结果的确定性清洗。

Tavily / Context7 / Playwright / DevTools 不经汇总 LLM，落盘或进 State 前做轻量清洗：
解包 text blocks、规范空白、截断；不做脆弱的“提主文”启发式。
"""

from __future__ import annotations

import json
import re
from typing import Any


_MULTI_BLANK_LINES = re.compile(r"\n{3,}")
_MULTI_SPACES = re.compile(r"[ \t]{2,}")
_PLAYWRIGHT_NOISE_LINES = re.compile(
    r"^(skip to (main )?content|sign in|log in|cookie(s)?|accept all|拒绝|登录|注册)\s*$",
    re.IGNORECASE,
)


def unwrap_text_content(value: Any) -> str:
    """把 MCP / LLM 常见 content 形态解成纯文本。"""

    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if text.startswith("{") or text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return text
            # Tavily 等 MCP 常把整段 JSON（含 results）放进 text block。
            # 若解析后不是可解包正文结构，保留原 JSON 字符串，供物化层再 json.loads。
            nested = unwrap_text_content(parsed)
            return nested if nested else text
        return text
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            part = unwrap_text_content(item)
            if part:
                parts.append(part)
        return "\n".join(parts).strip()
    if isinstance(value, dict):
        # 常见 text block：{"type":"text","text":"..."}
        for key in ("text", "content", "docs_result", "docs", "markdown", "page_text"):
            nested = value.get(key)
            if nested is not None and key in value:
                unwrapped = unwrap_text_content(nested)
                if unwrapped:
                    return unwrapped
        # 避免把整个结构化对象 str() 成伪正文
        return ""
    return str(value).strip()


def normalize_whitespace(text: str) -> str:
    """压缩多余空白，保留段落换行。"""

    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _MULTI_SPACES.sub(" ", normalized)
    normalized = _MULTI_BLANK_LINES.sub("\n\n", normalized)
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def truncate_cleaned_text(text: str, max_chars: int) -> str:
    """截断已清洗文本。"""

    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def clean_plain_text(value: Any, *, max_chars: int) -> str:
    """通用：解包 + 空白规范化 + 截断。"""

    text = normalize_whitespace(unwrap_text_content(value))
    return truncate_cleaned_text(text, max_chars)


def clean_tavily_snippet(value: Any, *, max_chars: int = 2000) -> str:
    """清洗 Tavily content/snippet。"""

    return clean_plain_text(value, max_chars=max_chars)


def clean_context7_docs(value: Any, *, max_chars: int) -> str:
    """清洗 Context7 文档正文；拒绝把整个 payload dict 当正文。"""

    if isinstance(value, dict) and not any(
        key in value for key in ("text", "content", "docs_result", "docs", "markdown")
    ):
        # 例如 {'library_id': ..., 'resolve_result': ...} 不能当 docs
        return ""
    return clean_plain_text(value, max_chars=max_chars)


def clean_context7_resolve(value: Any, *, max_chars: int) -> str:
    """清洗 Context7 resolve 元数据，仅作短说明。"""

    return clean_plain_text(value, max_chars=max_chars)


def clean_playwright_body(value: Any, *, max_chars: int) -> str:
    """清洗 Playwright 页面正文：去空白、去掉极短导航噪声行、截断。"""

    text = normalize_whitespace(unwrap_text_content(value))
    if not text:
        return ""
    kept_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            kept_lines.append("")
            continue
        if len(stripped) <= 40 and _PLAYWRIGHT_NOISE_LINES.match(stripped):
            continue
        kept_lines.append(line)
    cleaned = normalize_whitespace("\n".join(kept_lines))
    return truncate_cleaned_text(cleaned, max_chars)


def compact_devtools_observation(
    payload: dict[str, Any],
    *,
    max_field_chars: int = 1200,
) -> dict[str, Any]:
    """压缩 DevTools 观测结果，仅保留短字段，不作为证据正文。"""

    if not isinstance(payload, dict):
        return {
            "ok": False,
            "provider": "devtools_mcp",
            "error": "devtools observation 非对象",
        }

    compact: dict[str, Any] = {
        "ok": bool(payload.get("ok", True)),
        "provider": payload.get("provider") or "devtools_mcp",
    }
    if payload.get("skipped"):
        compact["skipped"] = True
    if isinstance(payload.get("reason"), str):
        compact["reason"] = clean_plain_text(payload["reason"], max_chars=300)
    if isinstance(payload.get("error"), str):
        compact["error"] = clean_plain_text(payload["error"], max_chars=500)

    raw_result = payload.get("raw_result")
    if isinstance(raw_result, dict):
        navigation = clean_plain_text(
            raw_result.get("navigation_result"),
            max_chars=max_field_chars,
        )
        inspect = clean_plain_text(
            raw_result.get("inspect_result") or raw_result.get("snapshot_result"),
            max_chars=max_field_chars,
        )
        compact["observation"] = {
            "navigation_summary": navigation,
            "inspect_summary": inspect,
        }
    elif raw_result is not None:
        compact["observation"] = {
            "raw_summary": clean_plain_text(raw_result, max_chars=max_field_chars),
        }
    return compact
