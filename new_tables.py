# new_tables.py
import sqlite3
from contextlib import closing

DB_PATH = "stats.db"  # можно поменять под твой путь


def create_tables():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()

        # Таблица дейликов
        cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            creator_user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            date TEXT,
            time TEXT,
            cars TEXT,
            link TEXT
        );
        """)

        # Таблица участников дейликов
        cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            daily_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            is_driver INTEGER DEFAULT 0,
            FOREIGN KEY (daily_id) REFERENCES daily_events(id)
        );
        """)

        conn.commit()
        print("✅ Таблицы daily_events и daily_participants созданы/проверены.")


if __name__ == "__main__":
    create_tables()
