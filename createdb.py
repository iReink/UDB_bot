import sqlite3
from contextlib import closing
from pathlib import Path

DB_CANDIDATES = (
    "stats.db",
    "udb.sqlite3",
    "database.sqlite3",
    "UDB_bot/stats.db",
    "UDB_bot/udb.sqlite3",
    "UDB_bot/database.sqlite3",
)


def _table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    )
    return cur.fetchone() is not None


def ensure_web_settings_table(cur: sqlite3.Cursor) -> bool:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS web_settings (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            hide_base INTEGER NOT NULL DEFAULT 0 CHECK(hide_base IN (0, 1)),
            reject_geyser_catch_by_guest INTEGER NOT NULL DEFAULT 0 CHECK(reject_geyser_catch_by_guest IN (0, 1)),
            notify_group_masturbation INTEGER NOT NULL DEFAULT 1 CHECK(notify_group_masturbation IN (0, 1)),
            notify_group_masturbation_sound INTEGER NOT NULL DEFAULT 1 CHECK(notify_group_masturbation_sound IN (0, 1)),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, chat_id)
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_settings_chat_user
        ON web_settings(chat_id, user_id)
        """
    )

    cur.execute("PRAGMA table_info(web_settings)")
    columns = {str(row[1]).lower() for row in cur.fetchall()}
    if "notify_group_masturbation" not in columns:
        cur.execute(
            """
            ALTER TABLE web_settings
            ADD COLUMN notify_group_masturbation INTEGER NOT NULL DEFAULT 1
            CHECK(notify_group_masturbation IN (0, 1))
            """
        )
    if "notify_group_masturbation_sound" not in columns:
        cur.execute(
            """
            ALTER TABLE web_settings
            ADD COLUMN notify_group_masturbation_sound INTEGER NOT NULL DEFAULT 1
            CHECK(notify_group_masturbation_sound IN (0, 1))
            """
        )

    if _table_exists(cur, "users"):
        cur.execute(
            """
            INSERT OR IGNORE INTO web_settings (
                user_id,
                chat_id,
                hide_base,
                reject_geyser_catch_by_guest,
                notify_group_masturbation,
                notify_group_masturbation_sound
            )
            SELECT u.user_id, u.chat_id, 0, 0, 1, 1
            FROM users u
            """
        )
        cur.execute(
            """
            UPDATE web_settings
            SET notify_group_masturbation = 1
            WHERE notify_group_masturbation NOT IN (0, 1)
               OR notify_group_masturbation IS NULL
            """
        )
        cur.execute(
            """
            UPDATE web_settings
            SET notify_group_masturbation_sound = 1
            WHERE notify_group_masturbation_sound NOT IN (0, 1)
               OR notify_group_masturbation_sound IS NULL
            """
        )
    return True


# Keep this name for compatibility with web.server import.
def ensure_idle_game_tables(cur: sqlite3.Cursor) -> bool:
    return ensure_web_settings_table(cur)


def _existing_db_paths() -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in DB_CANDIDATES:
        path = Path(candidate).resolve()
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and path.is_file():
            result.append(path)
    return result


def migrate() -> list[Path]:
    updated_paths: list[Path] = []
    for db_path in _existing_db_paths():
        with closing(sqlite3.connect(db_path)) as conn:
            cur = conn.cursor()
            changed = ensure_web_settings_table(cur)
            if changed:
                conn.commit()
                updated_paths.append(db_path)

    if not updated_paths:
        checked = ", ".join(str(p) for p in _existing_db_paths()) or "no .db/.sqlite3 files found"
        raise RuntimeError(
            "Database files not found for migration. "
            f"Checked: {checked}"
        )
    return updated_paths


if __name__ == "__main__":
    paths = migrate()
    print("Done: web_settings migrated in databases:")
    for path in paths:
        print(f" - {path}")
