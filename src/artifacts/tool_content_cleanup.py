"""代码路径工具结果的确定性清洗。

Tavily / Context7 / Playwright / DevTools 不经汇总 LLM，落盘或进 State 前做轻量清洗：
解包 text blocks、规范空白、截断；Playwright accessibility snapshot 另做确定性角色过滤。
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
_PLAYWRIGHT_A11Y_HINT = re.compile(
    r"(?:###\s*Snapshot|\[ref=e\d+\]|^\s*-\s+(?:main|article|heading|paragraph)\b)",
    re.IGNORECASE | re.MULTILINE,
)
_PAGE_URL_RE = re.compile(r"^-\s*Page URL:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_PAGE_TITLE_RE = re.compile(r"^-\s*Page Title:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
# Playwright a11y 行：- role "name" [attrs]: trailing  或  - 'role "name" [attrs]'
_A11Y_ROLE_LINE_RE = re.compile(
    r"""^\s*-\s+'?
        (?P<role>[A-Za-z][\w-]*)
        (?:\s+(?P<quoted>"(?:\\.|[^"\\])*"))?
        (?P<attrs>(?:\s+\[[^\]]+\])*)
        \s*'?
        (?:\s*:\s*(?P<trailing>.*))?
        \s*$""",
    re.VERBOSE,
)
_A11Y_URL_LINE_RE = re.compile(r"^\s*-\s*/url\s*:", re.IGNORECASE)
_A11Y_MAIN_START_RE = re.compile(
    r"^\s*-\s+'?(?:main|article)\b",
    re.IGNORECASE,
)
# 仅在页脚地标结束；breadcrumb 等 navigation 常嵌在 article 内，不能当结束符
_A11Y_MAIN_END_RE = re.compile(
    r"^\s*-\s+'?(?:contentinfo)\b",
    re.IGNORECASE,
)

# 始终丢弃的 chrome / 控件角色
_A11Y_CHROME_ROLES = frozenset(
    {
        "navigation",
        "banner",
        "complementary",
        "contentinfo",
        "menu",
        "menuitem",
        "menubar",
        "toolbar",
        "tablist",
        "tab",
        "button",
        "textbox",
        "searchbox",
        "checkbox",
        "radio",
        "switch",
        "slider",
        "combobox",
        "listbox",
        "option",
        "dialog",
        "alertdialog",
        "status",
        "progressbar",
        "img",
        "image",
        "link",  # 导航/侧栏链接噪声大；正文靠 paragraph/heading
    }
)
# 结构节点：无尾随文本则跳过
_A11Y_STRUCTURAL_ROLES = frozenset(
    {
        "generic",
        "group",
        "list",
        "listitem",
        "table",
        "row",
        "rowgroup",
        "grid",
        "gridcell",
        "main",
        "article",
        "region",
        "document",
        "presentation",
        "none",
    }
)
# 优先保留的正文角色
_A11Y_CONTENT_ROLES = frozenset(
    {
        "heading",
        "paragraph",
        "text",
        "time",
        "blockquote",
        "caption",
        "cell",
        "columnheader",
        "rowheader",
        "term",
        "definition",
        "code",
        "emphasis",
        "strong",
        "mark",
        "note",
    }
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


def _looks_like_playwright_a11y(text: str) -> bool:
    """判断是否为 Playwright accessibility snapshot 形态。"""

    return _PLAYWRIGHT_A11Y_HINT.search(text) is not None


def _unquote_a11y_name(value: str | None) -> str:
    """去掉 a11y 节点名两侧引号并还原转义。"""

    if not value:
        return ""
    text = value.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return text.replace('\\"', '"').strip()


def _extract_playwright_page_meta(text: str) -> list[str]:
    """提取 Page URL / Title 作为短元数据头。"""

    parts: list[str] = []
    title = _PAGE_TITLE_RE.search(text)
    if title:
        parts.append(f"Title: {title.group(1).strip()}")
    url = _PAGE_URL_RE.search(text)
    if url:
        parts.append(f"URL: {url.group(1).strip()}")
    return parts


def _slice_playwright_main_region(lines: list[str]) -> list[str]:
    """若存在 main/article，截取到下一 chrome 地标或文末。"""

    start: int | None = None
    for index, line in enumerate(lines):
        if _A11Y_MAIN_START_RE.match(line):
            start = index
            break
    if start is None:
        return lines

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _A11Y_MAIN_END_RE.match(lines[index]):
            end = index
            break
    return lines[start:end]


def _extract_a11y_line_text(line: str) -> str | None:
    """从单行 a11y 节点抽取可读文本；噪声行返回 None。"""

    stripped = line.strip()
    if not stripped or stripped.startswith("```"):
        return None
    if _A11Y_URL_LINE_RE.match(stripped):
        return None
    if stripped.startswith("###"):
        return None

    match = _A11Y_ROLE_LINE_RE.match(stripped)
    if match is None:
        # 非角色行（如 "text: ..." 已被 role=text 覆盖）；保留较长纯文本
        if stripped.startswith("-"):
            return None
        if len(stripped) >= 20 and not stripped.startswith("["):
            return stripped
        return None

    role = match.group("role").lower()
    quoted = _unquote_a11y_name(match.group("quoted"))
    trailing = (match.group("trailing") or "").strip().strip("'").strip()
    if trailing.startswith('"') and trailing.endswith('"') and len(trailing) >= 2:
        trailing = trailing[1:-1].strip()

    if role in _A11Y_CHROME_ROLES:
        return None

    text = trailing or quoted
    if not text:
        return None

    if role in _A11Y_STRUCTURAL_ROLES and len(text) < 40:
        return None

    if role in _A11Y_CONTENT_ROLES or role in _A11Y_STRUCTURAL_ROLES or len(text) >= 40:
        return text
    return None


def extract_playwright_readable_text(text: str) -> str:
    """把 Playwright snapshot / 纯文本压成报告可用的可读正文。"""

    if not text or not text.strip():
        return ""
    if not _looks_like_playwright_a11y(text):
        return text

    meta = _extract_playwright_page_meta(text)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    region = _slice_playwright_main_region(lines)

    extracted: list[str] = []
    seen: set[str] = set()
    for line in region:
        piece = _extract_a11y_line_text(line)
        if not piece:
            continue
        # 跳过与 Title 元数据完全重复的首个超长 heading
        if piece in seen:
            continue
        seen.add(piece)
        extracted.append(piece)

    if not extracted:
        # main 切片过严或角色未命中时，回退全量抽取
        for line in lines:
            piece = _extract_a11y_line_text(line)
            if not piece or piece in seen:
                continue
            seen.add(piece)
            extracted.append(piece)

    if not extracted:
        return text

    body = "\n".join(extracted)
    if meta:
        return "\n".join([*meta, "", body])
    return body


def clean_playwright_body(value: Any, *, max_chars: int) -> str:
    """清洗 Playwright 页面正文：a11y 角色过滤 / 去导航噪声，再截断。"""

    text = normalize_whitespace(unwrap_text_content(value))
    if not text:
        return ""
    text = extract_playwright_readable_text(text)
    text = normalize_whitespace(text)
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
