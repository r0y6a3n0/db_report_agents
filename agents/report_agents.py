from fastmcp import FastMCP
import pandas as pd
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
import logging
import base64
# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ReportAgent")

# ---------- 初始化 ----------
mcp = FastMCP("Report Agent")

load_dotenv()
token = os.getenv("GITHUB_TOKEN")
if not token:
    raise RuntimeError("請先設定 GITHUB_TOKEN")

llm = ChatOpenAI(
    api_key=token,
    base_url="https://models.github.ai/inference",
    model="openai/gpt-4o-mini",
    temperature=0
)

# Cloud Run 唯一可寫路徑
OUTPUT_FILE = "/tmp/returns_report.xlsx"


# ---------- 工具 ----------
@mcp.tool(name="generate_excel_report")
def generate_excel_report(rows: list) -> dict:
    """
    接收 rows，產生 Excel 報表，包含 Raw Data / Summary / TopN 統計 / Findings
    """
    logger.info("=== ReportAgent 收到呼叫 ===")
    logger.info(f"rows type: {type(rows)}, len: {len(rows) if isinstance(rows, list) else 'N/A'}")

    # 確保 rows 一定是 list
    if isinstance(rows, dict) and "rows" in rows:
        rows = rows["rows"]

    if not isinstance(rows, list):
        return {"status": "error", "message": f"rows 格式錯誤: {type(rows)}"}

    if not rows:
        return {"status": "error", "message": "rows 為空，無法生成報表"}

    logger.info(f"rows sample: {rows[:2]}")

    df = pd.DataFrame(rows)

    # 檢查必要欄位
    required_cols = {"order_id", "product", "store", "date"}
    missing = required_cols - set(df.columns)
    if missing:
        return {"status": "error", "message": f"缺少欄位: {missing}"}

    # Summary
    total_returns = len(df)

    # TopN 統計
    def safe_value_counts(series, name):
        try:
            return series.value_counts().head(5).reset_index(names=[name, "Count"])
        except Exception:
            return pd.DataFrame()

    top_stores = safe_value_counts(df["store"], "Store")
    top_products = safe_value_counts(df["product"], "Product")
    top_dates = safe_value_counts(df["date"], "Date")

    # Findings Prompt
    findings_prompt = f"""
    你是一個數據分析助理，請根據以下退貨統計，生成 2-3 條自然語言觀察：
    ---
    總退貨數: {total_returns}
    Top Stores: {df["store"].value_counts().head(5).to_dict()}
    Top Products: {df["product"].value_counts().head(5).to_dict()}
    Top Dates: {df["date"].value_counts().head(5).to_dict()}
    """
    try:
        resp = llm.invoke([("user", findings_prompt)])
        findings_text = resp.content.strip()
    except Exception as e:
        findings_text = f"無法生成 Findings，錯誤: {e}"

    # 存到 Excel
    try:
        with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter") as writer:
            # 原始資料
            df.to_excel(writer, sheet_name="Raw Data", index=False)

            # Summary
            pd.DataFrame({"Total Returns": [total_returns]}).to_excel(
                writer, sheet_name="Summary", index=False
            )

            # TopN 統計
            if not top_stores.empty:
                top_stores.to_excel(writer, sheet_name="By Store", index=False)
            if not top_products.empty:
                top_products.to_excel(writer, sheet_name="By Product", index=False)
            if not top_dates.empty:
                top_dates.to_excel(writer, sheet_name="By Date", index=False)

            # Findings
            findings_df = pd.DataFrame({"Findings": findings_text.split("\n")})
            findings_df.to_excel(writer, sheet_name="Findings", index=False)

        # 在 Excel 生成成功後
        with open(OUTPUT_FILE, "rb") as f:
            file_bytes = f.read()
            file_b64 = base64.b64encode(file_bytes).decode("utf-8")

        return {
            "status": "ok",
            "file_name": "returns_report.xlsx",
            "file_base64": file_b64,
            "message": "Excel 報表生成成功"
         }
    except Exception as e:
        logger.error(f"生成 Excel 失敗: {e}", exc_info=True)
        return {"status": "error", "message": f"生成 Excel 失敗: {e}"}


# ---------- 啟動 ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"[ReportAgent] 啟動於 port {port}, OUTPUT_FILE={OUTPUT_FILE}")
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")
