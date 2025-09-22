# new_tables.py
import sqlite3
from contextlib import closing
from datetime import date, timedelta

DB_PATH = "stats.db"  # <- ваш файл БД

def create_tables():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()

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
    print("✅ Таблицы checked/created: daily_events, daily_participants")


def insert_mock_dailies():
    chat_id = -1002737417162
    creator_user_id = 884940984

    today = date.today()
    dailies = [
        {
            "name": "Завтрак для бездомных животных",
            "description": "Собираемся приготовить и развезти корм по приютам.",
            "date": today.isoformat(),
            "time": "09:30",
            "cars": "нет",
            "link": ""
        },
        {
            "name": "Поездка в приют",
            "description": "Едем навещать собак, выгуливать и помогать работникам.",
            "date": (today + timedelta(days=1)).isoformat(),
            "time": "11:00",
            "cars": "да",
            "link": ""
        },
        {
            "name": "Сбор вещей для приюта",
            "description": "Собираем одеяла, игрушки и лекарства.",
            "date": (today + timedelta(days=2)).isoformat(),
            "time": "18:00",
            "cars": "да",
            "link": "https://example.com/collection-info"
        },
    ]

    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()

        inserted_ids = []
        for ev in dailies:
            # проверим, есть ли уже такой дейлик (чтобы не дублировать при повторном запуске)
            cur.execute("""
                SELECT id FROM daily_events
                WHERE chat_id = ? AND name = ? AND date = ?
            """, (chat_id, ev["name"], ev["date"]))
            row = cur.fetchone()
            if row:
                print(f"⚠️ Пропускаем (уже есть): {ev['name']} ({ev['date']}) — id={row[0]}")
                inserted_ids.append(row[0])
                continue

            cur.execute("""
                INSERT INTO daily_events
                    (chat_id, creator_user_id, name, description, date, time, cars, link)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                chat_id,
                creator_user_id,
                ev["name"],
                ev["description"],
                ev["date"],
                ev["time"],
                ev["cars"],
                ev["link"],
            ))
            new_id = cur.lastrowid
            inserted_ids.append(new_id)
            print(f"Добавлен дейлик id={new_id}: {ev['name']} ({ev['date']})")

        # Добавим участников: сделаем создателя водителем в первых двух дейликах (если нет)
        to_mark_driver = inserted_ids[:2]  # первые два
        for daily_id in to_mark_driver:
            if daily_id is None:
                continue
            # проверим, не добавлен ли уже такой участник
            cur.execute("""
                SELECT id FROM daily_participants
                WHERE daily_id = ? AND user_id = ?
            """, (daily_id, creator_user_id))
            if cur.fetchone():
                print(f"⚠️ Участник уже есть в daily_id={daily_id}")
                # если есть, но is_driver может быть 0 — обновим
                cur.execute("""
                    UPDATE daily_participants
                    SET is_driver = 1
                    WHERE daily_id = ? AND user_id = ?
                """, (daily_id, creator_user_id))
                continue

            cur.execute("""
                INSERT INTO daily_participants (daily_id, user_id, is_driver)
                VALUES (?, ?, 1)
            """, (daily_id, creator_user_id))
            print(f"Добавлен участник (driver) user={creator_user_id} в daily_id={daily_id}")

        conn.commit()
    print("✅ Вставка моковых дейликов завершена.")


if __name__ == "__main__":
    create_tables()
    insert_mock_dailies()
