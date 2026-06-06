from __future__ import annotations

import html
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "stats.db"
SCHEMA_FILE = BASE_DIR / "STATS_DB_SCHEMA.md"

TASK_TYPE_TEXT_TO_SQL = "text_to_sql"
TASK_STATUS_PENDING = "pending"
TASK_STATUS_PROCESSING = "processing"
TASK_STATUS_DONE = "done"
TASK_STATUS_FAILED = "failed"

TEXT_TO_SQL_MODEL = "gemma4:e4b"
TEXT_TO_SQL_PRIORITY = 100
TEXT_TO_SQL_COOLDOWN_SECONDS = 120
TEXT_TO_SQL_MAX_RETRY_ATTEMPT = 1
AI_TASK_LEASE_SECONDS = 180

CHAT_SCOPED_TABLES = {
    "users",
    "daily_stats",
    "total_stats",
    "messages_reactions",
    "sticker_stats",
    "sit_stats",
    "user_achievements",
    "mujlo",
    "sosalsa_stats",
    "user_quests",
    "daily_events",
    "settings",
    "geyser_events",
    "user_body_parts",
    "dicks",
    "masturbate_log",
    "idle_player_buildings",
    "web_geyser_daily_catches",
    "web_settings",
    "summary_publish_log",
}

DANGEROUS_SQL_WORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "attach",
    "detach",
    "pragma",
    "vacuum",
    "replace",
    "reindex",
}


