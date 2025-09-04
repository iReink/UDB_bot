import sqlite3

DB_FILE = "stats.db"


def ask_nick_for_users(conn):
    cur = conn.cursor()
    # выбираем всех, у кого nick NULL
    cur.execute("SELECT user_id, name FROM users WHERE nick IS NULL")
    rows = cur.fetchall()

    if not rows:
        print("✅ Все пользователи уже имеют ник")
        return

    print(f"Найдено {len(rows)} пользователей без ника\n")

    for user_id, name in rows:
        while True:
            answer = input(f"Введите ник для пользователя '{name}' (оставьте пустым, если нет): ").strip()
            if answer == "":
                cur.execute("UPDATE users SET nick = ? WHERE user_id = ?", (None, user_id))
                conn.commit()
                print(f"ℹ️ У {name} ник отсутствует\n")
                break
            else:
                # Добавим @, если пользователь не ввёл
                if not answer.startswith("@"):
                    answer = "@" + answer
                cur.execute("UPDATE users SET nick = ? WHERE user_id = ?", (answer, user_id))
                conn.commit()
                print(f"✅ Ник '{answer}' сохранён для {name}\n")
                break


def main():
    conn = sqlite3.connect(DB_FILE)
    ask_nick_for_users(conn)
    conn.close()


if __name__ == "__main__":
    main()
