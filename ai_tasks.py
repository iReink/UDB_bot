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
TASK_TYPE_CHAT_SUMMARY = "chat_summary"
TASK_TYPE_RESPONSE = "response"
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
CHAT_SUMMARY_MODEL = "gemma4:e4b"
CHAT_SUMMARY_PRIORITY = 1
CHAT_SUMMARY_MAX_RETRY_ATTEMPT = 1
CHAT_SUMMARY_MIN_MESSAGE_LENGTH = 10
CHAT_SUMMARY_TARGET_SECONDS = 2 * 60 * 60
CHAT_SUMMARY_FORCE_SECONDS = 4 * 60 * 60
CHAT_SUMMARY_MAX_CHARS = 150
RESPONSE_MODEL = "gemma4:e4b"
RESPONSE_PRIORITY = 200
RESPONSE_MAX_RETRY_ATTEMPT = 2
RESPONSE_MAX_CHARS = 500
RESPONSE_RANDOM_COOLDOWN_SECONDS = 10 * 60
RESPONSE_DIRECT_COOLDOWN_SECONDS = 3
RESPONSE_SHORT_MEMORY_LIMIT = 30
RESPONSE_LONG_MEMORY_LIMIT = 10
RESPONSE_REACTION_IN_PROGRESS = "\U0001f440"
RESPONSE_REACTION_DONE = "\u2705"
RESPONSE_REACTION_ERROR = "\u26a0\ufe0f"
AI_TASK_LEASE_SECONDS = 180
TYPE_CHECK_MODEL = os.getenv("AI_CLASSIFIER_MODEL", "gemma4:e4b")
TYPE_CHECK_LEASE_SECONDS = 60
TYPE_CHECK_RESULT_RESPONSE = "response"
TYPE_CHECK_RESULT_TEXT_TO_SQL = "text_to_sql"
TYPE_CHECK_RESULT_IGNORE = "ignore"
TYPE_CHECK_RESULT_WEB_SEARCH = "web_search"
TYPE_CHECK_ALLOWED_RESULTS = {
    TYPE_CHECK_RESULT_RESPONSE,
    TYPE_CHECK_RESULT_TEXT_TO_SQL,
    TYPE_CHECK_RESULT_IGNORE,
    TYPE_CHECK_RESULT_WEB_SEARCH,
}
SEARCH_PLAN_MODEL = os.getenv("AI_CLASSIFIER_MODEL", "gemma4:e4b")
SEARCH_PLAN_LEASE_SECONDS = 90
SEARCH_PLAN_MAX_RETRY_ATTEMPT = 1
SEARCH_PLAN_MAX_QUERIES = 3
SEARCH_PLAN_MAX_FACTS = 5
WEB_CONTEXT_CHAR_LIMIT = 6_000

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
    "ai_summary",
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


class TypeCheckError(ValueError):
    pass


class SearchPlanError(ValueError):
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


def ensure_ai_type_checks_table() -> None:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_type_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL DEFAULT 'pending',
                model TEXT NOT NULL,
                prompt TEXT NOT NULL,
                message_text TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                request_message_id INTEGER NOT NULL,
                trigger_reason TEXT NOT NULL,
                result_type TEXT,
                error_text TEXT,
                lease_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT,
                UNIQUE(chat_id, request_message_id)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_type_checks_queue
            ON ai_type_checks(status, created_at ASC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_type_checks_message
            ON ai_type_checks(chat_id, request_message_id)
            """
        )
        conn.commit()


def ensure_ai_search_plans_table() -> None:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_search_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL DEFAULT 'pending',
                model TEXT NOT NULL,
                prompt TEXT NOT NULL,
                message_text TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                request_message_id INTEGER NOT NULL,
                trigger_reason TEXT NOT NULL,
                result_json TEXT,
                error_text TEXT,
                attempt INTEGER NOT NULL DEFAULT 0,
                lease_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT,
                UNIQUE(chat_id, request_message_id)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_search_plans_queue
            ON ai_search_plans(status, created_at ASC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_search_plans_message
            ON ai_search_plans(chat_id, request_message_id)
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


def ensure_ai_summary_table() -> None:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                task_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                summary_text TEXT,
                message_count INTEGER NOT NULL DEFAULT 0,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                model TEXT NOT NULL,
                error_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT,
                UNIQUE(chat_id, window_start, window_end)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_summary_chat_window
            ON ai_summary(chat_id, window_end DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_summary_task_id
            ON ai_summary(task_id)
            """
        )
        conn.commit()


def ensure_ai_tables() -> None:
    ensure_ai_tasks_table()
    ensure_ai_type_checks_table()
    ensure_ai_search_plans_table()
    ensure_ai_profiles_table()
    ensure_ai_summary_table()


def read_schema_markdown() -> str:
    try:
        return SCHEMA_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "Файл STATS_DB_SCHEMA.md не найден. Используй только известную схему SQLite из проекта."


def build_text_to_sql_prompt(
    user_query: str,
    chat_id: int,
    *,
    requester_user_id: int | None = None,
    requester_name: str | None = None,
    requester_nick: str | None = None,
    previous_sql: str | None = None,
    previous_error: str | None = None,
) -> str:
    schema = read_schema_markdown()
    requester_user_id_text = str(requester_user_id) if requester_user_id is not None else "unknown"
    requester_name_text = (requester_name or "").strip() or "unknown"
    requester_nick_text = (requester_nick or "").strip() or "unknown"
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

