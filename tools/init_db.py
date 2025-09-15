import sqlite3

def init_db(db_path="returns.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 重新建立 returns table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            product TEXT NOT NULL,
            store TEXT NOT NULL,
            date TEXT NOT NULL
        )
    ''')

    conn.commit()
    conn.close()
    print(f"Initialized database at {db_path}.")

if __name__ == "__main__":
    init_db()
