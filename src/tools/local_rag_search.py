"""本地向量 RAG 只读检索工具。

参考 `reference/app.py`：用 DashScope embedding + SKLearnVectorStore
加载预构建 parquet 向量库，按 query 检索相关文档 chunk。
只返回检索结果作为证据候选，不在工具内生成最终回答。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from langchain_community.vectorstores import SKLearnVectorStore
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.tools import tool
from openai import OpenAI

from src.config.settings import (
    _read_optional_env,
    _read_required_env,
    get_project_root,
)


DEFAULT_PERSIST_RELATIVE = Path("data") / "resources" / "union.parquet"
DEFAULT_EMBED_MODEL = "text-embedding-v3"
DEFAULT_TOP_K = 4
DEFAULT_EMBED_BATCH_SIZE = 10
MAX_EMBED_BATCH_SIZE = 10
MAX_SNIPPET_CHARS = 500
MAX_BODY_CHARS = 4000

_retriever_lock = threading.Lock()
_retriever_cache: dict[str, Any] = {}


class DashScopeEmbeddings(Embeddings):
    """用 OpenAI 兼容 SDK 直连 DashScope embedding。"""

    def __init__(
        self,
        client: OpenAI,
        model: str = DEFAULT_EMBED_MODEL,
        batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
    ) -> None:
        self._client = client
        self.model = model
        self.batch_size = max(1, min(batch_size, MAX_EMBED_BATCH_SIZE))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for index in range(0, len(texts), self.batch_size):
            batch = texts[index : index + self.batch_size]
            response = self._client.embeddings.create(model=self.model, input=batch)
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(item.embedding for item in ordered)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def _persist_path() -> Path:
    """解析本地 RAG 向量库路径。"""

    raw = _read_optional_env("LOCAL_RAG_PERSIST_PATH")
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = get_project_root() / path
    else:
        path = get_project_root() / DEFAULT_PERSIST_RELATIVE
    return path.resolve()


def _embed_model() -> str:
    return _read_optional_env("LOCAL_RAG_EMBED_MODEL") or DEFAULT_EMBED_MODEL


def _top_k() -> int:
    raw = _read_optional_env("LOCAL_RAG_TOP_K")
    if raw is None:
        return DEFAULT_TOP_K
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"LOCAL_RAG_TOP_K 必须是整数，当前值：{raw}") from exc
    if value < 1:
        raise RuntimeError(f"LOCAL_RAG_TOP_K 必须 ≥ 1，当前值：{value}")
    return value


def _embed_batch_size() -> int:
    raw = _read_optional_env("LOCAL_RAG_EMBED_BATCH_SIZE")
    if raw is None:
        return DEFAULT_EMBED_BATCH_SIZE
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"LOCAL_RAG_EMBED_BATCH_SIZE 必须是整数，当前值：{raw}"
        ) from exc
    if value < 1:
        raise RuntimeError(f"LOCAL_RAG_EMBED_BATCH_SIZE 必须 ≥ 1，当前值：{value}")
    return min(value, MAX_EMBED_BATCH_SIZE)


def _build_embeddings() -> DashScopeEmbeddings:
    """创建 DashScope embedding 客户端。"""

    api_key = _read_required_env("DASHSCOPE_API_KEY")
    base_url = _read_optional_env("DASHSCOPE_BASE_URL")
    client = OpenAI(api_key=api_key, base_url=base_url)
    return DashScopeEmbeddings(
        client=client,
        model=_embed_model(),
        batch_size=_embed_batch_size(),
    )


def get_local_rag_retriever(*, force_reload: bool = False):
    """加载预构建向量库并返回 retriever；缺失文件时显式失败。"""

    persist_path = _persist_path()
    cache_key = str(persist_path)
    with _retriever_lock:
        if not force_reload and cache_key in _retriever_cache:
            return _retriever_cache[cache_key]
        if not persist_path.exists():
            raise FileNotFoundError(
                f"本地 RAG 向量库不存在：{persist_path}。"
                "请准备 parquet 文件或设置 LOCAL_RAG_PERSIST_PATH。"
            )
        if not persist_path.is_file():
            raise IsADirectoryError(f"LOCAL_RAG_PERSIST_PATH 不是文件：{persist_path}")

        vectorstore = SKLearnVectorStore(
            embedding=_build_embeddings(),
            persist_path=str(persist_path),
            serializer="parquet",
        )
        retriever = vectorstore.as_retriever(
            search_kwargs={"k": _top_k()},
            lambda_mult=0,
        )
        _retriever_cache[cache_key] = retriever
        return retriever


def clear_local_rag_retriever_cache() -> None:
    """测试用：清空 retriever 缓存。"""

    with _retriever_lock:
        _retriever_cache.clear()


def _document_url(metadata: dict[str, Any]) -> str | None:
    for key in ("source", "loc", "url"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _document_title(content: str, metadata: dict[str, Any], url: str | None) -> str:
    for key in ("title",):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip() and not value.startswith("http"):
            return value.strip()[:200]

    def _clean_heading(text: str) -> str:
        compact = " ".join(text.split())
        if " - Docs by " in compact:
            return compact.split(" - Docs by ", 1)[0][:200]
        return compact[:200]

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return _clean_heading(heading)
        if stripped and not stripped.lower().startswith("skip to"):
            return _clean_heading(stripped)

    if url:
        path = unquote(urlparse(url).path).rstrip("/")
        if path:
            return path.rsplit("/", 1)[-1] or url
    return "local_rag_chunk"


def _truncate(text: str, max_chars: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars] + "...[truncated]"


def retrieve_local_rag_documents(query: str, *, k: int | None = None) -> list[Document]:
    """按 query 检索本地向量库文档。"""

    normalized = query.strip()
    if not normalized:
        raise ValueError("本地 RAG 检索 query 不能为空")

    retriever = get_local_rag_retriever()
    top_k = k if k is not None else _top_k()
    if hasattr(retriever, "search_kwargs"):
        retriever.search_kwargs = {**getattr(retriever, "search_kwargs", {}), "k": top_k}
    documents = retriever.invoke(normalized)
    if not isinstance(documents, list):
        raise TypeError(f"本地 RAG retriever 返回类型异常：{type(documents)!r}")
    return documents


def search_local_rag(query: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """检索本地 RAG 并规范化为工具结果行。"""

    documents = retrieve_local_rag_documents(query, k=limit)
    rows: list[dict[str, Any]] = []
    for index, document in enumerate(documents, start=1):
        content = (document.page_content or "").strip()
        if not content:
            continue
        metadata = document.metadata if isinstance(document.metadata, dict) else {}
        url = _document_url(metadata)
        title = _document_title(content, metadata, url)
        rows.append(
            {
                "title": title,
                "snippet": _truncate(content, MAX_SNIPPET_CHARS),
                "body": _truncate(content, MAX_BODY_CHARS),
                "url_or_path": url or f"local_rag://chunk/{index}",
                "source_name": "local_langsmith_docs_rag",
                "score": None,
                "metadata": {
                    key: value
                    for key, value in metadata.items()
                    if isinstance(value, (str, int, float, bool)) or value is None
                },
            }
        )
    return rows


@tool
def local_rag_search(query: str) -> str:
    """只读检索本地 LangSmith docs 向量库，返回相关文档片段。"""

    rows = search_local_rag(query=query)
    if not rows:
        return "未在本地 RAG 向量库中找到匹配内容。"
    return "\n".join(
        f"{item['title']}｜{item['url_or_path']}｜{item['snippet']}" for item in rows
    )


def query_local_rag_for_task(task: dict[str, Any]) -> list[dict[str, Any]]:
    """按 SearchTask 查询本地 RAG 并返回工具结果。"""

    rows = search_local_rag(query=task["query"])
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        results.append(
            {
                "result_id": f"R_local_rag_{task['task_id']}_{index}",
                "task_id": task["task_id"],
                "question_id": task["question_id"],
                "source_type": task["source_type"],
                "source_name": row["source_name"],
                "url_or_path": row["url_or_path"],
                "title": row["title"],
                "snippet": row["snippet"],
                "body": row["body"],
                "published_at": None,
                "collected_by": "local_rag_search",
                "requires_login": False,
                "blocked_reason": None,
                "local_payload": {
                    "score": row["score"],
                    "metadata": row["metadata"],
                },
            }
        )
    return results