Запросивший пользователь:
- requester_user_id: {requester_user_id_text}
- requester_name: {requester_name_text}
- requester_nick: {requester_nick_text}

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
- Если в запросе есть "я", "мой", "мне", "меня", "мои" или другой личный контекст, это означает requester_user_id = {requester_user_id_text}; добавь фильтр по user_id = {requester_user_id_text}.
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


def build_type_check_prompt(*, message_text: str, trigger_reason: str) -> str:
    clean_text = message_text.replace("\r", " ").strip()
    return f"""Классифицируй сообщение, адресованное Telegram-боту.

Верни только одно слово:
response — обычный разговорный ответ бота;
text_to_sql — пользователь просит статистику, аналитику, подсчёт, топ, сравнение или факт из базы данных чата;
ignore — сообщение адресовано боту, но не требует ответа или действия;
web_search — нужен внешний интернет-контекст: актуальные события, новости, текущие данные или научные/справочные факты.

Выбирай text_to_sql только если нужен запрос к истории/статистике чата.
Выбирай web_search, если для ответа нужны факты из внешнего мира или свежая информация.
Выбирай response для шуток, мнений, обычных вопросов и разговорных обращений.
Не добавляй пояснения, markdown или JSON.

trigger_reason: {trigger_reason}

Сообщение:
{clean_text}
"""


def has_pending_type_check(*, chat_id: int, request_message_id: int | None = None) -> bool:
    ensure_ai_type_checks_table()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        if request_message_id is None:
            cur.execute(
                """
                SELECT 1
                FROM ai_type_checks
                WHERE chat_id = ?
                  AND status IN (?, ?)
                LIMIT 1
                """,
                (chat_id, TASK_STATUS_PENDING, TASK_STATUS_PROCESSING),
            )
        else:
            cur.execute(
                """
                SELECT 1
                FROM ai_type_checks
                WHERE chat_id = ?
                  AND request_message_id = ?
                  AND status IN (?, ?)
                LIMIT 1
                """,
                (chat_id, request_message_id, TASK_STATUS_PENDING, TASK_STATUS_PROCESSING),
            )
        return cur.fetchone() is not None


