"""证据长文落盘与读取。

长文写入 `outputs/evidence/{evidence_id}.txt`，Graph State 只保留路径与短摘要。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config.settings import get_project_root
from src.artifacts.tool_content_cleanup import normalize_whitespace


EVIDENCE_CONTENT_DIRNAME = "evidence"
DEFAULT_SNIPPET_MAX_CHARS = 500


def evidence_content_dir() -> Path:
    """返回证据长文目录（位于 outputs/evidence）。"""

    path = get_project_root() / "outputs" / EVIDENCE_CONTENT_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def shorten_snippet(text: str, *, max_chars: int = DEFAULT_SNIPPET_MAX_CHARS) -> str:
    """生成进入 State 的短摘要。"""

    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars] + "...[truncated]"


def persist_evidence_content(evidence_id: str, content: str) -> str:
    """把证据正文写入 outputs/evidence，返回相对项目根的路径。"""

    text = content.strip()
    if not text:
        raise ValueError(f"证据 {evidence_id} 正文为空，无法落盘")
    relative = Path("outputs") / EVIDENCE_CONTENT_DIRNAME / f"{evidence_id}.txt"
    absolute = get_project_root() / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(text, encoding="utf-8")
    return relative.as_posix()


def load_evidence_content(content_path: str | None) -> str:
    """按相对或绝对路径读取证据正文；缺失时返回空串。"""

    if not isinstance(content_path, str) or not content_path.strip():
        return ""
    path = Path(content_path)
    if not path.is_absolute():
        path = get_project_root() / path
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def extract_candidate_long_text(candidate: dict[str, Any]) -> str:
    """从候选中提取应落盘的长文：body > docs_result > resolve_result。

    提取后做轻量空白规范化；来源侧应已完成针对性清洗。
    """

    for key in ("body", "docs_result", "resolve_result"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_whitespace(value)
    return ""
