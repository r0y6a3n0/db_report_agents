# db_report_agents
📊 db_report_agents

A small Python app that demonstrates a multi-agent MCP-style architecture:
	•	Coordinator: Parses natural language prompts, decides which agent/tool to call.
	•	Retrieval Agent: Handles all database-related operations (SQLite: returns.db).
	•	Report Agent: Generates Excel reports from return data.

⸻

🚀 Features
	•	Natural language query → automatically mapped to SQL actions
	•	Example: “新增退貨 laptop” → insert into DB
	•	Example: “查詢 9 月的退貨紀錄” → SQL query
	•	List return records
	•	Example: “列出所有退貨” → fetch rows
	•	CSV ingestion
	•	Example: “匯入 sample.csv” → loads initial dataset into returns.db
	•	Generate Excel reports
	•	Example: “生成 Excel 報表” → creates a multi-sheet Excel file with:
	•	Raw Data
	•	Summary
	•	Findings (AI-generated insights)