def create_type_check_task(
    *,
    chat_id: int,
    user_id: int,
    request_message_id: int,
    message_text: str,
    trigger_reason: str,
) -> int | None:
    ensure_ai_type_checks_table()
    if chat_id >= 0:
        return None

    prompt = build_type_check_prompt(message_text=message_text, trigger_reason=trigger_reason)
    now = now_iso()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT 1
            FROM ai_type_checks
            WHERE chat_id = ?
              AND request_message_id = ?
              AND status IN (?, ?)
            LIMIT 1
            """,
            (chat_id, request_message_id, TASK_STATUS_PENDING, TASK_STATUS_PROCESSING),
        )
        if cur.fetchone():
            conn.rollback()
            return None
        try:
            cur.execute(
                """
                INSERT INTO ai_type_checks (
                    status, model, prompt, message_text, chat_id, user_id,
                    request_message_id, trigger_reason, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    TASK_STATUS_PENDING,
                    TYPE_CHECK_MODEL,
                    prompt,
                    message_text,
                    chat_id,
                    user_id,
                    request_message_id,
                    trigger_reason,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return None
        task_id = int(cur.lastrowid)
        conn.commit()
    return task_id


def build_search_plan_prompt(
    *,
    message_text: str,
    trigger_reason: str,
    previous_response: str | None = None,
    previous_error: str | None = None,
) -> str:
    clean_text = message_text.replace("\r", " ").strip()
    retry_block = ""
    if previous_response or previous_error:
        retry_block = f"""

Предыдущая попытка была неудачной.
Ответ модели:
{previous_response or "(пусто)"}

Ошибка:
{previous_error or "(не указана)"}

Верни исправленный JSON строго по контракту.
"""

    return f"""Составь план веб-поиска для ответа Telegram-бота.

Текущая дата: {date.today().isoformat()}
trigger_reason: {trigger_reason}

Сообщение пользователя:
{clean_text}

Верни только JSON без markdown, code fence и пояснений:
{{
  "queries": ["1-3 коротких поисковых запроса"],
  "needed_facts": ["1-5 фактов, которые нужно проверить"],
  "answer_strategy": "короткая инструкция на русском, как собрать ответ"
}}

Правила:
- queries должны быть конкретными поисковыми запросами, а не пересказом сообщения.
- Если вопрос требует расчёта, ищи исходные величины отдельными запросами.
- Для актуальных событий добавляй год или слова "сейчас", "сегодня", если это помогает.
- Не придумывай факты, только планируй что искать.

Пример:
Сообщение: сколько комаров нужно чтобы высосать человека
JSON:
{{
  "queries": ["сколько крови в организме взрослого человека", "сколько крови выпивает комар за один укус"],
  "needed_facts": ["объём крови взрослого человека", "объём крови за один укус комара"],
  "answer_strategy": "Найти обе величины, привести их к одним единицам и разделить объём крови человека на объём крови за укус."
}}
{retry_block}
"""


def create_search_plan_task(
    *,
    chat_id: int,
    user_id: int,
    request_message_id: int,
    message_text: str,
    trigger_reason: str,
) -> int | None:
    ensure_ai_search_plans_table()
    if chat_id >= 0:
        return None

    prompt = build_search_plan_prompt(message_text=message_text, trigger_reason=trigger_reason)
    now = now_iso()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT 1
            FROM ai_search_plans
            WHERE chat_id = ?
              AND request_message_id = ?
              AND status IN (?, ?)
            LIMIT 1
            """,
            (chat_id, request_message_id, TASK_STATUS_PENDING, TASK_STATUS_PROCESSING),
        )
        if cur.fetchone():
            conn.rollback()
            return None
        try:
            cur.execute(
                """
                INSERT INTO ai_search_plans (
                    status, model, prompt, message_text, chat_id, user_id,
                    request_message_id, trigger_reason, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    TASK_STATUS_PENDING,
                    SEARCH_PLAN_MODEL,
                    prompt,
                    message_text,
                    chat_id,
                    user_id,
                    request_message_id,
                    trigger_reason,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return None
        task_id = int(cur.lastrowid)
        conn.commit()
    return task_id


def create_text_to_sql_task(
    *,
    chat_id: int,
    user_id: int,
    request_message_id: int,
    user_query: str,
    requester_name: str | None = None,
    requester_nick: str | None = None,
) -> int:
    ensure_ai_tasks_table()
    prompt = build_text_to_sql_prompt(
        user_query=user_query,
        chat_id=chat_id,
        requester_user_id=user_id,
        requester_name=requester_name,
        requester_nick=requester_nick,
    )
    payload = {
        "user_query": user_query,
        "chat_id": chat_id,
        "request_message_id": request_message_id,
        "created_by_user_id": user_id,
        "requester_name": requester_name,
        "requester_nick": requester_nick,
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


def local_now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _summary_dt(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    return parse_iso(str(value))


def _summary_iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def has_non_summary_ai_backlog() -> bool:
    ensure_ai_tasks_table()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1
            FROM ai_tasks
            WHERE status IN (?, ?)
              AND task_type != ?
            LIMIT 1
            """,
            (TASK_STATUS_PENDING, TASK_STATUS_PROCESSING, TASK_TYPE_CHAT_SUMMARY),
        )
        return cur.fetchone() is not None


def has_pending_chat_summary(chat_id: int) -> bool:
    ensure_ai_tables()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1
            FROM ai_summary
            WHERE chat_id = ? AND status IN (?, ?)
            LIMIT 1
            """,
            (chat_id, TASK_STATUS_PENDING, TASK_STATUS_PROCESSING),
        )
        return cur.fetchone() is not None


def get_latest_done_chat_summary(chat_id: int) -> sqlite3.Row | None:
    ensure_ai_summary_table()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT *
            FROM ai_summary
            WHERE chat_id = ? AND status = ?
            ORDER BY window_end DESC, id DESC
            LIMIT 1
            """,
            (chat_id, TASK_STATUS_DONE),
        )
        return cur.fetchone()


def get_chat_summary_window(chat_id: int, *, now_dt: datetime | None = None) -> tuple[datetime, datetime, float]:
    now_dt = _summary_dt(now_dt) or local_now()
    latest = get_latest_done_chat_summary(chat_id)
    if latest and latest["window_end"]:
        window_start = _summary_dt(latest["window_end"]) or (now_dt - timedelta(seconds=CHAT_SUMMARY_FORCE_SECONDS))
        elapsed_seconds = max(0.0, (now_dt - window_start).total_seconds())
    else:
        window_start = now_dt - timedelta(seconds=CHAT_SUMMARY_FORCE_SECONDS)
        elapsed_seconds = float(CHAT_SUMMARY_FORCE_SECONDS)
    return window_start, now_dt, elapsed_seconds


def get_chat_summary_messages(
    *,
    chat_id: int,
    window_start: datetime | str,
    window_end: datetime | str,
) -> list[dict[str, Any]]:
    ensure_ai_tasks_table()
    start_iso = _summary_iso(_summary_dt(window_start) or local_now())
    end_iso = _summary_iso(_summary_dt(window_end) or local_now())
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                mr.message_id,
                mr.user_id,
                mr.message_text,
                mr.date,
                COALESCE(u.name, '') AS name,
                COALESCE(u.nick, '') AS nick
            FROM messages_reactions mr
            LEFT JOIN users u
              ON u.user_id = mr.user_id AND u.chat_id = mr.chat_id
            WHERE mr.chat_id = ?
              AND mr.date > ?
              AND mr.date <= ?
              AND LENGTH(TRIM(COALESCE(mr.message_text, ''))) >= ?
            ORDER BY mr.date ASC, mr.message_id ASC
            """,
            (chat_id, start_iso, end_iso, CHAT_SUMMARY_MIN_MESSAGE_LENGTH),
        )
        rows = cur.fetchall()
    return [
        {
            "message_id": int(row["message_id"]),
            "user_id": int(row["user_id"]),
            "date": row["date"],
            "name": row["name"],
            "nick": row["nick"],
            "text": str(row["message_text"] or "").strip(),
        }
        for row in rows
    ]


def build_chat_summary_prompt(
    *,
    chat_id: int,
    window_start: str,
    window_end: str,
    messages: list[dict[str, Any]],
    previous_response: str | None = None,
    previous_error: str | None = None,
) -> str:
    retry_block = ""
    if previous_response or previous_error:
        retry_block = f"""

Предыдущая попытка была неудачной.
Ответ модели в прошлый раз:
{previous_response or "(пусто)"}

Ошибка:
{previous_error or "(не указана)"}

Исправь ответ и строго уложись в контракт.
"""

    message_lines = []
    for item in messages:
        text = str(item["text"]).replace("\r", " ").replace("\n", " ").strip()
        name = str(item.get("name") or "").strip() or "unknown"
        nick = str(item.get("nick") or "").strip() or ""
        message_lines.append(
            f"- [{item['date']}] user_id={item['user_id']} name={name} nick={nick}: {text}"
        )
    messages_block = "\n".join(message_lines)

    return f"""Ты сжимаешь короткий период переписки Telegram-чата в компактное summary для долговременной памяти бота.

Контекст:
- chat_id: {chat_id}
- window_start: {window_start}
- window_end: {window_end}
- сообщений после фильтра: {len(messages)}

Контракт ответа:
- Верни только короткое summary на русском языке.
- Максимум {CHAT_SUMMARY_MAX_CHARS} символов.
- Без markdown, кавычек, списков, code fence и пояснений.
- Не перечисляй user_id.
- Если уместно, используй имена людей.
- Сожми главную тему, событие или настроение периода.
- Не выдумывай факты и не добавляй деталей, которых нет в сообщениях.
{retry_block}
Сообщения:
{messages_block}
"""


def create_chat_summary_task_for_chat(
    *,
    chat_id: int,
    now_dt: datetime | None = None,
    queue_busy: bool | None = None,
) -> dict[str, Any]:
    ensure_ai_tables()
    if chat_id >= 0:
        return {"chat_id": chat_id, "created": 0, "skipped_reason": "not_group_chat"}
    if has_pending_chat_summary(chat_id):
        return {"chat_id": chat_id, "created": 0, "skipped_reason": "summary_already_pending"}

    window_start_dt, window_end_dt, elapsed_seconds = get_chat_summary_window(chat_id, now_dt=now_dt)
    if elapsed_seconds < CHAT_SUMMARY_TARGET_SECONDS:
        return {"chat_id": chat_id, "created": 0, "skipped_reason": "too_early"}

    queue_busy = has_non_summary_ai_backlog() if queue_busy is None else bool(queue_busy)
    if queue_busy and elapsed_seconds < CHAT_SUMMARY_FORCE_SECONDS:
        return {"chat_id": chat_id, "created": 0, "skipped_reason": "queue_busy"}

    window_start = _summary_iso(window_start_dt)
    window_end = _summary_iso(window_end_dt)
    messages = get_chat_summary_messages(
        chat_id=chat_id,
        window_start=window_start,
        window_end=window_end,
    )
    if not messages:
        return {"chat_id": chat_id, "created": 0, "skipped_reason": "no_messages"}

    prompt = build_chat_summary_prompt(
        chat_id=chat_id,
        window_start=window_start,
        window_end=window_end,
        messages=messages,
    )
    payload = {
        "chat_id": chat_id,
        "window_start": window_start,
        "window_end": window_end,
        "message_count": len(messages),
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
                FROM ai_summary
                WHERE chat_id = ? AND status IN (?, ?)
                LIMIT 1
                """,
                (chat_id, TASK_STATUS_PENDING, TASK_STATUS_PROCESSING),
            )
            if cur.fetchone():
                conn.rollback()
                return {"chat_id": chat_id, "created": 0, "skipped_reason": "summary_already_pending"}
            cur.execute(
                """
                INSERT INTO ai_tasks (
                    task_type, status, priority, model, prompt, payload_json,
                    chat_id, user_id, request_message_id, attempt, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?)
                """,
                (
                    TASK_TYPE_CHAT_SUMMARY,
                    TASK_STATUS_PENDING,
                    CHAT_SUMMARY_PRIORITY,
                    CHAT_SUMMARY_MODEL,
                    prompt,
                    json.dumps(payload, ensure_ascii=False),
                    chat_id,
                    now,
                    now,
                ),
            )
            task_id = int(cur.lastrowid)
            cur.execute(
                """
                INSERT INTO ai_summary (
                    chat_id, task_id, status, summary_text, message_count,
                    window_start, window_end, model, error_text,
                    created_at, updated_at, finished_at
                )
                VALUES (?, ?, ?, NULL, ?, ?, ?, ?, NULL, ?, ?, NULL)
                """,
                (
                    chat_id,
                    task_id,
                    TASK_STATUS_PENDING,
                    len(messages),
                    window_start,
                    window_end,
                    CHAT_SUMMARY_MODEL,
                    now,
                    now,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            return {"chat_id": chat_id, "created": 0, "skipped_reason": "duplicate_window"}

    return {
        "chat_id": chat_id,
        "created": 1,
        "task_id": task_id,
        "message_count": len(messages),
        "window_start": window_start,
        "window_end": window_end,
    }


def create_due_chat_summary_tasks(
    *,
    chat_ids: list[int],
    now_dt: datetime | None = None,
) -> dict[str, Any]:
    ensure_ai_tables()
    now_dt = _summary_dt(now_dt) or local_now()
    queue_busy = has_non_summary_ai_backlog()
    results = [
        create_chat_summary_task_for_chat(chat_id=int(chat_id), now_dt=now_dt, queue_busy=queue_busy)
        for chat_id in chat_ids
        if int(chat_id) < 0
    ]
    created_task_ids = [int(item["task_id"]) for item in results if item.get("created")]
    skipped: dict[str, int] = {}
    for item in results:
        reason = str(item.get("skipped_reason") or "created")
        skipped[reason] = skipped.get(reason, 0) + (0 if item.get("created") else 1)
    return {
        "checked": len(results),
        "created": len(created_task_ids),
        "task_ids": created_task_ids,
        "queue_busy": queue_busy,
        "skipped": skipped,
    }


def has_pending_response_task(chat_id: int) -> bool:
    ensure_ai_tasks_table()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1
            FROM ai_tasks
            WHERE chat_id = ?
              AND task_type = ?
              AND status IN (?, ?)
            LIMIT 1
            """,
            (chat_id, TASK_TYPE_RESPONSE, TASK_STATUS_PENDING, TASK_STATUS_PROCESSING),
        )
        return cur.fetchone() is not None


def get_response_cooldown_left(chat_id: int, *, cooldown_seconds: int) -> int:
    ensure_ai_tasks_table()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COALESCE(finished_at, updated_at, created_at) AS last_at
            FROM ai_tasks
            WHERE chat_id = ?
              AND task_type = ?
              AND status = ?
            ORDER BY COALESCE(finished_at, updated_at, created_at) DESC
            LIMIT 1
            """,
            (chat_id, TASK_TYPE_RESPONSE, TASK_STATUS_DONE),
        )
        row = cur.fetchone()
    last_at = parse_iso(row["last_at"]) if row and row["last_at"] else None
    if not last_at:
        return 0
    elapsed = (utcnow() - last_at).total_seconds()
    return max(0, int(cooldown_seconds - elapsed))


def get_response_short_memory(*, chat_id: int, before_message_id: int) -> list[dict[str, Any]]:
    ensure_ai_tasks_table()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                mr.message_id,
                mr.user_id,
                mr.message_text,
                mr.date,
                COALESCE(u.name, '') AS name,
                COALESCE(u.nick, '') AS nick
            FROM messages_reactions mr
            LEFT JOIN users u
              ON u.user_id = mr.user_id AND u.chat_id = mr.chat_id
            WHERE mr.chat_id = ?
              AND mr.message_id < ?
              AND LENGTH(TRIM(COALESCE(mr.message_text, ''))) > 0
            ORDER BY mr.message_id DESC
            LIMIT ?
            """,
            (chat_id, before_message_id, RESPONSE_SHORT_MEMORY_LIMIT),
        )
        rows = cur.fetchall()
    rows = list(reversed(rows))
    return [
        {
            "message_id": int(row["message_id"]),
            "user_id": int(row["user_id"]),
            "date": row["date"],
            "name": row["name"],
            "nick": row["nick"],
            "text": str(row["message_text"] or "").strip(),
        }
        for row in rows
    ]


def get_response_long_memory(*, chat_id: int) -> list[dict[str, Any]]:
    ensure_ai_summary_table()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT window_start, window_end, summary_text
            FROM ai_summary
            WHERE chat_id = ?
              AND status = ?
              AND summary_text IS NOT NULL
              AND TRIM(summary_text) != ''
            ORDER BY window_end DESC, id DESC
            LIMIT ?
            """,
            (chat_id, TASK_STATUS_DONE, RESPONSE_LONG_MEMORY_LIMIT),
        )
        rows = cur.fetchall()
    rows = list(reversed(rows))
    return [
        {
            "window_start": row["window_start"],
            "window_end": row["window_end"],
            "summary_text": row["summary_text"],
        }
        for row in rows
    ]


def build_response_prompt(
    *,
    chat_id: int,
    request_message_id: int,
    requester_user_id: int,
    requester_name: str,
    requester_nick: str | None,
    message_text: str,
    trigger_reason: str,
    short_memory: list[dict[str, Any]],
    long_memory: list[dict[str, Any]],
    profile_json: str | None,
    web_context: str | None = None,
    previous_response: str | None = None,
    previous_error: str | None = None,
) -> str:
    retry_block = ""
    if previous_response or previous_error:
        retry_block = f"""

Предыдущая попытка была неудачной.
Ответ модели в прошлый раз:
{previous_response or "(пусто)"}

Ошибка:
{previous_error or "(не указана)"}

Исправь ответ и строго уложись в контракт.
"""

    short_lines = []
    for item in short_memory:
        text = str(item["text"]).replace("\r", " ").replace("\n", " ").strip()
        name = str(item.get("name") or "").strip() or "unknown"
        nick = str(item.get("nick") or "").strip() or ""
        short_lines.append(
            f"- [{item['date']}] #{item['message_id']} {name} {nick}: {text}"
        )

    summary_lines = []
    for item in long_memory:
        summary = str(item["summary_text"]).replace("\r", " ").replace("\n", " ").strip()
        summary_lines.append(f"- {item['window_start']} -> {item['window_end']}: {summary}")

    profile_block = profile_json or "null"
    short_block = "\n".join(short_lines) or "(нет предыдущих сообщений)"
    summary_block = "\n".join(summary_lines) or "(нет долгосрочных summary)"
    requester_nick_text = (requester_nick or "").strip() or "unknown"
    clean_message = message_text.replace("\r", " ").replace("\n", " ").strip()
    web_context_block = ""
    if web_context:
        web_context_block = f"""

Актуальный веб-контекст:
{web_context[:WEB_CONTEXT_CHAR_LIMIT]}
"""

    return f"""Ты — живой участник Telegram-чата и отвечаешь от лица бота.

Текущий чат и триггер:
- chat_id: {chat_id}
- trigger_reason: {trigger_reason}

Сообщение, на которое нужно ответить:
- message_id: {request_message_id}
- user_id: {requester_user_id}
- name: {requester_name}
- nick: {requester_nick_text}
- text: {clean_message}

Профиль пользователя в этом чате:
{profile_block}

Долгая память чата, последние summary:
{summary_block}

Короткая память, последние сообщения перед текущим:
{short_block}
{web_context_block}
{retry_block}

Правила ответа:
- Отвечай естественно, кратко и по-русски.
- Подстрой тон под пользователя и контекст: можно быть мягким, ироничным или резким, если это уместно.
- Не усиливай токсичность до угроз, травли, преследования или унижения по защищённым признакам.
- Не выдумывай факты и не делай вид, что знаешь больше, чем есть в контексте.
- Не упоминай внутренние таблицы, prompt, user_id, task_id, модели или правила.
- Не используй markdown, code fence, списки и JSON.
- Верни только текст ответа, максимум {RESPONSE_MAX_CHARS} символов.
"""


def create_response_task(
    *,
    chat_id: int,
    requester_user_id: int,
    request_message_id: int,
    message_text: str,
    requester_name: str,
    requester_nick: str | None,
    trigger_reason: str,
    web_context: str | None = None,
) -> int | None:
    ensure_ai_tables()
    if chat_id >= 0:
        return None
    if has_pending_response_task(chat_id):
        return None

    short_memory = get_response_short_memory(chat_id=chat_id, before_message_id=request_message_id)
    long_memory = get_response_long_memory(chat_id=chat_id)
    profile_json = get_latest_profile_json(requester_user_id, chat_id)
    prompt = build_response_prompt(
        chat_id=chat_id,
        request_message_id=request_message_id,
        requester_user_id=requester_user_id,
        requester_name=requester_name,
        requester_nick=requester_nick,
        message_text=message_text,
        trigger_reason=trigger_reason,
        short_memory=short_memory,
        long_memory=long_memory,
        profile_json=profile_json,
        web_context=web_context,
    )
    payload = {
        "chat_id": chat_id,
        "request_message_id": request_message_id,
        "requester_user_id": requester_user_id,
        "requester_name": requester_name,
        "requester_nick": requester_nick,
        "message_text": message_text,
        "trigger_reason": trigger_reason,
        "short_memory_count": len(short_memory),
        "long_memory_count": len(long_memory),
        "has_profile": bool(profile_json),
        "has_web_context": bool(web_context),
        "web_context": web_context,
    }
    created_at = now_iso()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT 1
            FROM ai_tasks
            WHERE chat_id = ?
              AND task_type = ?
              AND status IN (?, ?)
            LIMIT 1
            """,
            (chat_id, TASK_TYPE_RESPONSE, TASK_STATUS_PENDING, TASK_STATUS_PROCESSING),
        )
        if cur.fetchone():
            conn.rollback()
            return None
        cur.execute(
            """
            INSERT INTO ai_tasks (
                task_type, status, priority, model, prompt, payload_json,
                chat_id, user_id, request_message_id, attempt, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                TASK_TYPE_RESPONSE,
                TASK_STATUS_PENDING,
                RESPONSE_PRIORITY,
                RESPONSE_MODEL,
                prompt,
                json.dumps(payload, ensure_ascii=False),
                chat_id,
                requester_user_id,
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


def claim_next_type_check() -> dict[str, Any] | None:
    ensure_ai_type_checks_table()
    now = now_iso()
    lease_until = (utcnow() + timedelta(seconds=TYPE_CHECK_LEASE_SECONDS)).isoformat()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT *
            FROM ai_type_checks
            WHERE status = ?
               OR (status = ? AND lease_until IS NOT NULL AND lease_until <= ?)
            ORDER BY created_at ASC
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
            UPDATE ai_type_checks
            SET status = ?, lease_until = ?, updated_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_PROCESSING, lease_until, now, task_id),
        )
        cur.execute("SELECT * FROM ai_type_checks WHERE id = ?", (task_id,))
        claimed_row = cur.fetchone()
        conn.commit()
    return serialize_type_check(claimed_row)


def serialize_type_check(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "status": row["status"],
        "model": row["model"],
        "prompt": row["prompt"],
        "message_text": row["message_text"],
        "chat_id": int(row["chat_id"]),
        "user_id": int(row["user_id"]),
        "request_message_id": int(row["request_message_id"]),
        "trigger_reason": row["trigger_reason"],
        "created_at": row["created_at"],
        "lease_until": row["lease_until"],
    }


def claim_next_search_plan() -> dict[str, Any] | None:
    ensure_ai_search_plans_table()
    now = now_iso()
    lease_until = (utcnow() + timedelta(seconds=SEARCH_PLAN_LEASE_SECONDS)).isoformat()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT *
            FROM ai_search_plans
            WHERE status = ?
               OR (status = ? AND lease_until IS NOT NULL AND lease_until <= ?)
            ORDER BY created_at ASC
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
            UPDATE ai_search_plans
            SET status = ?, lease_until = ?, updated_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_PROCESSING, lease_until, now, task_id),
        )
        cur.execute("SELECT * FROM ai_search_plans WHERE id = ?", (task_id,))
        claimed_row = cur.fetchone()
        conn.commit()
    return serialize_search_plan(claimed_row)


def serialize_search_plan(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "status": row["status"],
        "model": row["model"],
        "prompt": row["prompt"],
        "message_text": row["message_text"],
        "chat_id": int(row["chat_id"]),
        "user_id": int(row["user_id"]),
        "request_message_id": int(row["request_message_id"]),
        "trigger_reason": row["trigger_reason"],
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


def get_type_check(task_id: int) -> sqlite3.Row | None:
    ensure_ai_type_checks_table()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM ai_type_checks WHERE id = ?", (task_id,))
        return cur.fetchone()


def get_search_plan(task_id: int) -> sqlite3.Row | None:
    ensure_ai_search_plans_table()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM ai_search_plans WHERE id = ?", (task_id,))
        return cur.fetchone()


def validate_type_check_output(raw_output: str | None) -> str:
    text = (raw_output or "").strip().lower()
    if not text:
        raise TypeCheckError("LLM вернула пустой тип.")
    if text.startswith("```") or text.endswith("```"):
        raise TypeCheckError("Type-check не должен содержать markdown/code fence.")
    if text.startswith("{") or text.startswith("["):
        raise TypeCheckError("Type-check не должен быть JSON.")
    if "\n" in text or "\r" in text:
        raise TypeCheckError("Type-check должен быть одним словом.")
    text = text.strip(" .,:;!?\"'`")
    if text not in TYPE_CHECK_ALLOWED_RESULTS:
        raise TypeCheckError(f"Недопустимый type-check результат: {text!r}.")
    return text


def validate_search_plan_output(raw_output: str | None) -> dict[str, Any]:
    try:
        cleaned = clean_model_json(raw_output)
        data = json.loads(cleaned)
    except Exception as exc:
        raise SearchPlanError(f"Не удалось разобрать JSON search plan: {exc}") from exc
    if not isinstance(data, dict):
        raise SearchPlanError("Search plan должен быть JSON-объектом.")

    queries = data.get("queries")
    needed_facts = data.get("needed_facts")
    answer_strategy = data.get("answer_strategy")
    if not isinstance(queries, list) or not queries:
        raise SearchPlanError("Search plan должен содержать непустой массив queries.")
    if len(queries) > SEARCH_PLAN_MAX_QUERIES:
        raise SearchPlanError(f"Search plan содержит больше {SEARCH_PLAN_MAX_QUERIES} queries.")
    clean_queries: list[str] = []
    for query in queries:
        text = str(query or "").strip()
        if not text:
            raise SearchPlanError("Search plan содержит пустой query.")
        clean_queries.append(text[:200])

    if not isinstance(needed_facts, list):
        raise SearchPlanError("Search plan должен содержать массив needed_facts.")
    if len(needed_facts) > SEARCH_PLAN_MAX_FACTS:
        raise SearchPlanError(f"Search plan содержит больше {SEARCH_PLAN_MAX_FACTS} needed_facts.")
    clean_facts = [str(fact or "").strip()[:200] for fact in needed_facts if str(fact or "").strip()]
    if not clean_facts:
        raise SearchPlanError("Search plan должен содержать хотя бы один needed_fact.")

    strategy = str(answer_strategy or "").strip()
    if not strategy:
        raise SearchPlanError("Search plan должен содержать answer_strategy.")

    return {
        "queries": clean_queries,
        "needed_facts": clean_facts,
        "answer_strategy": strategy[:600],
    }


def mark_search_plan_done(task_id: int, *, result: dict[str, Any]) -> None:
    now = now_iso()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE ai_search_plans
            SET status = ?, result_json = ?, error_text = NULL,
                lease_until = NULL, updated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_DONE, json.dumps(result, ensure_ascii=False), now, now, task_id),
        )
        conn.commit()


def requeue_or_fail_search_plan(
    task_id: int,
    *,
    previous_response: str | None,
    error_text: str,
) -> tuple[bool, sqlite3.Row | None]:
    task = get_search_plan(task_id)
    if not task:
        return False, None

    attempt = int(task["attempt"] or 0)
    now = now_iso()
    if attempt < SEARCH_PLAN_MAX_RETRY_ATTEMPT:
        retry_prompt = build_search_plan_prompt(
            message_text=str(task["message_text"] or ""),
            trigger_reason=str(task["trigger_reason"] or "web_search"),
            previous_response=previous_response,
            previous_error=error_text,
        )
        with closing(get_connection()) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE ai_search_plans
                SET status = ?, prompt = ?, error_text = ?, attempt = attempt + 1,
                    lease_until = NULL, updated_at = ?
                WHERE id = ?
                """,
                (TASK_STATUS_PENDING, retry_prompt, error_text, now, task_id),
            )
            conn.commit()
        return True, get_search_plan(task_id)

    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE ai_search_plans
            SET status = ?, error_text = ?, lease_until = NULL,
                updated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_FAILED, error_text, now, now, task_id),
        )
        conn.commit()
    return False, get_search_plan(task_id)


