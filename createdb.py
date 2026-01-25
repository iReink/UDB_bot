import sqlite3
from contextlib import closing

DB_FILE = "stats.db"


def add_top1_entrance_date_column() -> None:
    with closing(sqlite3.connect(DB_FILE)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(dicks)")
        columns = {row["name"] for row in cur.fetchall()}
        if "top1_entrance_date" not in columns:
            cur.execute("ALTER TABLE dicks ADD COLUMN top1_entrance_date TEXT DEFAULT ''")
            conn.commit()


if __name__ == "__main__":
    add_top1_entrance_date_column()
