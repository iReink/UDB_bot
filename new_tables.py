# new_tables.py
import sqlite3

DB_FILE = "stats.db"  # замени на путь к своей БД, если другой

def create_sosalsa_table():
    """Создаёт таблицу для статистики сосания/шпёха, если её ещё нет."""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sosalsa_stats (
            chat_id INTEGER NOT NULL,
            user_id1 INTEGER NOT NULL,
            user_id2 INTEGER NOT NULL,
            sosalsa_count INTEGER NOT NULL DEFAULT 0,
            shpehalsa_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id1, user_id2)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sosalsa_chat ON sosalsa_stats(chat_id)")
    conn.commit()
    conn.close()
    print("✅ Таблица sosalsa_stats создана или уже существует.")

if __name__ == "__main__":
    create_sosalsa_table()
