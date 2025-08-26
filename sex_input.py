import sqlite3

DB_FILE = "stats.db"


def ask_sex_for_users(conn):
    cur = conn.cursor()
    # выбираем всех, у кого sex NULL
    cur.execute("SELECT user_id, name FROM users WHERE sex IS NULL")
    rows = cur.fetchall()

    if not rows:
        print("✅ Все пользователи уже имеют значение sex")
        return

    print(f"Найдено {len(rows)} пользователей без пола\n")

    for user_id, name in rows:
        while True:
            answer = input(f"Введите пол для пользователя '{name}' (f = female, m = male): ").strip().lower()
            if answer in ("f", "m"):
                cur.execute("UPDATE users SET sex = ? WHERE user_id = ?", (answer, user_id))
                conn.commit()
                print(f"✅ Пол '{answer}' сохранён для {name}\n")
                break
            else:
                print("⚠️ Ошибка: введите только 'f' или 'm'")


def main():
    conn = sqlite3.connect(DB_FILE)
    ask_sex_for_users(conn)
    conn.close()


if __name__ == "__main__":
    main()