class TextToSqlError(ValueError):
    pass


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def utcnow() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def now_iso() -> str:
    return utcnow().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def ensure_ai_tasks_table() -> None:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                priority INTEGER NOT NULL DEFAULT 100,
                model TEXT NOT NULL,
                prompt TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                result_text TEXT,
                error_text TEXT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                request_message_id INTEGER NOT NULL,
                response_message_id INTEGER,
                attempt INTEGER NOT NULL DEFAULT 0,
                lease_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_tasks_queue
            ON ai_tasks(status, priority DESC, created_at ASC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_tasks_chat_type_created
            ON ai_tasks(chat_id, task_type, created_at DESC)
            """
        )
        conn.commit()


def read_schema_markdown() -> str:
    try:
        return SCHEMA_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "Файл STATS_DB_SCHEMA.md не найден. Используй только известную схему SQLite из проекта."


def build_text_to_sql_prompt(
    user_query: str,
    chat_id: int,
    *,
    previous_sql: str | None = None,
    previous_error: str | None = None,
) -> str:
    schema = read_schema_markdown()
    retry_block = ""
    if previous_sql or previous_error:
        retry_block = f"""

Предыдущая попытка была неудачной.
SQL прошлой попытки:
{previous_sql or "(SQL не был получен)"}

Ошибка прошлой попытки:
{previous_error or "(ошибка не указана)"}

Исправь ошибку и верни новый корректный SELECT.
"""

    return f"""Ты преобразуешь пользовательский запрос на русском языке в один SQL-запрос SQLite.

Пользователь оставил запрос:
{user_query}

Текущий chat_id: {chat_id}
Текущая дата: {date.today().isoformat()}

Контракт ответа:
- Верни только один SQL-запрос SELECT для SQLite.
- Не используй markdown, пояснения, code fence, комментарии и shell-команды.
- Запрос будет выполнен backend'ом в read-only режиме.
- Никакие INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, ATTACH, PRAGMA, VACUUM и любые операции изменения БД недопустимы.
- Все пользовательские и чатовые данные фильтруй по chat_id = {chat_id}.
- В результате нужны имена и/или ники, а не голые user_id: используй JOIN users по user_id и chat_id.
- Используй понятные русские alias-ы колонок, чтобы backend мог красиво вывести таблицу.
- Если нужен топ или список, добавь разумный LIMIT.
- Если используешь дату "сегодня", сравнивай с '{date.today().isoformat()}'.
- Если запрос за период, используй поля date/date_taken/created_at согласно схеме.
{retry_block}
Схема БД:
{schema}
"""


def get_text_to_sql_cooldown(chat_id: int) -> int:
    ensure_ai_tasks_table()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT created_at
            FROM ai_tasks
            WHERE chat_id = ? AND task_type = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (chat_id, TASK_TYPE_TEXT_TO_SQL),
        )
        row = cur.fetchone()
    last_created = parse_iso(row["created_at"]) if row else None
    if not last_created:
        return 0
    elapsed = (utcnow() - last_created).total_seconds()
    return max(0, int(TEXT_TO_SQL_COOLDOWN_SECONDS - elapsed))


def create_text_to_sql_task(
    *,
    chat_id: int,
    user_id: int,
    request_message_id: int,
    user_query: str,
) -> int:
    ensure_ai_tasks_table()
    prompt = build_text_to_sql_prompt(user_query=user_query, chat_id=chat_id)
    payload = {
        "user_query": user_query,
        "chat_id": chat_id,
        "request_message_id": request_message_id,
        "created_by_user_id": user_id,
    }
    created_at = now_iso()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO ai_tasks (
                task_type, status, priority, model, prompt, payload_json,
                chat_id, user_id, request_message_id, attempt, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                TASK_TYPE_TEXT_TO_SQL,
                TASK_STATUS_PENDING,
                TEXT_TO_SQL_PRIORITY,
                TEXT_TO_SQL_MODEL,
                prompt,
                json.dumps(payload, ensure_ascii=False),
                chat_id,
                user_id,
                request_message_id,
                created_at,
                created_at,
            ),
        )
        task_id = int(cur.lastrowid)
        conn.commit()
        return task_id


def claim_next_task() -> dict[str, Any] | None:
    ensure_ai_tasks_table()
    now = now_iso()
    lease_until = (utcnow() + timedelta(seconds=AI_TASK_LEASE_SECONDS)).isoformat()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT *
            FROM ai_tasks
            WHERE status = ?
               OR (status = ? AND lease_until IS NOT NULL AND lease_until <= ?)
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """,
            (TASK_STATUS_PENDING, TASK_STATUS_PROCESSING, now),
        )
        row = cur.fetchone()
        if not row:
            conn.commit()
            return None
        task_id = int(row["id"])
        cur.execute(
            """
            UPDATE ai_tasks
            SET status = ?, lease_until = ?, updated_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_PROCESSING, lease_until, now, task_id),
        )
        cur.execute("SELECT * FROM ai_tasks WHERE id = ?", (task_id,))
        claimed_row = cur.fetchone()
        conn.commit()
    return serialize_task(claimed_row)


def serialize_task(row: sqlite3.Row) -> dict[str, Any]:
    payload_raw = row["payload_json"] or "{}"
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": int(row["id"]),
        "task_type": row["task_type"],
        "status": row["status"],
        "priority": int(row["priority"]),
        "model": row["model"],
        "prompt": row["prompt"],
        "payload": payload,
        "chat_id": int(row["chat_id"]),
        "user_id": int(row["user_id"]),
        "request_message_id": int(row["request_message_id"]),
        "attempt": int(row["attempt"] or 0),
        "created_at": row["created_at"],
        "lease_until": row["lease_until"],
    }


def get_task(task_id: int) -> sqlite3.Row | None:
    ensure_ai_tasks_table()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM ai_tasks WHERE id = ?", (task_id,))
        return cur.fetchone()


def mark_task_done(task_id: int, *, sql: str, response_message_id: int | None) -> None:
    now = now_iso()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE ai_tasks
            SET status = ?, result_text = ?, error_text = NULL, response_message_id = ?,
                lease_until = NULL, updated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_DONE, sql, response_message_id, now, now, task_id),
        )
        conn.commit()


def requeue_or_fail_task(task_id: int, *, previous_sql: str | None, error_text: str) -> tuple[bool, sqlite3.Row | None]:
    task = get_task(task_id)
    if not task:
        return False, None

    attempt = int(task["attempt"] or 0)
    now = now_iso()
    if attempt < TEXT_TO_SQL_MAX_RETRY_ATTEMPT:
        payload = json.loads(task["payload_json"] or "{}")
        user_query = str(payload.get("user_query") or "")
        retry_prompt = build_text_to_sql_prompt(
            user_query=user_query,
            chat_id=int(task["chat_id"]),
            previous_sql=previous_sql,
            previous_error=error_text,
        )
        with closing(get_connection()) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE ai_tasks
                SET status = ?, prompt = ?, error_text = ?, attempt = attempt + 1,
                    lease_until = NULL, updated_at = ?
                WHERE id = ?
                """,
                (TASK_STATUS_PENDING, retry_prompt, error_text, now, task_id),
            )
            conn.commit()
        return True, get_task(task_id)

    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE ai_tasks
            SET status = ?, error_text = ?, lease_until = NULL, updated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_FAILED, error_text, now, now, task_id),
        )
        conn.commit()
    return False, get_task(task_id)


