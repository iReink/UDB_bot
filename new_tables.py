# new_tables.py
import sqlite3
from contextlib import closing

DB_PATH = "stats.db"

def create_settings_table():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                chat_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                value INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, name)
            )
        """)
        conn.commit()
        print("Таблица settings создана или уже существует.")

if __name__ == "__main__":
    create_settings_table()
