import sqlite3

DB_FILE = "stats.db"

def create_tables(conn):
    cur = conn.cursor()

    # Таблица всех квестов (справочник)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS quests_catalog (
            quest_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            type TEXT NOT NULL,
            target INTEGER NOT NULL,
            reward INTEGER NOT NULL
        )
    """)

    # Таблица прогресса квестов для каждого пользователя
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_quests (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            quest_id INTEGER NOT NULL,
            date_taken TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'failed')),
            progress INTEGER DEFAULT 0,
            date_completed TEXT,
            PRIMARY KEY (user_id, chat_id, date_taken),
            FOREIGN KEY (quest_id) REFERENCES quests_catalog(quest_id)
        )
    """)

    conn.commit()
    print("✅ Таблицы quests_catalog и user_quests созданы (если их не было)")


def seed_quests(conn):
    """Заполняем quests_catalog базовыми квестами"""
    cur = conn.cursor()

    quests = [
        # Сообщения
        ("50 сообщений", "Напиши 50 сообщений за день", "messages_sent", 50, 2),
        ("100 сообщений", "Напиши 100 сообщений за день", "messages_sent", 100, 4),
        ("200 сообщений", "Напиши 200 сообщений за день", "messages_sent", 200, 6),

        # Стикеры
        ("5 стикеров", "Отправь 5 стикеров за день", "stickers_sent", 5, 1),
        ("10 стикеров", "Отправь 10 стикеров за день", "stickers_sent", 10, 2),
        ("15 стикеров", "Отправь 15 стикеров за день", "stickers_sent", 15, 4),

        # Лайки поставленные
        ("30 лайков", "Поставь 30 лайков за день", "likes_given", 30, 2),
        ("70 лайков", "Поставь 70 лайков за день", "likes_given", 70, 4),
        ("120 лайков", "Поставь 120 лайков за день", "likes_given", 120, 6),

        # Лайки полученные
        ("20 лайков", "Получите 20 лайков за день", "likes_received", 20, 2),
        ("30 лайков", "Получите 30 лайков за день", "likes_received", 30, 3),
        ("50 лайков", "Получите 50 лайков за день", "likes_received", 50, 5),

        # Групповая мастурбация
        ("Победа в групповухе", "Выиграй групповую мастурбацию", "group_win", 1, 5),

        # Кофе
        ("5 кофе без штрафа", "Выпей 5 кофе без штрафа за день", "coffee_safe", 5, 5),
    ]

    cur.executemany("""
        INSERT INTO quests_catalog (name, description, type, target, reward)
        VALUES (?, ?, ?, ?, ?)
    """, quests)

    conn.commit()
    print(f"✅ Добавлено {len(quests)} квестов в quests_catalog")


def main():
    conn = sqlite3.connect(DB_FILE)
    create_tables(conn)
    seed_quests(conn)
    conn.close()


if __name__ == "__main__":
    main()
