import sqlite3
from contextlib import closing

DB_PATH = "stats.db"

with closing(sqlite3.connect(DB_PATH)) as conn:
    cur = conn.cursor()

    # ===============================
    # 1. Таблица частей тела
    # ===============================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS body_parts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name_nom TEXT NOT NULL,   -- Что?
            name_acc TEXT NOT NULL,   -- Укусил за что?
            name_gen TEXT NOT NULL    -- Лишился чего?
        );
    """)

    # ===============================
    # 2. Таблица частей тела пользователей
    # ===============================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_body_parts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            body_part_id INTEGER NOT NULL,
            state INTEGER NOT NULL DEFAULT 1,  -- 1 = на месте, 0 = откушено

            FOREIGN KEY (body_part_id) REFERENCES body_parts(id),
            UNIQUE(user_id, chat_id, body_part_id)
        );
    """)

    # ===============================
    # 3. Вставка базовых частей тела
    # ===============================
    base_parts = [
        ("Жопа",   "Жопу",   "Жопы"),
        ("Нипель", "Нипель", "Нипеля"),
        ("Щека",   "Щеку",   "Щеки"),
    ]

    cur.executemany("""
        INSERT INTO body_parts (name_nom, name_acc, name_gen)
        VALUES (?, ?, ?)
    """, base_parts)

    conn.commit()

print("Таблицы созданы, части тела добавлены.")
