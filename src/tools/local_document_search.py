"""本地 Markdown 文档只读搜索工具。

从 `LOCAL_DOCUMENTS_BASE_PATH` 指定目录读取和搜索 `.md` 文件。
只提供读能力，不写入、不删除、不修改文件。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import jieba
from langchain.tools import tool

from src.config.settings import _read_required_env


MAX_FILE_SIZE_BYTES = 1_000_000
MAX_RESULTS = 8
SNIPPET_RADIUS = 180
_PUNCTUATION_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)
_STOPWORDS = {
    "的",
    "了",
    "和",
    "与",
    "及",
    "或",
    "在",
    "是",
    "有",
    "为",
    "对",
    "等",
    "中",
    "上",
    "下",
    "也",
    "就",
    "都",
    "而",
    "把",
    "被",
    "从",
    "到",
    "以",
    "及其",
}


def _base_path() -> Path:
    """读取并校验本地文档根目录。"""

    raw_path = _read_required_env("LOCAL_DOCUMENTS_BASE_PATH")
    base = Path(raw_path).expanduser().resolve()
    if not base.exists():
        raise FileNotFoundError(f"LOCAL_DOCUMENTS_BASE_PATH 不存在：{base}")
    if not base.is_dir():
        raise NotADirectoryError(f"LOCAL_DOCUMENTS_BASE_PATH 不是目录：{base}")
    return base


def _safe_markdown_files(base: Path) -> list[Path]:
    """列出 base 下的 Markdown 文件。"""

    files = []
    for path in base.rglob("*.md"):
        resolved = path.resolve()
        if not resolved.is_file():
            continue
        if base not in resolved.parents and resolved != base:
            raise RuntimeError(f"检测到越权路径：{resolved}")
        if resolved.stat().st_size > MAX_FILE_SIZE_BYTES:
            continue
        files.append(resolved)
    return sorted(files)


def _extract_snippet(content: str, query_terms: list[str]) -> str:
    """提取包含查询词的片段。"""

    lowered = content.lower()
    hit_positions = [
        lowered.find(term.lower())
        for term in query_terms
        if term and lowered.find(term.lower()) >= 0
    ]
    if not hit_positions:
        snippet = content[: SNIPPET_RADIUS * 2]
    else:
        center = min(hit_positions)
        start = max(center - SNIPPET_RADIUS, 0)
        end = min(center + SNIPPET_RADIUS, len(content))
        snippet = content[start:end]
    return " ".join(snippet.split())


def _tokenize_query(query: str) -> list[str]:
    """根据上游 search task 的 query 做 jieba 搜索分词。"""

    normalized = query.replace("，", " ").replace(",", " ").strip()
    if not normalized:
        return []

    terms: list[str] = []
    seen: set[str] = set()
    for term in jieba.lcut_for_search(normalized):
        cleaned = term.strip()
        if not cleaned:
            continue
        if _PUNCTUATION_ONLY.match(cleaned):
            continue
        if cleaned in _STOPWORDS:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(cleaned)
    return terms


def search_markdown_documents(query: str, limit: int = MAX_RESULTS) -> list[dict[str, Any]]:
    """搜索本地 Markdown 文档。"""

    base = _base_path()
    query_terms = _tokenize_query(query)
    if not query_terms:
        raise ValueError("本地文档搜索 query 不能为空")

    results = []
    for path in _safe_markdown_files(base):
        content = path.read_text(encoding="utf-8", errors="ignore")
        lowered_content = content.lower()
        score = sum(1 for term in query_terms if term.lower() in lowered_content)
        if score <= 0:
            continue

        relative_path = path.relative_to(base)
        title = path.stem
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip() or title
                break

        results.append(
            {
                "path": str(path),
                "relative_path": str(relative_path),
                "title": title,
                "snippet": _extract_snippet(content, query_terms),
                "score": score,
                "source_name": "local_markdown_documents",
            }
        )

    results.sort(key=lambda item: (-item["score"], item["relative_path"]))
    return results[: max(1, min(limit, MAX_RESULTS))]


@tool
def local_markdown_search(query: str) -> str:
    """只读搜索 LOCAL_DOCUMENTS_BASE_PATH 下的 Markdown 文件。"""

    results = search_markdown_documents(query=query)
    if not results:
        return "未在本地 Markdown 文档中找到匹配内容。"
    return "\n".join(
        f"{item['relative_path']}｜{item['title']}｜{item['snippet']}"
        for item in results
    )


def query_local_documents_for_task(task: dict[str, Any]) -> list[dict[str, Any]]:
    """按 SearchTask 查询本地 Markdown 文档并返回工具结果。"""

    rows = search_markdown_documents(query=task["query"])
    results = []
    for index, row in enumerate(rows, start=1):
        results.append(
            {
                "result_id": f"R_local_{task['task_id']}_{index}",
                "task_id": task["task_id"],
                "question_id": task["question_id"],
                "source_type": task["source_type"],
                "source_name": row["source_name"],
                "url_or_path": row["path"],
                "title": row["title"],
                "snippet": row["snippet"],
                "published_at": None,
                "collected_by": "local_document_search",
                "is_placeholder": False,
                "requires_login": False,
                "blocked_reason": None,
                "local_payload": {
                    "relative_path": row["relative_path"],
                    "score": row["score"],
                },
            }
        )
    return results
