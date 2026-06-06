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
TASK_TYPE_PROFILE_UPDATE = "profile_update"
TASK_STATUS_PENDING = "pending"
TASK_STATUS_PROCESSING = "processing"
TASK_STATUS_DONE = "done"
TASK_STATUS_FAILED = "failed"

TEXT_TO_SQL_MODEL = "gemma4:e4b"
TEXT_TO_SQL_PRIORITY = 100
TEXT_TO_SQL_COOLDOWN_SECONDS = 120
TEXT_TO_SQL_MAX_RETRY_ATTEMPT = 1
PROFILE_UPDATE_MODEL = "gemma4:e4b"
PROFILE_UPDATE_PRIORITY = 20
PROFILE_UPDATE_MAX_RETRY_ATTEMPT = 1
PROFILE_MIN_MESSAGE_LENGTH = 10
PROFILE_MIN_MESSAGES = 5
PROFILE_MAX_MESSAGES = 120
PROFILE_MESSAGES_CHAR_LIMIT = 12_000
AI_TASK_LEASE_SECONDS = 180

PROFILE_ARRAY_LIMITS = {
    "stable_interests": 5,
    "preferences": 5,
    "current_topics": 3,
    "behavior_notes": 5,
    "local_memes": 5,
    "facts": 5,
    "do_not_assume": 5,
}
PROFILE_STRING_FIELDS = {
    "display_name",
    "communication_style",
    "confidence",
    "short_summary",
}
PROFILE_REQUIRED_KEYS = set(PROFILE_ARRAY_LIMITS) | PROFILE_STRING_FIELDS
PROFILE_CONFIDENCE_VALUES = {"low", "medium", "high"}

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

FORBIDDEN_TEXT_TO_SQL_TABLES = {
    "ai_profiles",
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


class ProfileUpdateError(ValueError):
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


def ensure_ai_profiles_table() -> None:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_profiles (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                profile_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                profile_json TEXT,
                summary_text TEXT,
                message_count INTEGER NOT NULL DEFAULT 0,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                model TEXT NOT NULL,
                task_id INTEGER,
                error_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, chat_id, profile_date)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_profiles_latest
            ON ai_profiles(user_id, chat_id, status, profile_date DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_profiles_task_id
            ON ai_profiles(task_id)
            """
        )
        conn.commit()


def ensure_ai_tables() -> None:
    ensure_ai_tasks_table()
    ensure_ai_profiles_table()


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


def _profile_window(profile_date: str | date) -> tuple[str, str, str]:
    if isinstance(profile_date, date):
        profile_date_str = profile_date.isoformat()
        profile_date_obj = profile_date
    else:
        profile_date_str = str(profile_date)
        profile_date_obj = date.fromisoformat(profile_date_str)
    window_start = f"{profile_date_str}T00:00:00"
    window_end = f"{(profile_date_obj + timedelta(days=1)).isoformat()}T00:00:00"
    return profile_date_str, window_start, window_end


def _display_name(name: str | None, nick: str | None, user_id: int) -> str:
    name = (name or "").strip()
    nick = (nick or "").strip()
    if name and nick:
        return f"{name} ({nick})"
    return name or nick or str(user_id)


def get_latest_profile_json(user_id: int, chat_id: int, *, before_date: str | None = None) -> str | None:
    ensure_ai_profiles_table()
    params: list[Any] = [user_id, chat_id, TASK_STATUS_DONE]
    date_filter = ""
    if before_date:
        date_filter = "AND profile_date < ?"
        params.append(before_date)
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT profile_json
            FROM ai_profiles
            WHERE user_id = ? AND chat_id = ? AND status = ?
              AND profile_json IS NOT NULL
              {date_filter}
            ORDER BY profile_date DESC, updated_at DESC
            LIMIT 1
            """,
            params,
        )
        row = cur.fetchone()
    return str(row["profile_json"]) if row and row["profile_json"] else None


def collect_profile_update_candidates(
    *,
    profile_date: str | date,
    chat_id: int | None = None,
) -> list[dict[str, Any]]:
    ensure_ai_tables()
    profile_date_str, window_start, window_end = _profile_window(profile_date)
    params: list[Any] = [profile_date_str, PROFILE_MIN_MESSAGE_LENGTH, profile_date_str]
    chat_filter = ""
    if chat_id is not None:
        chat_filter = "AND mr.chat_id = ?"
        params.append(chat_id)
    params.append(PROFILE_MIN_MESSAGES)
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT
                mr.chat_id,
                mr.user_id,
                COALESCE(u.name, '') AS name,
                COALESCE(u.nick, '') AS nick,
                COUNT(*) AS message_count,
                SUM(LENGTH(TRIM(COALESCE(mr.message_text, '')))) AS text_chars
            FROM messages_reactions mr
            LEFT JOIN users u
              ON u.user_id = mr.user_id AND u.chat_id = mr.chat_id
            WHERE date(mr.date) = ?
              AND LENGTH(TRIM(COALESCE(mr.message_text, ''))) >= ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM ai_profiles p
                  WHERE p.user_id = mr.user_id
                    AND p.chat_id = mr.chat_id
                    AND p.profile_date = ?
              )
              {chat_filter}
            GROUP BY mr.chat_id, mr.user_id
            HAVING COUNT(*) >= ?
            ORDER BY mr.chat_id ASC, message_count DESC
            """,
            params,
        )
        rows = cur.fetchall()
    return [
        {
            "chat_id": int(row["chat_id"]),
            "user_id": int(row["user_id"]),
            "name": row["name"],
            "nick": row["nick"],
            "display_name": _display_name(row["name"], row["nick"], int(row["user_id"])),
            "message_count": int(row["message_count"] or 0),
            "text_chars": int(row["text_chars"] or 0),
            "profile_date": profile_date_str,
            "window_start": window_start,
            "window_end": window_end,
        }
        for row in rows
    ]


def get_profile_update_messages(
    *,
    profile_date: str | date,
    chat_id: int,
    user_id: int,
) -> list[dict[str, Any]]:
    ensure_ai_tasks_table()
    profile_date_str, _, _ = _profile_window(profile_date)
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT message_id, message_text, date
            FROM messages_reactions
            WHERE chat_id = ?
              AND user_id = ?
              AND date(date) = ?
              AND LENGTH(TRIM(COALESCE(message_text, ''))) >= ?
            ORDER BY date DESC, message_id DESC
            LIMIT ?
            """,
            (chat_id, user_id, profile_date_str, PROFILE_MIN_MESSAGE_LENGTH, PROFILE_MAX_MESSAGES),
        )
        rows = cur.fetchall()

    selected: list[sqlite3.Row] = []
    used_chars = 0
    for row in rows:
        text = str(row["message_text"] or "").strip()
        line_cost = len(text) + 64
        if selected and used_chars + line_cost > PROFILE_MESSAGES_CHAR_LIMIT:
            break
        selected.append(row)
        used_chars += line_cost

    selected.reverse()
    return [
        {
            "message_id": int(row["message_id"]),
            "date": row["date"],
            "text": str(row["message_text"] or "").strip(),
        }
        for row in selected
    ]


