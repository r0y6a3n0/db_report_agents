from fastmcp import FastMCP
import sqlite3
import pandas as pd
import uuid
import os
from typing import Dict, Any
from dotenv import load_dotenv
import logging

from langchain_openai import ChatOpenAI
from langchain_community.utilities import SQLDatabase
from langchain_experimental.sql.base import SQLDatabaseChain
from sqlalchemy import create_engine

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("retrieval-agent")

# ---------- 初始化 ----------
DB_PATH = os.getenv("DB_PATH", "/tmp/returns.db")
mcp = FastMCP("Retrieval Agent")

load_dotenv()

# 全域變數，延遲初始化
_llm = None
_db_chain = None


def get_llm():
    """延後建立 LLM，確保 container 可以先啟動"""
    global _llm
    if _llm is None:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise RuntimeError("請先設定 GITHUB_TOKEN")
        _llm = ChatOpenAI(
            api_key=token,
            base_url="https://models.github.ai/inference",
            model="openai/gpt-4o-mini",
            temperature=0
        )
        logger.info("[INIT] LLM 已成功建立")
    return _llm


def get_db_chain():
    """延後建立 SQLDatabaseChain"""
    global _db_chain
    if _db_chain is None:
        try:
            engine = create_engine(
                f"sqlite:///{DB_PATH}",
                connect_args={"check_same_thread": False}
            )
            db = SQLDatabase(engine)
            _db_chain = SQLDatabaseChain.from_llm(
                get_llm(),
                db,
                verbose=True,
                return_direct=True
            )
            logger.info(f"[INIT] 成功載入 SQLDatabaseChain, DB_PATH={DB_PATH}")
        except Exception as e:
            logger.error(f"[INIT] 初始化 SQLDatabaseChain 失敗: {e}", exc_info=True)
            _db_chain = None
    return _db_chain


# ---------- 工具 ----------
@mcp.tool(name="ingest_csv")
def ingest_csv(path: str) -> Dict[str, Any]:
    """
    匯入 CSV 並建立 SQLite 資料表。
    - 如果表不存在就建立。
    - 匯入前會清空舊資料，避免重複插入。
    """
    try:
        if not os.path.exists(path):
            return {"status": "error", "error": f"CSV 檔案不存在: {path}"}

        df = pd.read_csv(path)

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        # 建立資料表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS returns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                product TEXT,
                store TEXT,
                date TEXT
            )
        """)

        # 清空舊資料
        cur.execute("DELETE FROM returns")

        # 插入新資料
        for _, row in df.iterrows():
            order_id = str(row.get("order_id", str(uuid.uuid4())[:8]))
            product = row.get("product", "")
            store = row.get("store", "")
            date = row.get("date", "")

            cur.execute(
                "INSERT INTO returns (order_id, product, store, date) VALUES (?, ?, ?, ?)",
                (order_id, product, store, date)
            )

        conn.commit()
        conn.close()

        return {"status": "ok", "rows_inserted": len(df)}

    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool(name="query_db")
def query_db(prompt: str) -> Dict[str, Any]:
    """
    使用自然語言操作 SQLite (查詢 / 新增 / 更新 / 刪除)
    """
    chain = get_db_chain()
    if chain is None:
        return {"status": "error", "error": "SQLDatabaseChain 未初始化成功"}

    try:
        result = chain.invoke(prompt)
        logger.info(f"[query_db] 執行 prompt: {prompt} → result: {str(result)[:200]}")
        return {"status": "ok", "prompt": prompt, "result": result}
    except Exception as e:
        logger.error(f"[query_db] 發生錯誤: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


@mcp.tool(name="list_returns")
def list_returns(limit: int = 500) -> Dict[str, Any]:
    """
    直接查 returns 表，回傳結構化 rows
    """
    try:
        limit = int(limit)
        logger.info(f"[list_returns] 開始查詢，limit={limit}, DB_PATH={DB_PATH}")

        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()

            # 檢查表是否存在
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='returns'")
            table_exists = cur.fetchone()
            if not table_exists:
                logger.warning(f"[list_returns] returns 表不存在")
                return {"status": "error", "error": "returns 表不存在", "db_path": DB_PATH}

            # 計算總數
            cur.execute("SELECT COUNT(*) FROM returns")
            total = cur.fetchone()[0]
            logger.info(f"[list_returns] returns 表共有 {total} 筆資料")

            # 查詢資料
            cur.execute("SELECT order_id, product, store, date FROM returns LIMIT ?", (limit,))
            rows = cur.fetchall()

        logger.info(f"[list_returns] 查詢完成，取得 {len(rows)} 筆, 前 2 筆: {rows[:2]}")

        result = [
            {"order_id": r[0], "product": r[1], "store": r[2], "date": r[3]}
            for r in rows
        ]

        return {
            "status": "ok",
            "rows": result,
            "count": len(result),
            "db_path": DB_PATH
        }

    except Exception as e:
        logger.error(f"[list_returns] 發生錯誤: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


# ---------- 啟動 ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))  # 統一用 8080 作 fallback
    logger.info(f"[MAIN] Retrieval Agent 啟動中... DB_PATH={DB_PATH}, PORT={port}")
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")

