import json
import sqlite3
import os
from datetime import datetime, timedelta

DB_FILE = "chat_stats.db"
JSON_FILE = "stats.json"

def create_tables(conn):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        name TEXT,
        sits INTEGER DEFAULT 0,
        punished INTEGER DEFAULT 0,
        sex TEXT DEFAULT NULL
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        messages INTEGER DEFAULT 0,
        words INTEGER DEFAULT 0,
        chars INTEGER DEFAULT 0,
        stickers INTEGER DEFAULT 0,
        coffee INTEGER DEFAULT 0,
        UNIQUE(user_id, chat_id, date)
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS total_stats (
        user_id INTEGER PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        messages INTEGER DEFAULT 0,
        words INTEGER DEFAULT 0,
        chars INTEGER DEFAULT 0,
        stickers INTEGER DEFAULT 0,
        coffee INTEGER DEFAULT 0
    )""")
    conn.commit()

def migrate_from_json(conn, json_file):
    if not os.path.exists(json_file):
        print(f"Файл {json_file} не найден")
        return

    with open(json_file, "r", encoding="utf-8") as f:
        stats = json.load(f)

    cur = conn.cursor()
    today = datetime.today().date()

    for chat_id_str, users in stats.items():
        chat_id = int(chat_id_str)
        for uid_str, data in users.items():
            user_id = int(uid_str)
            name = data.get("name", "")
            sits = int(data.get("sits", 0))
            punished = int(data.get("punished", 0))

            # вставка/обновление пользователя
            cur.execute("""
            INSERT INTO users (user_id, chat_id, name, sits, punished, sex)
            VALUES (?, ?, ?, ?, ?, NULL)
            ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name,
                sits=excluded.sits,
                punished=excluded.punished
            """, (user_id, chat_id, name, sits, punished))

            # daily stats
            daily_list = data.get("daily", [])
            for i, day_data in enumerate(reversed(daily_list)):
                date = today - timedelta(days=i)
                date_str = date.isoformat()
                cur.execute("""
                INSERT INTO daily_stats (user_id, chat_id, date, messages, words, chars, stickers, coffee)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, chat_id, date) DO UPDATE SET
                    messages=excluded.messages,
                    words=excluded.words,
                    chars=excluded.chars,
                    stickers=excluded.stickers,
                    coffee=excluded.coffee
                """, (
                    user_id,
                    chat_id,
                    date_str,
                    int(day_data.get("messages", 0)),
                    int(day_data.get("words", 0)),
                    int(day_data.get("chars", 0)),
                    int(day_data.get("stickers", 0)),
                    int(day_data.get("coffee", 0)),
                ))

            # total stats
            total = data.get("total", {})
            cur.execute("""
            INSERT INTO total_stats (user_id, chat_id, messages, words, chars, stickers, coffee)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                messages=excluded.messages,
                words=excluded.words,
                chars=excluded.chars,
                stickers=excluded.stickers,
                coffee=excluded.coffee
            """, (
                user_id,
                chat_id,
                int(total.get("messages", 0)),
                int(total.get("words", 0)),
                int(total.get("chars", 0)),
                int(total.get("stickers", 0)),
                int(total.get("coffee", 0)),
            ))

    conn.commit()

def main():
    conn = sqlite3.connect(DB_FILE)
    create_tables(conn)
    migrate_from_json(conn, JSON_FILE)
    conn.close()
    print("✅ Миграция завершена")

if __name__ == "__main__":
    main()