def mark_type_check_done(task_id: int, *, result_type: str) -> None:
    now = now_iso()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE ai_type_checks
            SET status = ?, result_type = ?, error_text = NULL,
                lease_until = NULL, updated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_DONE, result_type, now, now, task_id),
        )
        conn.commit()


def mark_type_check_failed(task_id: int, *, error_text: str) -> None:
    now = now_iso()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE ai_type_checks
            SET status = ?, error_text = ?, lease_until = NULL,
                updated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_FAILED, error_text, now, now, task_id),
        )
        conn.commit()


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
            requester_user_id=int(payload.get("created_by_user_id") or task["user_id"]),
            requester_name=str(payload.get("requester_name") or ""),
            requester_nick=str(payload.get("requester_nick") or ""),
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


def validate_response_output(raw_output: str | None) -> str:
    text = (raw_output or "").strip()
    if not text:
        raise ValueError("LLM вернула пустой ответ.")
    if text.startswith("```") or text.endswith("```"):
        raise ValueError("Ответ не должен содержать markdown/code fence.")
    if text.startswith("{") and text.endswith("}"):
        raise ValueError("Ответ не должен быть JSON.")
    first_line = text.splitlines()[0].lstrip()
    if first_line.startswith(("- ", "* ", "1. ", "#")):
        raise ValueError("Ответ не должен быть markdown-разметкой.")
    return text


