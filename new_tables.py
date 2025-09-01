# migrate_sticker_stats_daily.py
import sqlite3
from datetime import date

DB_FILE = "stats.db"

def migrate():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    # 1) Если таблицы нет — создаём нужную схему
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sticker_stats'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE sticker_stats (
                chat_id INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                set_name TEXT,
                date TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, file_id, date)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sticker_stats_chat_date ON sticker_stats(chat_id, date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sticker_stats_setname ON sticker_stats(set_name)")
        conn.commit()
        print("✅ Создана таблица sticker_stats (новая).")
        conn.close()
        return

    # 2) Таблица есть — проверим, есть ли колонка 'date'
    cur.execute("PRAGMA table_info(sticker_stats)")
    cols = [row[1] for row in cur.fetchall()]
    if "date" in cols:
        print("ℹ️ Таблица sticker_stats уже содержит колонку 'date'. Ничего не делаю.")
        conn.close()
        return

    # 3) Миграция: создаём новую таблицу, копируем данные с today's date, удаляем старую и переименовываем
    print("⚠️ Обнаружена старая таблица sticker_stats без колонки 'date'. Начинаю миграцию...")
    cur.execute("""
        CREATE TABLE sticker_stats_new (
            chat_id INTEGER NOT NULL,
            file_id TEXT NOT NULL,
            set_name TEXT,
            date TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, file_id, date)
        )
    """)
    today = date.today().isoformat()
    cur.execute("""
        INSERT INTO sticker_stats_new (chat_id, file_id, set_name, date, count)
        SELECT chat_id, file_id, set_name, ?, count FROM sticker_stats
    """, (today,))
    cur.execute("DROP TABLE sticker_stats")
    cur.execute("ALTER TABLE sticker_stats_new RENAME TO sticker_stats")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sticker_stats_chat_date ON sticker_stats(chat_id, date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sticker_stats_setname ON sticker_stats(set_name)")
    conn.commit()
    conn.close()
    print(f"✅ Миграция завершена. Существующие счётчики перенесены в дату {today}.")

if __name__ == "__main__":
    migrate()