def clean_model_sql(raw_output: str | None) -> str:
    text = (raw_output or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:sql|sqlite|sqlite3)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    return text.strip()


def validate_text_to_sql(raw_output: str | None, *, chat_id: int) -> str:
    sql = clean_model_sql(raw_output)
    if not sql:
        raise TextToSqlError("LLM вернула пустой ответ.")

    semicolon_pos = sql.find(";")
    if semicolon_pos != -1 and semicolon_pos != len(sql.rstrip()) - 1:
        raise TextToSqlError("SQL должен содержать только один statement.")
    sql = sql.rstrip().rstrip(";").strip()

    lowered = sql.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise TextToSqlError("SQL должен начинаться с SELECT или WITH ... SELECT.")

    words = set(re.findall(r"\b[a-z_]+\b", lowered))
    bad_words = sorted(words & DANGEROUS_SQL_WORDS)
    if bad_words:
        raise TextToSqlError(f"SQL содержит запрещенные операции: {', '.join(bad_words)}.")

    if lowered.startswith("with") and not re.search(r"\bselect\b", lowered):
        raise TextToSqlError("WITH-запрос должен содержать SELECT.")

    if not sqlite3.complete_statement(sql + ";"):
        raise TextToSqlError("SQL синтаксически не похож на завершенный statement.")

    used_chat_tables = {
        table
        for table in CHAT_SCOPED_TABLES
        if re.search(rf"\b{re.escape(table.lower())}\b", lowered)
    }
    if used_chat_tables:
        chat_id_pattern = rf"\bchat_id\b\s*(=|IN\s*\()\s*{re.escape(str(chat_id))}\b"
        if not re.search(chat_id_pattern, lowered):
            raise TextToSqlError("Запрос к чатовым данным должен явно фильтровать текущий chat_id.")

    return sql


def readonly_authorizer(action: int, arg1: str | None, arg2: str | None, dbname: str | None, source: str | None) -> int:
    denied = {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_PRAGMA,
    }
    return sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK


def execute_readonly_sql(sql: str) -> tuple[list[str], list[tuple[Any, ...]], bool]:
    uri = f"file:{DB_FILE.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA query_only = ON")
        conn.set_authorizer(readonly_authorizer)
        cur.execute(sql)
        rows = cur.fetchmany(101)
        columns = [item[0] for item in (cur.description or [])]
        truncated = len(rows) > 100
        if truncated:
            rows = rows[:100]
        return columns, [tuple(row) for row in rows], truncated
    finally:
        conn.close()


def _cell(value: Any, max_len: int = 80) -> str:
    if value is None:
        text = ""
    else:
        text = str(value).replace("\r", " ").replace("\n", " ")
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def format_sql_result_for_telegram(columns: list[str], rows: list[tuple[Any, ...]], *, truncated: bool) -> str:
    if not columns:
        return "Запрос выполнен, но не вернул табличный результат."
    if not rows:
        return "Запрос выполнен, но строк не найдено."

    table = [[_cell(col, 40) for col in columns]]
    table.extend([[_cell(value) for value in row] for row in rows])
    widths = [min(40, max(len(line[i]) for line in table)) for i in range(len(columns))]

    def fmt(row: list[str]) -> str:
        return " | ".join(row[i].ljust(widths[i]) for i in range(len(widths))).rstrip()

    sep = "-+-".join("-" * width for width in widths)
    lines = [fmt(table[0]), sep]
    lines.extend(fmt(row) for row in table[1:])
    if truncated:
        lines.append("... показаны первые 100 строк")

    body = "\n".join(lines)
    max_body_len = 3600
    if len(body) > max_body_len:
        body = body[:max_body_len].rstrip() + "\n... результат обрезан"
    return f"<b>Результат запроса</b>\n<pre>{html.escape(body)}</pre>"