def mark_response_task_done(task_id: int, *, response_text: str, response_message_id: int | None) -> None:
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
            (TASK_STATUS_DONE, response_text, response_message_id, now, now, task_id),
        )
        conn.commit()


def _rebuild_response_retry_prompt(
    task: sqlite3.Row,
    *,
    previous_response: str | None,
    previous_error: str,
) -> str:
    payload = json.loads(task["payload_json"] or "{}")
    chat_id = int(payload.get("chat_id") or task["chat_id"])
    request_message_id = int(payload.get("request_message_id") or task["request_message_id"])
    requester_user_id = int(payload.get("requester_user_id") or task["user_id"])
    requester_name = str(payload.get("requester_name") or requester_user_id)
    requester_nick = str(payload.get("requester_nick") or "")
    message_text = str(payload.get("message_text") or "")
    trigger_reason = str(payload.get("trigger_reason") or "retry")
    web_context = payload.get("web_context")
    web_context = str(web_context) if web_context else None
    short_memory = get_response_short_memory(chat_id=chat_id, before_message_id=request_message_id)
    long_memory = get_response_long_memory(chat_id=chat_id)
    profile_json = get_latest_profile_json(requester_user_id, chat_id)
    return build_response_prompt(
        chat_id=chat_id,
        request_message_id=request_message_id,
        requester_user_id=requester_user_id,
        requester_name=requester_name,
        requester_nick=requester_nick,
        message_text=message_text,
        trigger_reason=trigger_reason,
        short_memory=short_memory,
        long_memory=long_memory,
        profile_json=profile_json,
        web_context=web_context,
        previous_response=previous_response,
        previous_error=previous_error,
    )


