import sqlite3
from contextlib import closing

DB_FILE = "stats.db"


def ensure_masturbate_log_table(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS masturbate_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            is_winner INTEGER NOT NULL DEFAULT 0,
            reward_sits INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_masturbate_log_chat_time "
        "ON masturbate_log(chat_id, created_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_masturbate_log_chat_user "
        "ON masturbate_log(chat_id, user_id)"
    )


def ensure_matsturbator_achievement(cur: sqlite3.Cursor) -> None:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='achievements'"
    )
    if not cur.fetchone():
        return

    cur.execute(
        """
        INSERT OR IGNORE INTO achievements (key, name_m, name_f)
        VALUES ('matsturbator', 'Дротик', 'Дротесса')
        """
    )


def migrate() -> None:
    with closing(sqlite3.connect(DB_FILE)) as conn:
        cur = conn.cursor()
        ensure_masturbate_log_table(cur)
        ensure_matsturbator_achievement(cur)
        conn.commit()


if __name__ == "__main__":
    migrate()
    print("DB migration complete: masturbate_log + matsturbator achievement.")