def build_profile_update_prompt(
    *,
    profile_date: str,
    chat_id: int,
    user_id: int,
    display_name: str,
    nick: str | None,
    message_count: int,
    messages: list[dict[str, Any]],
    previous_profile_json: str | None = None,
    previous_response: str | None = None,
    previous_error: str | None = None,
) -> str:
    previous_profile_block = previous_profile_json or "null"
    retry_block = ""
    if previous_response or previous_error:
        retry_block = f"""

Предыдущая попытка была неудачной.
Ответ модели в прошлый раз:
{previous_response or "(пусто)"}

Ошибка парсинга/валидации:
{previous_error or "(не указана)"}

Исправь ответ. Верни только валидный JSON по контракту ниже.
"""

    message_lines = []
    for item in messages:
        text = str(item["text"]).replace("\r", " ").replace("\n", " ").strip()
        message_lines.append(f"- [{item['date']}] #{item['message_id']}: {text}")
    messages_block = "\n".join(message_lines)

    return f"""Ты обновляешь компактный поведенческий профиль пользователя Telegram-чата для будущей персонализации ответов бота.

Контекст:
- profile_date: {profile_date}
- chat_id: {chat_id}
- user_id: {user_id}
- display_name: {display_name}
- nick: {nick or ""}
- подходящих сообщений за день: {message_count}

Предыдущий профиль этого пользователя в этом чате, если он есть:
{previous_profile_block}

Сообщения пользователя за день. Учитывай только этот материал и предыдущий профиль:
{messages_block}
{retry_block}

Правила анализа:
- Пиши на русском языке.
- Не выдумывай факты, намерения, биографию, отношения и предпочтения, которых нет в сообщениях или прошлом профиле.
- Сохраняй устойчивую информацию из прошлого профиля, если новые сообщения ее не опровергают.
- Удаляй устаревшие текущие темы; текущие темы должны отражать именно этот день.
- Отличай стабильные интересы пользователя от разовых тем дня.
- Если данных мало или они неоднозначны, ставь confidence = "low" и оставляй спорные массивы пустыми.
- short_summary должен быть компактным фрагментом 1-3 предложения, пригодным для будущего prompt персонализации.
- Не включай user_id/chat_id/message_id в смысловые поля профиля, если это не часть естественного описания.

Контракт ответа:
Верни только один JSON-объект без markdown, code fence, комментариев и пояснений.
Все ключи обязательны. Лишние ключи не нужны.
Массивы должны содержать только строки и не превышать указанные лимиты.

Формат:
{{
  "display_name": "string",
  "communication_style": "string",
  "stable_interests": ["max 5 strings"],
  "preferences": ["max 5 strings"],
  "current_topics": ["max 3 strings"],
  "behavior_notes": ["max 5 strings"],
  "local_memes": ["max 5 strings"],
  "facts": ["max 5 strings"],
  "do_not_assume": ["max 5 strings"],
  "confidence": "low|medium|high",
  "short_summary": "1-3 sentences in Russian"
}}
"""


