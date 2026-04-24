import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "masturbate.db"


class MasturbateStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DB_FILE
        self._schema_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self, reset_runtime_state: bool = False) -> None:
        with self._schema_lock:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.executescript(
                    """
                    PRAGMA journal_mode = WAL;
                    PRAGMA synchronous = NORMAL;

                    CREATE TABLE IF NOT EXISTS events (
                        chat_id INTEGER PRIMARY KEY,
                        status TEXT NOT NULL DEFAULT 'preparing',
                        join_open INTEGER NOT NULL DEFAULT 0 CHECK(join_open IN (0, 1)),
                        join_message_id INTEGER,
                        thread_id INTEGER,
                        started_by_user_id INTEGER NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS participants (
                        chat_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        display_name TEXT NOT NULL,
                        role TEXT NOT NULL CHECK(role IN ('participant', 'spectator')),
                        is_freebie INTEGER NOT NULL DEFAULT 0 CHECK(is_freebie IN (0, 1)),
                        joined_order INTEGER NOT NULL,
                        source TEXT NOT NULL DEFAULT 'tg',
                        joined_at INTEGER NOT NULL,
                        PRIMARY KEY (chat_id, user_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_participants_chat_order
                    ON participants(chat_id, joined_order);

                    CREATE TABLE IF NOT EXISTS reminders (
                        chat_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        display_name TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        PRIMARY KEY (chat_id, user_id)
                    );

                    CREATE TABLE IF NOT EXISTS outbox (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        processed_at INTEGER,
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_outbox_pending
                    ON outbox(processed_at, id);
                    """
                )
                if reset_runtime_state:
                    cur.execute("DELETE FROM participants")
                    cur.execute("DELETE FROM reminders")
                    cur.execute("DELETE FROM events")
                    cur.execute("DELETE FROM outbox")
                conn.commit()

    def get_event(self, chat_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT chat_id, status, join_open, join_message_id, thread_id, started_by_user_id
                FROM events
                WHERE chat_id = ?
                """,
                (chat_id,),
            )
            return cur.fetchone()

    def create_event(
        self,
        chat_id: int,
        started_by_user_id: int,
        starter_display_name: str,
        thread_id: int | None,
        source: str = "tg",
    ) -> str:
        now = int(time.time())
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT 1 FROM events WHERE chat_id = ?", (chat_id,))
            if cur.fetchone():
                conn.rollback()
                return "active_exists"

            cur.execute(
                """
                INSERT INTO events (
                    chat_id,
                    status,
                    join_open,
                    join_message_id,
                    thread_id,
                    started_by_user_id,
                    created_at,
                    updated_at
                )
                VALUES (?, 'preparing', 0, NULL, ?, ?, ?, ?)
                """,
                (chat_id, thread_id, started_by_user_id, now, now),
            )
            cur.execute(
                """
                INSERT INTO participants (
                    chat_id,
                    user_id,
                    display_name,
                    role,
                    is_freebie,
                    joined_order,
                    source,
                    joined_at
                )
                VALUES (?, ?, ?, 'participant', 0, 1, ?, ?)
                """,
                (chat_id, started_by_user_id, starter_display_name, source, now),
            )
            conn.commit()
        return "started"

    def set_join_window(
        self,
        chat_id: int,
        is_open: bool,
        status: str | None = None,
        join_message_id: int | None = None,
    ) -> bool:
        now = int(time.time())
        with self._connect() as conn:
            cur = conn.cursor()
            fields = ["join_open = ?", "updated_at = ?"]
            values: list[Any] = [1 if is_open else 0, now]
            if status is not None:
                fields.append("status = ?")
                values.append(status)
            if join_message_id is not None:
                fields.append("join_message_id = ?")
                values.append(join_message_id)
            values.append(chat_id)
            cur.execute(
                f"UPDATE events SET {', '.join(fields)} WHERE chat_id = ?",
                tuple(values),
            )
            conn.commit()
            return cur.rowcount > 0

    def set_join_message_id(self, chat_id: int, message_id: int) -> bool:
        return self.set_join_window(chat_id=chat_id, is_open=True, join_message_id=message_id)

    def add_member(
        self,
        chat_id: int,
        user_id: int,
        display_name: str,
        role: str,
        is_freebie: bool,
        source: str = "tg",
    ) -> str:
        now = int(time.time())
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                """
                SELECT join_open
                FROM events
                WHERE chat_id = ?
                """,
                (chat_id,),
            )
            event_row = cur.fetchone()
            if not event_row:
                conn.rollback()
                return "no_event"

            if int(event_row["join_open"] or 0) != 1:
                conn.rollback()
                return "join_closed"

            cur.execute(
                """
                SELECT role
                FROM participants
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            )
            if cur.fetchone():
                conn.rollback()
                return "already_joined"

            cur.execute(
                """
                SELECT COALESCE(MAX(joined_order), 0) + 1 AS next_order
                FROM participants
                WHERE chat_id = ?
                """,
                (chat_id,),
            )
            next_order = int(cur.fetchone()["next_order"] or 1)
            cur.execute(
                """
                INSERT INTO participants (
                    chat_id,
                    user_id,
                    display_name,
                    role,
                    is_freebie,
                    joined_order,
                    source,
                    joined_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    user_id,
                    display_name,
                    role,
                    1 if is_freebie else 0,
                    next_order,
                    source,
                    now,
                ),
            )
            conn.commit()
        return "added"

    def add_reminder(self, chat_id: int, user_id: int, display_name: str) -> str:
        now = int(time.time())
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT 1 FROM events WHERE chat_id = ?", (chat_id,))
            if not cur.fetchone():
                conn.rollback()
                return "no_event"

            cur.execute(
                """
                INSERT OR IGNORE INTO reminders (chat_id, user_id, display_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (chat_id, user_id, display_name, now),
            )
            inserted = cur.rowcount > 0
            conn.commit()
        return "added" if inserted else "already_added"

    def list_participants(self, chat_id: int, role: str | None = None, is_freebie: bool | None = None) -> list[sqlite3.Row]:
        clauses = ["chat_id = ?"]
        params: list[Any] = [chat_id]
        if role is not None:
            clauses.append("role = ?")
            params.append(role)
        if is_freebie is not None:
            clauses.append("is_freebie = ?")
            params.append(1 if is_freebie else 0)

        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT user_id, display_name, role, is_freebie, joined_order
                FROM participants
                WHERE {' AND '.join(clauses)}
                ORDER BY joined_order ASC
                """,
                tuple(params),
            )
            return cur.fetchall()

    def list_reminders(self, chat_id: int) -> list[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT user_id, display_name
                FROM reminders
                WHERE chat_id = ?
                ORDER BY created_at ASC
                """,
                (chat_id,),
            )
            return cur.fetchall()

    def finish_event(self, chat_id: int) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("DELETE FROM participants WHERE chat_id = ?", (chat_id,))
            cur.execute("DELETE FROM reminders WHERE chat_id = ?", (chat_id,))
            cur.execute("DELETE FROM events WHERE chat_id = ?", (chat_id,))
            conn.commit()

    def enqueue_outbox(self, chat_id: int, kind: str, payload: dict[str, Any]) -> int:
        now = int(time.time())
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO outbox (chat_id, kind, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (chat_id, kind, json.dumps(payload, ensure_ascii=False), now),
            )
            outbox_id = int(cur.lastrowid)
            conn.commit()
        return outbox_id

    def fetch_outbox_batch(self, limit: int = 30) -> list[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, chat_id, kind, payload_json, attempt_count
                FROM outbox
                WHERE processed_at IS NULL
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            )
            return cur.fetchall()

    def mark_outbox_processed(self, outbox_id: int) -> None:
        now = int(time.time())
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE outbox
                SET processed_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (now, outbox_id),
            )
            conn.commit()

    def mark_outbox_failed(self, outbox_id: int, error_text: str, terminal: bool = False) -> None:
        now = int(time.time())
        with self._connect() as conn:
            cur = conn.cursor()
            if terminal:
                cur.execute(
                    """
                    UPDATE outbox
                    SET attempt_count = attempt_count + 1,
                        last_error = ?,
                        processed_at = ?
                    WHERE id = ?
                    """,
                    (error_text[:1000], now, outbox_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE outbox
                    SET attempt_count = attempt_count + 1,
                        last_error = ?
                    WHERE id = ?
                    """,
                    (error_text[:1000], outbox_id),
                )
            conn.commit()
