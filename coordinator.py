import os
import json
import asyncio
import logging
import sqlite3
import base64
import tempfile

from fastmcp import Client
from fastmcp.client.client import CallToolResult
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------- 環境設定 ----------
RETRIEVAL_URL = os.getenv("RETRIEVAL_URL", "")
REPORT_URL    = os.getenv("REPORT_URL", "")
CSV_PATH      = "sample.csv"

load_dotenv()
token = os.getenv("GITHUB_TOKEN")
if not token:
    raise RuntimeError("請先設定 GITHUB_TOKEN")

# ---------- 初始化 LLM ----------
llm = ChatOpenAI(
    api_key=token,
    base_url="https://models.github.ai/inference",
    model="openai/gpt-4o-mini",
    temperature=0.0,
    response_format={"type": "json_object"}
)

# ---------- unwrap 工具 ----------
def unwrap_rows(resp):
    if isinstance(resp, CallToolResult):
        sc = resp.structured_content
        if isinstance(sc, dict):
            if "rows" in sc:
                return sc["rows"]
            if "result" in sc and isinstance(sc["result"], list):
                return sc["result"]
        if isinstance(sc, list):
            return sc
    elif isinstance(resp, dict):
        return resp.get("rows") or resp.get("result", [])
    elif isinstance(resp, list):
        return resp
    return []

# ---------- LLM 意圖解析 ----------
def interpret_with_llm(prompt: str) -> dict:
    system_msg = """
    你是一個任務選擇器，負責將使用者輸入轉換成工具調用指令。

    ### 規則
    - "新增/插入/查詢/商店退貨數量" → query_db
    - "列出所有退貨/顯示所有退貨紀錄/全部退貨資料" → list_returns
    - "產生/生成/請出 Excel 報表" → generate_report
    - "匯入 CSV/載入 sample.csv" → ingest_csv

    ⚠️ 格式要求:
    - 必須輸出 JSON: {"tool": "<工具名稱>", "args": {...}}
    - 工具名稱只能是: ingest_csv, query_db, list_returns, generate_report, none
    """
    resp = llm.invoke([
        ("system", system_msg),
        ("user", prompt)
    ])
    try:
        return json.loads(resp.content)
    except Exception:
        return {"tool": "none", "args": {}, "error": "無法解析需求"}

# ---------- 協調器 ----------
async def process_request(prompt: str):
    logger.info(f"[Coordinator] 收到請求: {prompt}")

    plan = interpret_with_llm(prompt)
    logger.info(f"[Coordinator] LLM 規劃結果: {plan}")

    tool = plan.get("tool")
    args = plan.get("args", {})

    if tool == "ingest_csv":
        async with Client(RETRIEVAL_URL) as c:
            result = await c.call_tool("ingest_csv", {"path": CSV_PATH})
        return {"status": "ok", "message": result}

    elif tool == "query_db":
        if "prompt" not in args:
            args["prompt"] = prompt
        async with Client(RETRIEVAL_URL) as c:
            resp = await c.call_tool("query_db", args)
        return resp.structured_content if isinstance(resp, CallToolResult) else resp

    elif tool == "list_returns":
        async with Client(RETRIEVAL_URL) as c:
            resp = await c.call_tool("list_returns", args)
            rows = unwrap_rows(resp)
        logger.info(f"[Coordinator] list_returns result: {len(rows)} rows")
        return {"status": "ok", "rows": rows}

    elif tool == "generate_report":
        async with Client(RETRIEVAL_URL) as c:
            resp = await c.call_tool("list_returns", {"limit": 500})
            rows = unwrap_rows(resp)

        if not rows:
            return {"status": "error", "message": "無資料，無法生成報表"}

        async with Client(REPORT_URL) as r:
            rep = await r.call_tool("generate_excel_report", {"rows": rows})
        return rep.structured_content if isinstance(rep, CallToolResult) else rep

    else:
        return {"status": "error", "message": f"未知的任務: {plan}"}

# ---------- FastAPI ----------
app = FastAPI(
    title="DB Report Coordinator API",
    description="""
這是一個協調器 API，負責整合 **Retrieval Agent** 與 **Report Agent**。
---
### 可用 API
1. **POST /process**  
   - 輸入: `{"prompt": "文字指令"}`  
   - 範例:  
     ```json
     {"prompt": "列出所有退貨"}
     ```
   - 會自動判斷該呼叫的工具 (query_db / list_returns / ingest_csv / generate_report)

2. **GET /**  
   - 健康檢查，回傳狀態 + 使用教學
   - 也可用 `/docs` (Swagger UI) 或 `/redoc` 查看文件
""",
    version="1.0.0"
)

class PromptRequest(BaseModel):
    prompt: str

@app.post("/process")
async def process_endpoint(req: PromptRequest):
    result = await process_request(req.prompt)

    # 報表 (Base64)
    if isinstance(result, dict) and result.get("status") == "ok" and "file_base64" in result:
        file_bytes = base64.b64decode(result["file_base64"])
        file_name = result.get("file_name", "returns_report.xlsx")
        tmp_path = os.path.join(tempfile.gettempdir(), file_name)

        with open(tmp_path, "wb") as f:
            f.write(file_bytes)

        return FileResponse(
            path=tmp_path,
            filename=file_name,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    return result


# ---------- 啟動 ----------
if __name__ == "__main__":
    async def init_db():
        logger.info("[Coordinator] 檢查 returns.db 是否已有資料")
        try:
            conn = sqlite3.connect("returns.db")
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM returns")
            count = cur.fetchone()[0]
            conn.close()
        except Exception:
            count = 0

        if count > 0:
            logger.info(f"[Coordinator] DB 已有 {count} 筆資料，跳過 ingest_csv")
            return

        logger.info("[Coordinator] DB 為空，觸發 ingest_csv")
        try:
            async with Client(RETRIEVAL_URL) as c:
                resp = await c.call_tool("ingest_csv", {"path": CSV_PATH})
                logger.info(f"[Coordinator] ingest_csv 回應: {resp}")
        except Exception as e:
            logger.error(f"[Coordinator] ingest_csv 失敗: {e}", exc_info=True)

    asyncio.run(init_db())
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("coordinator:app", host="0.0.0.0", port=port)
