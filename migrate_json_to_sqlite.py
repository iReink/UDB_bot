import json
import sqlite3
import os
from datetime import datetime

DB_FILE = "stats.db"
JSON_FILE = "stats.json"


def init_db(conn):
    cur = conn.cursor()

    # users
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        chat_id   TEXT,
        user_id   TEXT,
        name      TEXT,
        sits      INTEGER DEFAULT 0,
        punished  INTEGER DEFAULT 0,
        PRIMARY KEY (chat_id, user_id)
    )
    """)

    # daily stats
    cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_stats (
        chat_id   TEXT,
        user_id   TEXT,
        date      TEXT,
        messages  INTEGER DEFAULT 0,
        words     INTEGER DEFAULT 0,
        chars     INTEGER DEFAULT 0,
        stickers  INTEGER DEFAULT 0,
        coffee    INTEGER DEFAULT 0,
        PRIMARY KEY (chat_id, user_id, date)
    )
    """)

    # total stats
    cur.execute("""
    CREATE TABLE IF NOT EXISTS total_stats (
        chat_id   TEXT,
        user_id   TEXT,
        messages  INTEGER DEFAULT 0,
        words     INTEGER DEFAULT 0,
        chars     INTEGER DEFAULT 0,
        stickers  INTEGER DEFAULT 0,
        coffee    INTEGER DEFAULT 0,
        PRIMARY KEY (chat_id, user_id)
    )
    """)

    conn.commit()


def migrate():
    if not os.path.exists(JSON_FILE):
        print(f"❌ Не найден {JSON_FILE}")
        return

    with open(JSON_FILE, "r", encoding="utf-8") as f:
        stats = json.load(f)

    conn = sqlite3.connect(DB_FILE)
    init_db(conn)
    cur = conn.cursor()

    for chat_id, users in stats.items():
        for user_id, data in users.items():
            name = data.get("name", str(user_id))
            punished = int(data.get("punished", 0))
            sits = int(data.get("sits", 0))

            # вставляем/обновляем user
            cur.execute("""
                INSERT INTO users (chat_id, user_id, name, sits, punished)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    name=excluded.name,
                    sits=excluded.sits,
                    punished=excluded.punished
            """, (chat_id, user_id, name, sits, punished))

            # total
            total = data.get("total", {})
            cur.execute("""
                INSERT INTO total_stats (chat_id, user_id, messages, words, chars, stickers, coffee)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    messages=excluded.messages,
                    words=excluded.words,
                    chars=excluded.chars,
                    stickers=excluded.stickers,
                    coffee=excluded.coffee
            """, (
                chat_id, user_id,
                int(total.get("messages", 0)),
                int(total.get("words", 0)),
                int(total.get("chars", 0)),
                int(total.get("stickers", 0)),
                int(total.get("coffee", 0))
            ))

            # daily
            daily = data.get("daily", [])
            for idx, day_data in enumerate(daily):
                # если в json-е нет даты, используем "смещение" от сегодняшнего дня
                date = day_data.get("date")
                if not date:
                    date = (datetime.now().date()).isoformat()

                cur.execute("""
                    INSERT INTO daily_stats (chat_id, user_id, date, messages, words, chars, stickers, coffee)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chat_id, user_id, date) DO UPDATE SET
                        messages=excluded.messages,
                        words=excluded.words,
                        chars=excluded.chars,
                        stickers=excluded.stickers,
                        coffee=excluded.coffee
                """, (
                    chat_id, user_id, date,
                    int(day_data.get("messages", 0)),
                    int(day_data.get("words", 0)),
                    int(day_data.get("chars", 0)),
                    int(day_data.get("stickers", 0)),
                    int(day_data.get("coffee", 0))
                ))

    conn.commit()
    conn.close()
    print("✅ Миграция завершена успешно.")


if __name__ == "__main__":
    migrate()