def create_profile_update_tasks(
    *,
    profile_date: str | date,
    chat_id: int | None = None,
) -> dict[str, Any]:
    ensure_ai_tables()
    profile_date_str, window_start, window_end = _profile_window(profile_date)
    candidates = collect_profile_update_candidates(profile_date=profile_date_str, chat_id=chat_id)
    created_task_ids: list[int] = []
    skipped = 0

    for candidate in candidates:
        candidate_chat_id = int(candidate["chat_id"])
        candidate_user_id = int(candidate["user_id"])
        messages = get_profile_update_messages(
            profile_date=profile_date_str,
            chat_id=candidate_chat_id,
            user_id=candidate_user_id,
        )
        if len(messages) < PROFILE_MIN_MESSAGES:
            skipped += 1
            continue

        previous_profile_json = get_latest_profile_json(
            candidate_user_id,
            candidate_chat_id,
            before_date=profile_date_str,
        )
        prompt = build_profile_update_prompt(
            profile_date=profile_date_str,
            chat_id=candidate_chat_id,
            user_id=candidate_user_id,
            display_name=str(candidate["display_name"]),
            nick=str(candidate["nick"] or ""),
            message_count=int(candidate["message_count"]),
            messages=messages,
            previous_profile_json=previous_profile_json,
        )
        payload = {
            "profile_date": profile_date_str,
            "window_start": window_start,
            "window_end": window_end,
            "message_count": int(candidate["message_count"]),
            "display_name": str(candidate["display_name"]),
            "nick": str(candidate["nick"] or ""),
            "source": "messages_reactions",
        }
        now = now_iso()

        with closing(get_connection()) as conn:
            cur = conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                cur.execute(
                    """
                    SELECT 1
                    FROM ai_profiles
                    WHERE user_id = ? AND chat_id = ? AND profile_date = ?
                    """,
                    (candidate_user_id, candidate_chat_id, profile_date_str),
                )
                if cur.fetchone():
                    skipped += 1
                    conn.rollback()
                    continue

                cur.execute(
                    """
                    INSERT INTO ai_tasks (
                        task_type, status, priority, model, prompt, payload_json,
                        chat_id, user_id, request_message_id, attempt, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                    """,
                    (
                        TASK_TYPE_PROFILE_UPDATE,
                        TASK_STATUS_PENDING,
                        PROFILE_UPDATE_PRIORITY,
                        PROFILE_UPDATE_MODEL,
                        prompt,
                        json.dumps(payload, ensure_ascii=False),
                        candidate_chat_id,
                        candidate_user_id,
                        now,
                        now,
                    ),
                )
                task_id = int(cur.lastrowid)
                cur.execute(
                    """
                    INSERT INTO ai_profiles (
                        user_id, chat_id, profile_date, status, profile_json, summary_text,
                        message_count, window_start, window_end, model, task_id, error_text,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        candidate_user_id,
                        candidate_chat_id,
                        profile_date_str,
                        TASK_STATUS_PENDING,
                        int(candidate["message_count"]),
                        window_start,
                        window_end,
                        PROFILE_UPDATE_MODEL,
                        task_id,
                        now,
                        now,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                skipped += 1
                continue
        created_task_ids.append(task_id)

    return {
        "profile_date": profile_date_str,
        "chat_id": chat_id,
        "candidates": len(candidates),
        "created": len(created_task_ids),
        "skipped": skipped,
        "task_ids": created_task_ids,
        "window_start": window_start,
        "window_end": window_end,
    }


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


def clean_model_json(raw_output: str | None) -> str:
    text = (raw_output or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    return text


def validate_profile_update_output(raw_output: str | None) -> dict[str, Any]:
    text = clean_model_json(raw_output)
    if not text:
        raise ProfileUpdateError("LLM вернула пустой ответ.")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProfileUpdateError(f"Ответ LLM не является валидным JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProfileUpdateError("Ответ LLM должен быть JSON-объектом.")

    missing = sorted(PROFILE_REQUIRED_KEYS - set(parsed))
    if missing:
        raise ProfileUpdateError(f"В JSON нет обязательных ключей: {', '.join(missing)}.")

    normalized: dict[str, Any] = {}
    for key in PROFILE_STRING_FIELDS:
        value = parsed.get(key)
        if not isinstance(value, str):
            raise ProfileUpdateError(f"Поле {key} должно быть строкой.")
        normalized[key] = value.strip()

    confidence = normalized["confidence"].lower()
    if confidence not in PROFILE_CONFIDENCE_VALUES:
        raise ProfileUpdateError("Поле confidence должно быть low, medium или high.")
    normalized["confidence"] = confidence

    for key, limit in PROFILE_ARRAY_LIMITS.items():
        value = parsed.get(key)
        if not isinstance(value, list):
            raise ProfileUpdateError(f"Поле {key} должно быть массивом строк.")
        items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ProfileUpdateError(f"Поле {key} должно содержать только строки.")
            item = item.strip()
            if item:
                items.append(item)
        normalized[key] = items[:limit]

    return {
        "display_name": normalized["display_name"],
        "communication_style": normalized["communication_style"],
        "stable_interests": normalized["stable_interests"],
        "preferences": normalized["preferences"],
        "current_topics": normalized["current_topics"],
        "behavior_notes": normalized["behavior_notes"],
        "local_memes": normalized["local_memes"],
        "facts": normalized["facts"],
        "do_not_assume": normalized["do_not_assume"],
        "confidence": normalized["confidence"],
        "short_summary": normalized["short_summary"],
    }


def mark_profile_task_done(task_id: int, *, profile: dict[str, Any], raw_output: str | None = None) -> None:
    task = get_task(task_id)
    if not task:
        return
    profile_json = json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
    summary_text = str(profile.get("short_summary") or "").strip()
    now = now_iso()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            UPDATE ai_profiles
            SET status = ?, profile_json = ?, summary_text = ?, error_text = NULL,
                updated_at = ?
            WHERE task_id = ?
            """,
            (TASK_STATUS_DONE, profile_json, summary_text, now, task_id),
        )
        cur.execute(
            """
            UPDATE ai_tasks
            SET status = ?, result_text = ?, error_text = NULL, lease_until = NULL,
                updated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_DONE, raw_output or profile_json, now, now, task_id),
        )
        conn.commit()


def _rebuild_profile_retry_prompt(
    task: sqlite3.Row,
    *,
    previous_response: str | None,
    previous_error: str,
) -> str:
    payload = json.loads(task["payload_json"] or "{}")
    profile_date = str(payload.get("profile_date") or date.today().isoformat())
    chat_id = int(task["chat_id"])
    user_id = int(task["user_id"])
    messages = get_profile_update_messages(
        profile_date=profile_date,
        chat_id=chat_id,
        user_id=user_id,
    )
    previous_profile_json = get_latest_profile_json(user_id, chat_id, before_date=profile_date)
    display_name = str(payload.get("display_name") or user_id)
    nick = str(payload.get("nick") or "")
    message_count = int(payload.get("message_count") or len(messages))
    return build_profile_update_prompt(
        profile_date=profile_date,
        chat_id=chat_id,
        user_id=user_id,
        display_name=display_name,
        nick=nick,
        message_count=message_count,
        messages=messages,
        previous_profile_json=previous_profile_json,
        previous_response=previous_response,
        previous_error=previous_error,
    )


def requeue_or_fail_profile_task(
    task_id: int,
    *,
    previous_response: str | None,
    error_text: str,
) -> tuple[bool, sqlite3.Row | None]:
    task = get_task(task_id)
    if not task:
        return False, None

    attempt = int(task["attempt"] or 0)
    now = now_iso()
    if attempt < PROFILE_UPDATE_MAX_RETRY_ATTEMPT:
        retry_prompt = _rebuild_profile_retry_prompt(
            task,
            previous_response=previous_response,
            previous_error=error_text,
        )
        with closing(get_connection()) as conn:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                """
                UPDATE ai_tasks
                SET status = ?, prompt = ?, error_text = ?, attempt = attempt + 1,
                    lease_until = NULL, updated_at = ?
                WHERE id = ?
                """,
                (TASK_STATUS_PENDING, retry_prompt, error_text, now, task_id),
            )
            cur.execute(
                """
                UPDATE ai_profiles
                SET status = ?, error_text = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (TASK_STATUS_PENDING, error_text, now, task_id),
            )
            conn.commit()
        return True, get_task(task_id)

    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            UPDATE ai_tasks
            SET status = ?, error_text = ?, lease_until = NULL, updated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_FAILED, error_text, now, now, task_id),
        )
        cur.execute(
            """
            UPDATE ai_profiles
            SET status = ?, error_text = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (TASK_STATUS_FAILED, error_text, now, task_id),
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

    forbidden_tables = sorted(
        table
        for table in FORBIDDEN_TEXT_TO_SQL_TABLES
        if re.search(rf"\b{re.escape(table.lower())}\b", lowered)
    )
    if forbidden_tables:
        raise TextToSqlError("SQL обращается к закрытым для /db таблицам.")

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
