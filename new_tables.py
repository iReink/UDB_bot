# insert_mock_dailies.py
from contextlib import closing
import sqlite3
from datetime import datetime, timedelta

DB_PATH = "udb.sqlite3"  # укажи свой путь, если отличается


def insert_mock_dailies():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()

        chat_id = -1002737417162
        creator_user_id = 884940984

        # 3 моковых дейлика
        dailies = [
            ("Завтрак для бездомных животных", "Приготовить и развезти корм", datetime.now().date().isoformat()),
            ("Поездка в приют", "Навестить и выгулять собак", (datetime.now().date() + timedelta(days=1)).isoformat()),
            ("Сбор вещей", "Привезти одеяла и игрушки в приют", (datetime.now().date() + timedelta(days=2)).isoformat())
        ]

        daily_ids = []
        for title, description, date_str in dailies:
            cur.execute(
                """
                INSERT INTO daily_events (chat_id, creator_user_id, title, description, event_date)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chat_id, creator_user_id, title, description, date_str)
            )
            daily_ids.append(cur.lastrowid)

        # В двух из трёх дейликов creator — водитель
        cur.executemany(
            """
            INSERT INTO daily_participants (daily_event_id, user_id, is_driver)
            VALUES (?, ?, ?)
            """,
            [
                (daily_ids[0], creator_user_id, 1),
                (daily_ids[1], creator_user_id, 1),
                # третий без участия
            ]
        )

        conn.commit()
        print("✅ Тестовые дейлики успешно добавлены!")


if __name__ == "__main__":
    insert_mock_dailies()
