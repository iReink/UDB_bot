import sqlite3
from contextlib import closing

DB_FILE = "stats.db"


def create_dicks_table():
    with closing(sqlite3.connect(DB_FILE)) as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dicks (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                length INTEGER DEFAULT 0,
                grow_date TEXT DEFAULT '',
                buff TEXT DEFAULT '',
                buff_exp TEXT DEFAULT '',
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        conn.commit()


if __name__ == "__main__":
    create_dicks_table()
