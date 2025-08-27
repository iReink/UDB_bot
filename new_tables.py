import sqlite3
import os

DB_PATH = "/root/UDB_bot/stats.db"

def run_migrations():
    if not os.path.exists(DB_PATH):
        print(f"Ошибка: База данных {DB_PATH} не найдена.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Включаем внешние ключи (для ссылок между таблицами)
    cur.execute("PRAGMA foreign_keys = ON;")

    # 1. Таблица achievements (справочник ачивок)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS achievements (
        key TEXT PRIMARY KEY,
        name_male TEXT NOT NULL,
        name_female TEXT NOT NULL
    )
    """)

    # 2. Таблица user_achievements (лог получения ачивок)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_achievements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        achievement_key TEXT NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (achievement_key) REFERENCES achievements (key)
    )
    """)

    conn.commit()
    conn.close()
    print("✅ Новые таблицы успешно добавлены/проверены.")

if __name__ == "__main__":
    run_migrations()
