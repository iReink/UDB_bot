# new_tables.py
import sqlite3
from contextlib import closing

DB_PATH = "stats.db"

def add_calendar_event_id_to_daily_events_table():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()

        # Проверяем, существует ли таблица daily_events
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_events'")
        table_exists = cur.fetchone()

        if not table_exists:
            print("Таблица daily_events не существует. Невозможно добавить столбец calendar_event_id.")
            return

        # Проверяем, существует ли столбец calendar_event_id
        cur.execute("PRAGMA table_info(daily_events)")
        columns = [column[1] for column in cur.fetchall()]

        if "calendar_event_id" not in columns:
            try:
                cur.execute("ALTER TABLE daily_events ADD COLUMN calendar_event_id TEXT")
                conn.commit()
                print("Столбец calendar_event_id добавлен в таблицу daily_events.")
            except sqlite3.OperationalError as e:
                print(f"Ошибка при добавлении столбца calendar_event_id: {e}")
        else:
            print("Столбец calendar_event_id уже существует в таблице daily_events.")

if __name__ == "__main__":
    add_calendar_event_id_to_daily_events_table() # Добавляем вызов новой функции
