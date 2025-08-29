import sqlite3

DB_FILE = "stats.db"

def init_mujlo_table():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Создаем таблицу mujlo
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mujlo (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            mujlo_freed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
    """)

    # Получаем всех юзеров из таблицы users
    cur.execute("SELECT chat_id, user_id FROM users")
    rows = cur.fetchall()

    # Заполняем таблицу mujlo начальными значениями
    for row in rows:
        cur.execute("""
            INSERT OR IGNORE INTO mujlo (chat_id, user_id, mujlo_freed)
            VALUES (?, ?, 0)
        """, (row["chat_id"], row["user_id"]))

    conn.commit()
    conn.close()
    print(f"Таблица mujlo инициализирована, записей добавлено: {len(rows)}")

if __name__ == "__main__":
    init_mujlo_table()
