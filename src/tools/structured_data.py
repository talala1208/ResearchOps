"""结构化 mock 数据搜索工具。

参考 `reference/bonus_sql.ipynb` 的思路：使用本地 SQLite 数据库和一个
可调用的查询工具。当前工具只查询项目内 `data/mock/ai_products.sqlite`，
不访问网络，也不执行外部写入。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from src.config.settings import get_project_root
from src.llm.chat import build_chat_model
from src.llm.prompt_loader import load_prompt, render_prompt_template
from src.llm.structured_outputs import LocalStructuredSearchQueryOutput


DB_PATH = get_project_root() / "data" / "mock" / "ai_products.sqlite"

SEED_PRODUCTS_PATH = get_project_root() / "data" / "mock" / "ai_products.json"


def _structured_tool_config() -> dict[str, Any]:
    """读取本地结构化搜索 YAML 配置。"""

    return load_prompt("local_structured_search.yml")


def load_seed_products() -> list[dict[str, str]]:
    """从 JSON 文件读取结构化 mock 种子数据。"""

    if not SEED_PRODUCTS_PATH.exists():
        raise FileNotFoundError(f"结构化 mock 种子数据不存在：{SEED_PRODUCTS_PATH}")

    payload = json.loads(SEED_PRODUCTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("结构化 mock 种子数据必须是列表")

    required_fields = {
        "name",
        "category",
        "description",
        "pricing",
        "features",
        "target_users",
        "competitors",
        "source_name",
    }
    products = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 条结构化 mock 数据必须是对象")
        missing_fields = required_fields - set(item)
        if missing_fields:
            raise ValueError(
                f"第 {index} 条结构化 mock 数据缺少字段：{sorted(missing_fields)}"
            )
        products.append({field: str(item[field]) for field in required_fields})
    return products


def ensure_mock_database() -> Path:
    """确保 mock SQLite 数据库存在并完成基础数据初始化。"""

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                pricing TEXT NOT NULL,
                features TEXT NOT NULL,
                target_users TEXT NOT NULL,
                competitors TEXT NOT NULL,
                source_name TEXT NOT NULL
            )
            """
        )
        for product in load_seed_products():
            conn.execute(
                """
                INSERT OR IGNORE INTO ai_products (
                    name, category, description, pricing, features,
                    target_users, competitors, source_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product["name"],
                    product["category"],
                    product["description"],
                    product["pricing"],
                    product["features"],
                    product["target_users"],
                    product["competitors"],
                    product["source_name"],
                ),
            )
        conn.commit()
    return DB_PATH


def search_ai_products(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """用参数化 SQL 查询 AI 产品 mock 表。"""

    return search_ai_products_by_terms(
        [term.strip() for term in query.replace("，", " ").split() if term.strip()]
        or ([query.strip()] if query.strip() else [""]),
        limit=limit,
    )


def search_ai_products_by_terms(
    search_terms: list[str],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """用 LLM 规划出的关键词执行参数化 SQL 查询。"""

    db_path = ensure_mock_database()
    normalized_limit = max(1, min(limit, 20))
    terms = [term.strip() for term in search_terms if term.strip()]
    if not terms:
        raise ValueError("结构化资料搜索关键词不能为空")

    where_parts = []
    params: list[str | int] = []
    for term in terms:
        pattern = f"%{term}%"
        where_parts.append(
            """
            (
                name LIKE ? OR category LIKE ? OR description LIKE ? OR
                pricing LIKE ? OR features LIKE ? OR target_users LIKE ? OR
                competitors LIKE ?
            )
            """
        )
        params.extend([pattern] * 7)

    sql = f"""
        SELECT
            id, name, category, description, pricing, features,
            target_users, competitors, source_name
        FROM ai_products
        WHERE {' OR '.join(where_parts)}
        ORDER BY name ASC
        LIMIT ?
    """
    params.append(normalized_limit)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()

    return [dict(row) for row in rows]


@tool
def structured_ai_product_search(query: str) -> str:
    """查询本地 AI 产品结构化 mock 数据。"""

    rows = search_ai_products(query=query, limit=5)
    if not rows:
        return "未在本地结构化 mock 数据中找到匹配产品。"
    return "\n".join(
        f"{row['name']}｜{row['category']}｜{row['pricing']}｜{row['features']}"
        for row in rows
    )


def _plan_structured_search_query(task: dict[str, Any]) -> LocalStructuredSearchQueryOutput:
    """用本地资料检索共用 LLM 规划结构化搜索语句。"""

    prompt = _structured_tool_config()
    user_prompt = render_prompt_template(
        prompt["user_prompt_template"],
        {
            "task_id": task["task_id"],
            "question_id": task["question_id"],
            "source_type": task["source_type"],
            "query": task["query"],
        },
    )
    model = build_chat_model("local_document_search").with_structured_output(
        LocalStructuredSearchQueryOutput
    )
    return model.invoke(
        [
            ("system", prompt["system_prompt"]),
            ("human", user_prompt),
        ]
    )


def query_structured_products_for_task(task: dict[str, Any]) -> list[dict[str, Any]]:
    """按 SearchTask 查询结构化 mock 数据并返回工具结果。"""

    structured_query = _plan_structured_search_query(task)
    rows = search_ai_products_by_terms(
        search_terms=structured_query.search_terms,
        limit=5,
    )
    results = []
    for row in rows:
        result_id = f"R_structured_{task['task_id']}_{row['id']}"
        config = _structured_tool_config()
        snippet = str(config["snippet_template"]).format(**row)
        results.append(
            {
                "result_id": result_id,
                "task_id": task["task_id"],
                "question_id": task["question_id"],
                "source_type": task["source_type"],
                "source_name": row["source_name"],
                "url_or_path": str(DB_PATH),
                "title": f"结构化产品数据：{row['name']}",
                "snippet": snippet,
                "published_at": None,
                "collected_by": "local_structured_search",
                "requires_login": False,
                "blocked_reason": None,
                "structured_payload": {
                    **row,
                    "search_terms": structured_query.search_terms,
                    "sql_search_statement": structured_query.sql_search_statement,
                    "search_reasoning": structured_query.reasoning,
                },
            }
        )
    return results
