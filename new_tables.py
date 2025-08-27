import sqlite3

DB_PATH = "stats.db"

with sqlite3.connect(DB_PATH) as conn:
    cur = conn.cursor()

    # 1) Таблица для ачивок
    cur.execute("""
    CREATE TABLE IF NOT EXISTS achievements (
        key TEXT PRIMARY KEY,   -- уникальный ключ ачивки
        name_m TEXT NOT NULL,   -- название для мужского пола
        name_f TEXT NOT NULL    -- название для женского пола
    )
    """)

    # 2) Таблица для логов получения ачивок пользователями
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_achievements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        achievement_key TEXT NOT NULL,
        date TEXT NOT NULL,
        FOREIGN KEY(achievement_key) REFERENCES achievements(key)
    )
    """)

    # Наполняем таблицу achievements текущими ачивками
    achievements_data = [
        ("sticker_bomber", "Стикербомбер", "Стикербомберка"),
        ("fluder", "Флудер", "Флудерка"),
        ("dushnila", "Душнила", "Душнила"),
        ("skromnyashka", "Скромняшка", "Скромняшка")
    ]

    for key, name_m, name_f in achievements_data:
        cur.execute("""
        INSERT OR IGNORE INTO achievements (key, name_m, name_f)
        VALUES (?, ?, ?)
        """, (key, name_m, name_f))

    conn.commit()

print("Новая инфраструктура для ачивок создана и заполнена текущими ачивками.")