def requeue_or_fail_response_task(
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
    if attempt < RESPONSE_MAX_RETRY_ATTEMPT:
        retry_prompt = _rebuild_response_retry_prompt(
            task,
            previous_response=previous_response,
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


def validate_chat_summary_output(raw_output: str | None) -> str:
    text = (raw_output or "").strip()
    if not text:
        raise ValueError("LLM вернула пустой summary.")
    if text.startswith("```") or text.endswith("```"):
        raise ValueError("Summary не должно содержать markdown/code fence.")
    if "\n" in text or "\r" in text:
        raise ValueError("Summary должно быть одной строкой.")
    if text.startswith(("-", "*", "1.", "1)")):
        raise ValueError("Summary не должно быть списком.")
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("«") and text.endswith("»")):
        raise ValueError("Summary не должно быть обёрнуто в кавычки.")
    return text


def mark_chat_summary_task_done(task_id: int, *, summary_text: str) -> None:
    now = now_iso()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            UPDATE ai_summary
            SET status = ?, summary_text = ?, error_text = NULL,
                updated_at = ?, finished_at = ?
            WHERE task_id = ?
            """,
            (TASK_STATUS_DONE, summary_text, now, now, task_id),
        )
        cur.execute(
            """
            UPDATE ai_tasks
            SET status = ?, result_text = ?, error_text = NULL, lease_until = NULL,
                updated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (TASK_STATUS_DONE, summary_text, now, now, task_id),
        )
        conn.commit()


def _rebuild_chat_summary_retry_prompt(
    task: sqlite3.Row,
    *,
    previous_response: str | None,
    previous_error: str,
) -> str:
    payload = json.loads(task["payload_json"] or "{}")
    chat_id = int(payload.get("chat_id") or task["chat_id"])
    window_start = str(payload.get("window_start") or "")
    window_end = str(payload.get("window_end") or "")
    messages = get_chat_summary_messages(
        chat_id=chat_id,
        window_start=window_start,
        window_end=window_end,
    )
    return build_chat_summary_prompt(
        chat_id=chat_id,
        window_start=window_start,
        window_end=window_end,
        messages=messages,
        previous_response=previous_response,
        previous_error=previous_error,
    )


def requeue_or_fail_chat_summary_task(
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
    if attempt < CHAT_SUMMARY_MAX_RETRY_ATTEMPT:
        retry_prompt = _rebuild_chat_summary_retry_prompt(
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
                UPDATE ai_summary
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
            UPDATE ai_summary
            SET status = ?, error_text = ?, updated_at = ?, finished_at = ?
            WHERE task_id = ?
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
