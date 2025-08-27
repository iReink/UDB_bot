# insert_achievements.py
import sqlite3

DB_FILE = "stats.db"

def insert_achievements():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    achievements = [
        ("lubimka", "Любимка", "Любимка"),  # одинаковое название
        ("likesobornik", "Лайкосборник", "Лайкосборница"),
        ("dobroe_serdtse", "Большое доброе сердце", "Большое доброе сердце"),
        ("tsarsky_like", "Царский лайк", "Царский лайк"),
    ]

    for key, male, female in achievements:
        cur.execute("""
            INSERT OR IGNORE INTO achievements (key, name_m, name_f)
            VALUES (?, ?, ?)
        """, (key, male, female))

    conn.commit()
    conn.close()

if __name__ == "__main__":
    insert_achievements()
    print("✅ Новые ачивки добавлены в таблицу achievements")
