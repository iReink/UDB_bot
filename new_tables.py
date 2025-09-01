# create_sticker_stats_table.py
import sqlite3

DB_FILE = "stats.db"

def create_sticker_stats_table():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sticker_stats (
            chat_id    INTEGER NOT NULL,
            file_id    TEXT NOT NULL,
            set_name   TEXT,
            count      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, file_id)
        )
    """)

    conn.commit()
    conn.close()
    print("✅ Таблица sticker_stats создана или уже существует.")

if __name__ == "__main__":
    create_sticker_stats_table()
