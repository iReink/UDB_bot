import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import random
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen
from zoneinfo import ZoneInfo

from ai_tasks import (
    TASK_STATUS_PROCESSING,
    TASK_TYPE_CHAT_SUMMARY,
    TASK_TYPE_DATA_ANALYSIS_RESPONSE,
    TASK_TYPE_DATA_ANALYSIS_SQL,
    TASK_TYPE_PROFILE_UPDATE,
    TASK_TYPE_RESPONSE,
    TASK_TYPE_TEXT_TO_SQL,
    TYPE_CHECK_RESULT_DATA_ANALYSIS,
    TYPE_CHECK_RESULT_IGNORE,
    TYPE_CHECK_RESULT_RESPONSE,
    TYPE_CHECK_RESULT_TEXT_TO_SQL,
    TYPE_CHECK_RESULT_WEB_SEARCH,
    RESPONSE_REACTION_DONE,
    RESPONSE_REACTION_ERROR,
    RESPONSE_REACTION_IN_PROGRESS,
    RESPONSE_DIRECT_COOLDOWN_SECONDS,
    DATA_ANALYSIS_RESULT_ROW_LIMIT,
    claim_next_task,
    claim_next_search_plan,
    claim_next_type_check,
    create_data_analysis_response_task,
    create_data_analysis_task,
    create_response_task,
    create_search_plan_task,
    create_text_to_sql_task,
    execute_readonly_sql,
    format_data_analysis_preview,
    format_sql_result_for_telegram,
    get_data_analysis_by_task,
    get_task,
    get_response_cooldown_left,
    get_search_plan,
    get_text_to_sql_cooldown,
    get_type_check,
    mark_chat_summary_task_done,
    mark_data_analysis_done,
    mark_data_analysis_sql_done,
    mark_task_done,
    mark_profile_task_done,
    mark_response_task_done,
    mark_search_plan_done,
    mark_type_check_done,
    mark_type_check_failed,
    requeue_or_fail_search_plan,
    requeue_or_fail_chat_summary_task,
    requeue_or_fail_data_analysis_response_task,
    requeue_or_fail_data_analysis_sql_task,
    requeue_or_fail_profile_task,
    requeue_or_fail_response_task,
    requeue_or_fail_task,
    validate_chat_summary_output,
    validate_profile_update_output,
    validate_response_output,
    validate_search_plan_output,
    validate_text_to_sql,
    validate_type_check_output,
)
from web_search import WebSearchError, build_web_context
from db import WEB_CHAT_MEDIA_DIR, ensure_web_chat_media_schema
from auth_code import (
    AuthCodeConflictError,
    AuthCodeExpiredError,
    AuthCodeInvalidError,
    AuthCodeUsedError,
    consume_auth_code,
)
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sits import normalize_sits
from group_event_engine import EVENT_COST, GroupEventEngine, JOIN_COST
from masturbate_store import MasturbateStore
from google_calendar_integration import (
    TARGET_CHAT_ID,
    create_calendar_event,
    delete_calendar_event,
    update_calendar_event,
)


BASE_DIR = Path(__file__).resolve().parents[1]
DB_FILE = BASE_DIR / "stats.db"
WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

COOKIE_NAME = "udb_web_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
TELEGRAM_AUTH_MAX_AGE_SECONDS = 60 * 60 * 24
CHAT_TITLE_CACHE_TTL_SECONDS = 12 * 60 * 60
CHAT_TITLE_FETCH_TIMEOUT_SECONDS = 0.8
IDLE_MICROSITS_IN_SIT = 1000
IDLE_MAX_LEVEL = 20
IDLE_UNLOCK_PREVIOUS_LEVEL = 10
GEYSER_DAILY_LIMIT = 10
GEYSER_REWARD_MIN_MILLISITS = 500
GEYSER_REWARD_MAX_MILLISITS = 1000
GROUP_PREPARE_DELAY_SECONDS = 10 * 60
GROUP_JOIN_WINDOW_SECONDS = 5 * 60
GROUP_EVENT_STICKER_FILE_ID = "CAACAgIAAyEFAASjKavKAAIDrGi31TwpfP-R-JI64M0v6eRnTCFxAAJMUAACITxRSq0hIi2dEdhQNgQ"
GROUP_JOIN_ANNOUNCE_MESSAGES = [
    "{name} пристраивается сбоку",
    "{name} садится на диван и смотрит",
    "Все немного двигаются чтобы дать {name} место",
    "{name} садится в центр круга",
    "{name} немного стесняется и активничает из-за угла",
    "Для {name} не нашлось лишнего стула, поэтому пришлось сесть на полу",
    "{name} тихонько подкрадывается и устраивается сзади",
    '{name} врывается в комнату с криком: "Я опоздал?"',
    "К всеобщей радости, {name} наконец-то с нами",
    '{name} аккуратно протискивается между диваном и столом со словами "Можно я тут?"',
    "{name} появляется с тарелкой печенья и моментально становится душой компании",
]
DAILY_TIMEZONE = ZoneInfo("Asia/Yekaterinburg")
DAILY_EXPIRED_PAGE_SIZE_DEFAULT = 10
DAILY_EXPIRED_PAGE_SIZE_MAX = 50
ADMIN_IDS_DEFAULT = {6010666986, 884940984, 749027951}

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip()
SESSION_SECRET = os.getenv("WEB_SESSION_SECRET", "").strip()
AI_WORKER_TOKEN = os.getenv("AI_WORKER_TOKEN", "").strip()
logger = logging.getLogger(__name__)
ADMIN_IDS_SET = set(ADMIN_IDS_DEFAULT)
admin_ids_raw = os.getenv("ADMIN_IDS", "").strip()
if admin_ids_raw:
    parsed_admin_ids: set[int] = set()
    for chunk in admin_ids_raw.replace(";", ",").split(","):
        token = chunk.strip()
        if not token:
            continue
        try:
            parsed_admin_ids.add(int(token))
        except ValueError:
            logger.warning("Skipping invalid ADMIN_IDS value: %s", token)
    if parsed_admin_ids:
        ADMIN_IDS_SET = parsed_admin_ids
idle_income_task: asyncio.Task[None] | None = None
idle_catalog_ready = False
idle_catalog_lock = threading.Lock()
group_store = MasturbateStore()
group_engine = GroupEventEngine(group_store)

if not SESSION_SECRET and BOT_TOKEN:
    SESSION_SECRET = hashlib.sha256(f"{BOT_TOKEN}:web-session".encode("utf-8")).hexdigest()

app = FastAPI(title="UDB Web")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class TelegramAuthRequest(BaseModel):
    auth_data: dict[str, Any] = Field(default_factory=dict)


class SelectChatRequest(BaseModel):
    chat_id: int


class StartVisitRequest(BaseModel):
    target_user_id: int


class CodeAuthRequest(BaseModel):
    code: str = ""


class PurchaseIdleBuildingRequest(BaseModel):
    building_code: str


class TransferSitsRequest(BaseModel):
    receiver_user_id: int
    amount: Any = ""


class UpdateWebSettingsRequest(BaseModel):
    hide_base: bool | None = None
    reject_geyser_catch_by_guest: bool | None = None
    notify_group_masturbation: bool | None = None
    notify_group_masturbation_sound: bool | None = None


class GroupEventActionRequest(BaseModel):
    pass


class ChatMessageRequest(BaseModel):
    text: str = ""


class AiTaskResultRequest(BaseModel):
    output: str = ""
    error: str = ""


class DailyEventUpsertRequest(BaseModel):
    name: str = ""
    description: str | None = None
    datetime: str | None = None
    link: str | None = None
    cars: Any = False


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    )
    return cur.fetchone() is not None


def _ensure_idle_service_tables() -> None:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS idle_hourly_income_ticks (
                hour_key TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def _ensure_geyser_tables() -> None:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS web_geyser_daily_catches (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                catch_date TEXT NOT NULL,
                amount INTEGER NOT NULL DEFAULT 0 CHECK(amount >= 0),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, chat_id, catch_date)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_geyser_daily_catches_date
            ON web_geyser_daily_catches(catch_date, chat_id, user_id)
            """
        )
        conn.commit()


def _ensure_web_settings_table() -> None:
    with _get_connection() as conn:
        cur = conn.cursor()
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
        cur.execute("PRAGMA table_info(web_settings)")
        columns = {str(row["name"]).lower() for row in cur.fetchall()}
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
        conn.commit()


def _get_web_settings(user_id: int, chat_id: int) -> dict[str, bool]:
    _ensure_web_settings_table()
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                COALESCE(hide_base, 0) AS hide_base,
                COALESCE(reject_geyser_catch_by_guest, 0) AS reject_geyser_catch_by_guest,
                COALESCE(notify_group_masturbation, 1) AS notify_group_masturbation,
                COALESCE(notify_group_masturbation_sound, 1) AS notify_group_masturbation_sound
            FROM web_settings
            WHERE user_id = ? AND chat_id = ?
            """,
            (user_id, chat_id),
        )
        row = cur.fetchone()
    if not row:
        return {
            "hide_base": False,
            "reject_geyser_catch_by_guest": False,
            "notify_group_masturbation": True,
            "notify_group_masturbation_sound": True,
        }
    return {
        "hide_base": bool(int(row["hide_base"] or 0)),
        "reject_geyser_catch_by_guest": bool(int(row["reject_geyser_catch_by_guest"] or 0)),
        "notify_group_masturbation": bool(int(row["notify_group_masturbation"] or 0)),
        "notify_group_masturbation_sound": bool(int(row["notify_group_masturbation_sound"] or 0)),
    }


def _update_web_settings(
    user_id: int,
    chat_id: int,
    hide_base: bool | None = None,
    reject_geyser_catch_by_guest: bool | None = None,
    notify_group_masturbation: bool | None = None,
    notify_group_masturbation_sound: bool | None = None,
) -> dict[str, bool]:
    _ensure_web_settings_table()
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT
                COALESCE(hide_base, 0) AS hide_base,
                COALESCE(reject_geyser_catch_by_guest, 0) AS reject_geyser_catch_by_guest,
                COALESCE(notify_group_masturbation, 1) AS notify_group_masturbation,
                COALESCE(notify_group_masturbation_sound, 1) AS notify_group_masturbation_sound
            FROM web_settings
            WHERE user_id = ? AND chat_id = ?
            """,
            (user_id, chat_id),
        )
        row = cur.fetchone()
        current_hide = bool(int(row["hide_base"] or 0)) if row else False
        current_reject = bool(int(row["reject_geyser_catch_by_guest"] or 0)) if row else False
        current_notify = bool(int(row["notify_group_masturbation"] or 1)) if row else True
        current_notify_sound = bool(int(row["notify_group_masturbation_sound"] or 1)) if row else True
        next_hide = current_hide if hide_base is None else bool(hide_base)
        next_reject = current_reject if reject_geyser_catch_by_guest is None else bool(reject_geyser_catch_by_guest)
        next_notify = current_notify if notify_group_masturbation is None else bool(notify_group_masturbation)
        next_notify_sound = current_notify_sound if notify_group_masturbation_sound is None else bool(notify_group_masturbation_sound)
        cur.execute(
            """
            INSERT INTO web_settings (
                user_id,
                chat_id,
                hide_base,
                reject_geyser_catch_by_guest,
                notify_group_masturbation,
                notify_group_masturbation_sound,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                hide_base = excluded.hide_base,
                reject_geyser_catch_by_guest = excluded.reject_geyser_catch_by_guest,
                notify_group_masturbation = excluded.notify_group_masturbation,
                notify_group_masturbation_sound = excluded.notify_group_masturbation_sound,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, chat_id, int(next_hide), int(next_reject), int(next_notify), int(next_notify_sound)),
        )
        conn.commit()
    return {
        "hide_base": next_hide,
        "reject_geyser_catch_by_guest": next_reject,
        "notify_group_masturbation": next_notify,
        "notify_group_masturbation_sound": next_notify_sound,
    }


def _today_date_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _server_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _get_geyser_catches_for_today(user_id: int, chat_id: int) -> int:
    _ensure_geyser_tables()
    date_key = _today_date_key()
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT amount
            FROM web_geyser_daily_catches
            WHERE user_id = ? AND chat_id = ? AND catch_date = ?
            """,
            (user_id, chat_id, date_key),
        )
        row = cur.fetchone()
    if not row:
        return 0
    return int(row["amount"] or 0)


def _catch_geyser_for_today(
    user_id: int,
    chat_id: int,
    beneficiary_user_id: int | None = None,
) -> dict[str, Any]:
    _ensure_geyser_tables()
    date_key = _today_date_key()
    reward_millisits = random.randint(GEYSER_REWARD_MIN_MILLISITS, GEYSER_REWARD_MAX_MILLISITS)
    reward_sits = reward_millisits / IDLE_MICROSITS_IN_SIT

    effective_beneficiary_user_id = int(beneficiary_user_id or user_id)
    if effective_beneficiary_user_id <= 0:
        effective_beneficiary_user_id = user_id
    geyser_owner_user_id = effective_beneficiary_user_id
    if effective_beneficiary_user_id == user_id:
        visitor_reward_millisits = 0
    else:
        visitor_reward_millisits = max(1, int(round(reward_millisits * 0.1)))
    visitor_reward_sits = visitor_reward_millisits / IDLE_MICROSITS_IN_SIT

    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT COALESCE(amount, 0) AS amount
            FROM web_geyser_daily_catches
            WHERE user_id = ? AND chat_id = ? AND catch_date = ?
            """,
            (geyser_owner_user_id, chat_id, date_key),
        )
        geyser_row = cur.fetchone()
        caught_today = int(geyser_row["amount"] or 0) if geyser_row else 0
        if caught_today >= GEYSER_DAILY_LIMIT:
            conn.rollback()
            raise HTTPException(status_code=409, detail="Дневной лимит гейзеров уже достигнут")

        cur.execute(
            """
            SELECT COALESCE(name, '') AS name, COALESCE(sits, 0) AS sits
            FROM users
            WHERE user_id = ? AND chat_id = ?
            """,
            (user_id, chat_id),
        )
        user_row = cur.fetchone()
        if not user_row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Пользователь не найден в выбранном чате")

        cur.execute(
            """
            SELECT COALESCE(name, '') AS name, COALESCE(sits, 0) AS sits
            FROM users
            WHERE user_id = ? AND chat_id = ?
            """,
            (effective_beneficiary_user_id, chat_id),
        )
        beneficiary_row = cur.fetchone()
        if not beneficiary_row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Получатель награды не найден в выбранном чате")

        new_caught_today = caught_today + 1
        cur.execute(
            """
            INSERT INTO web_geyser_daily_catches (user_id, chat_id, catch_date, amount)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id, catch_date) DO UPDATE SET
                amount = excluded.amount,
                updated_at = CURRENT_TIMESTAMP
            """,
            (geyser_owner_user_id, chat_id, date_key, new_caught_today),
        )

        catcher_balance = float(user_row["sits"] or 0)
        beneficiary_balance = float(beneficiary_row["sits"] or 0)
        beneficiary_name = str(beneficiary_row["name"] or "")

        if effective_beneficiary_user_id == user_id:
            new_beneficiary_balance = normalize_sits(beneficiary_balance + reward_sits)
            new_catcher_balance = new_beneficiary_balance
            cur.execute(
                """
                UPDATE users
                SET sits = ?
                WHERE user_id = ? AND chat_id = ?
                """,
                (new_beneficiary_balance, user_id, chat_id),
            )
        else:
            new_beneficiary_balance = normalize_sits(beneficiary_balance + reward_sits)
            new_catcher_balance = normalize_sits(catcher_balance + visitor_reward_sits)
            cur.execute(
                """
                UPDATE users
                SET sits = ?
                WHERE user_id = ? AND chat_id = ?
                """,
                (new_beneficiary_balance, effective_beneficiary_user_id, chat_id),
            )
            cur.execute(
                """
                UPDATE users
                SET sits = ?
                WHERE user_id = ? AND chat_id = ?
                """,
                (new_catcher_balance, user_id, chat_id),
            )

        if _table_exists(cur, "sit_stats"):
            now = datetime.now()
            date_value = now.date().isoformat()
            time_value = now.strftime("%H:%M:%S")
            cur.execute(
                """
                INSERT INTO sit_stats (date, time, chat_id, user_id, name, amount)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    date_value,
                    time_value,
                    chat_id,
                    effective_beneficiary_user_id,
                    beneficiary_name,
                    reward_sits,
                ),
            )
            if effective_beneficiary_user_id != user_id and visitor_reward_sits > 0:
                catcher_name = str(user_row["name"] or "")
                cur.execute(
                    """
                    INSERT INTO sit_stats (date, time, chat_id, user_id, name, amount)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        date_value,
                        time_value,
                        chat_id,
                        user_id,
                        catcher_name,
                        visitor_reward_sits,
                    ),
                )
        conn.commit()

    return {
        "reward_millisits": reward_millisits,
        "reward_sits": normalize_sits(reward_sits),
        "visitor_reward_millisits": visitor_reward_millisits,
        "visitor_reward_sits": normalize_sits(visitor_reward_sits),
        "geyser_owner_user_id": geyser_owner_user_id,
        "beneficiary_user_id": effective_beneficiary_user_id,
        "beneficiary_name": beneficiary_name,
        "beneficiary_balance": new_beneficiary_balance,
        "is_visit_reward": effective_beneficiary_user_id != user_id,
        "caught_today": new_caught_today,
        "daily_limit": GEYSER_DAILY_LIMIT,
        "balance": new_catcher_balance,
    }


def _ensure_idle_catalog_ready(force: bool = False) -> None:
    global idle_catalog_ready
    if idle_catalog_ready and not force:
        return

    try:
        from createdb import ensure_idle_game_tables as ensure_idle_catalog_tables
    except Exception:
        logger.exception("Failed to import idle catalog migration helpers")
        return

    with idle_catalog_lock:
        if idle_catalog_ready and not force:
            return
        try:
            with _get_connection() as conn:
                cur = conn.cursor()
                ensure_idle_catalog_tables(cur)
                conn.commit()
            idle_catalog_ready = True
        except Exception:
            logger.exception("Failed to ensure idle catalog tables")


def _to_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _hour_key(dt: datetime) -> str:
    return _to_hour(dt).strftime("%Y-%m-%d %H:00:00")


def _parse_hour_key(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:00:00")
    except ValueError:
        return None


def _to_microsits(sits_value: float | int | None) -> int:
    value = float(sits_value or 0)
    return int(round(value * IDLE_MICROSITS_IN_SIT))


def _sits_to_microsits(sits_value: float | int | None) -> int:
    # Backward-compatible alias for existing call sites.
    return _to_microsits(sits_value)


def _icon_file_name(image_file: str) -> str:
    image_path = Path(image_file)
    if image_path.suffix:
        return f"{image_path.stem}_icon{image_path.suffix}"
    return f"{image_file}_icon"


def _get_last_idle_income_hour() -> datetime | None:
    _ensure_idle_service_tables()
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MAX(hour_key) AS hour_key FROM idle_hourly_income_ticks")
        row = cur.fetchone()
    if not row:
        return None
    return _parse_hour_key(row["hour_key"])


def _apply_idle_income_for_hour(hour_start: datetime) -> bool:
    _ensure_idle_service_tables()
    hour_start = _to_hour(hour_start)
    hour_key = _hour_key(hour_start)
    date_value = hour_start.date().isoformat()
    time_value = hour_start.strftime("%H:%M:%S")

    with _get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                "INSERT INTO idle_hourly_income_ticks (hour_key) VALUES (?)",
                (hour_key,),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return False

        cur.execute(
            """
            UPDATE idle_player_buildings
            SET lifetime_earned_microsits = lifetime_earned_microsits + (
                SELECT income_microsits_per_hour
                FROM idle_building_levels levels
                WHERE levels.building_code = idle_player_buildings.building_code
                  AND levels.level = idle_player_buildings.current_level
            ),
            updated_at = CURRENT_TIMESTAMP
            WHERE current_level BETWEEN 1 AND ?
            """,
            (IDLE_MAX_LEVEL,),
        )

        cur.execute(
            """
            SELECT
                pb.user_id,
                pb.chat_id,
                COALESCE(u.name, '') AS name,
                SUM(levels.income_microsits_per_hour) AS income_microsits
            FROM idle_player_buildings pb
            JOIN idle_building_levels levels
              ON levels.building_code = pb.building_code
             AND levels.level = pb.current_level
            JOIN users u
              ON u.user_id = pb.user_id
             AND u.chat_id = pb.chat_id
            GROUP BY pb.user_id, pb.chat_id, u.name
            HAVING income_microsits > 0
            """
        )
        income_rows = cur.fetchall()

        can_write_sit_stats = _table_exists(cur, "sit_stats")
        for row in income_rows:
            income_microsits = int(row["income_microsits"] or 0)
            if income_microsits <= 0:
                continue

            income_sits = income_microsits / IDLE_MICROSITS_IN_SIT
            cur.execute(
                """
                UPDATE users
                SET sits = ROUND(COALESCE(sits, 0) + ?, 3)
                WHERE user_id = ? AND chat_id = ?
                """,
                (income_sits, int(row["user_id"]), int(row["chat_id"])),
            )

            if can_write_sit_stats:
                cur.execute(
                    """
                    INSERT INTO sit_stats (date, time, chat_id, user_id, name, amount)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        date_value,
                        time_value,
                        int(row["chat_id"]),
                        int(row["user_id"]),
                        str(row["name"] or ""),
                        income_sits,
                    ),
                )

        conn.commit()
        return True


def _catch_up_idle_income() -> int:
    processed_count = 0
    now_hour = _to_hour(datetime.now())
    last_hour = _get_last_idle_income_hour()

    if last_hour is None:
        if _apply_idle_income_for_hour(now_hour):
            processed_count += 1
        return processed_count

    next_hour = last_hour + timedelta(hours=1)
    while next_hour <= now_hour:
        if _apply_idle_income_for_hour(next_hour):
            processed_count += 1
        next_hour += timedelta(hours=1)
    return processed_count


async def _idle_income_worker() -> None:
    while True:
        try:
            _catch_up_idle_income()
        except Exception:
            logger.exception("Idle income worker failed")

        now = datetime.now()
        next_hour = _to_hour(now) + timedelta(hours=1)
        sleep_seconds = max((next_hour - now).total_seconds(), 1.0)
        await asyncio.sleep(sleep_seconds)


def _require_selected_user_chat(request: Request) -> tuple[int, int]:
    payload = _require_session(request)
    user_id = int(payload["telegram_user_id"])
    selected_chat_id = payload.get("selected_chat_id")
    if selected_chat_id is None:
        raise HTTPException(status_code=400, detail="Сначала выберите чат")

    try:
        chat_id = int(selected_chat_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректный чат")

    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1
            FROM users
            WHERE user_id = ? AND chat_id = ?
            """,
            (user_id, chat_id),
        )
        exists = cur.fetchone() is not None

    if not exists:
        raise HTTPException(status_code=403, detail="Чат недоступен для выбранной сессии")

    return user_id, chat_id


def _user_has_idle_buildings(user_id: int, chat_id: int) -> bool:
    try:
        with _get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT 1
                FROM idle_player_buildings
                WHERE user_id = ? AND chat_id = ? AND current_level > 0
                LIMIT 1
                """,
                (user_id, chat_id),
            )
            return cur.fetchone() is not None
    except sqlite3.OperationalError:
        return False


def _resolve_visit_target(
    owner_user_id: int,
    chat_id: int,
    visit_user_id_raw: Any,
    require_buildings: bool = False,
) -> dict[str, Any] | None:
    try:
        visit_user_id = int(visit_user_id_raw)
    except (TypeError, ValueError):
        return None

    if visit_user_id <= 0 or visit_user_id == owner_user_id:
        return None

    with _get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT COALESCE(name, '') AS name, COALESCE(nick, '') AS nick
                FROM users
                WHERE user_id = ? AND chat_id = ?
                LIMIT 1
                """,
                (visit_user_id, chat_id),
            )
        except sqlite3.OperationalError:
            cur.execute(
                """
                SELECT COALESCE(name, '') AS name, '' AS nick
                FROM users
                WHERE user_id = ? AND chat_id = ?
                LIMIT 1
                """,
                (visit_user_id, chat_id),
            )
        row = cur.fetchone()

    if not row:
        return None

    if require_buildings and not _user_has_idle_buildings(visit_user_id, chat_id):
        return None

    name_raw = str(row["name"] or "").strip()
    nick_raw = str(row["nick"] or "").strip()
    display_name = name_raw or nick_raw or f"Игрок {visit_user_id}"
    return {
        "user_id": visit_user_id,
        "name": display_name,
    }


def _get_user_balance(user_id: int, chat_id: int) -> float:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COALESCE(sits, 0) AS sits
            FROM users
            WHERE user_id = ? AND chat_id = ?
            """,
            (user_id, chat_id),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Пользователь не найден в выбранном чате")
    return float(row["sits"] or 0)


def _get_user_profile(chat_id: int, user_id: int) -> dict[str, Any]:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                COALESCE(name, '') AS name,
                COALESCE(nick, '') AS nick,
                COALESCE(sex, '') AS sex
            FROM users
            WHERE user_id = ? AND chat_id = ?
            LIMIT 1
            """,
            (user_id, chat_id),
        )
        row = cur.fetchone()
    if not row:
        return {
            "name": f"Игрок {user_id}",
            "nick": "",
            "sex": "",
        }
    name = str(row["name"] or "").strip()
    nick = str(row["nick"] or "").strip()
    return {
        "name": name or nick or f"Игрок {user_id}",
        "nick": nick,
        "sex": str(row["sex"] or "").strip().lower(),
    }


def _event_token(chat_id: int, created_at: int) -> str:
    return f"{chat_id}:{int(created_at)}"


def _seconds_left(target_ts: int, now_ts: int | None = None) -> int:
    current = int(now_ts if now_ts is not None else time.time())
    return max(0, int(target_ts) - current)


def _build_group_members_state(chat_id: int) -> dict[str, list[dict[str, Any]]]:
    participant_rows = group_store.list_participants(chat_id=chat_id, role="participant")
    spectator_rows = group_store.list_participants(chat_id=chat_id, role="spectator")
    event_row = group_store.get_event(chat_id)
    starter_user_id = int(event_row["started_by_user_id"]) if event_row else None

    def enrich(rows: list[sqlite3.Row], role: str) -> list[dict[str, Any]]:
        members: list[dict[str, Any]] = []
        for row in rows:
            member_user_id = int(row["user_id"])
            profile = _get_user_profile(chat_id, member_user_id)
            members.append(
                {
                    "user_id": member_user_id,
                    "name": str(row["display_name"] or profile["name"]),
                    "nick": profile["nick"],
                    "sex": profile["sex"],
                    "role": role,
                    "joined_order": int(row["joined_order"] or 0),
                    "is_starter": bool(starter_user_id is not None and member_user_id == starter_user_id),
                }
            )
        return members

    return {
        "participants": enrich(participant_rows, "participant"),
        "spectators": enrich(spectator_rows, "spectator"),
    }


def _build_group_result_state(chat_id: int) -> dict[str, Any] | None:
    result = group_store.get_event_result(chat_id)
    if not result:
        return None

    def enrich_user(target_user_id: int | None, fallback_name: str | None) -> dict[str, Any] | None:
        if target_user_id is None:
            return None
        profile = _get_user_profile(chat_id, int(target_user_id))
        return {
            "user_id": int(target_user_id),
            "name": str(fallback_name or profile["name"]),
            "sex": profile["sex"],
        }

    participants: list[dict[str, Any]] = []
    for item in result["participants"]:
        if not isinstance(item, dict):
            continue
        uid = item.get("user_id")
        try:
            member_user_id = int(uid)
        except (TypeError, ValueError):
            continue
        profile = _get_user_profile(chat_id, member_user_id)
        participants.append(
            {
                "user_id": member_user_id,
                "name": str(item.get("name") or profile["name"]),
                "sex": profile["sex"],
                "role": "participant",
                "is_starter": bool(item.get("is_starter")),
            }
        )

    spectators: list[dict[str, Any]] = []
    for item in result["spectators"]:
        if not isinstance(item, dict):
            continue
        uid = item.get("user_id")
        try:
            member_user_id = int(uid)
        except (TypeError, ValueError):
            continue
        profile = _get_user_profile(chat_id, member_user_id)
        spectators.append(
            {
                "user_id": member_user_id,
                "name": str(item.get("name") or profile["name"]),
                "sex": profile["sex"],
                "role": "spectator",
            }
        )

    return {
        "event_token": str(result["event_token"] or ""),
        "winner": enrich_user(result["winner_user_id"], result["winner_name"]),
        "winner_reward_millisits": _sits_to_microsits(result["winner_reward_sits"]),
        "lucky": enrich_user(result["lucky_user_id"], result["lucky_name"]),
        "lucky_dick": enrich_user(result["lucky_dick_user_id"], result["lucky_dick_name"]),
        "participants": participants,
        "spectators": spectators,
        "created_at": int(result["created_at"] or 0),
    }


def _build_group_event_state(chat_id: int, user_id: int) -> dict[str, Any]:
    event_row = group_store.get_event(chat_id)
    now_ts = int(time.time())
    if not event_row:
        return {
            "active": False,
            "phase": "idle",
            "event_token": None,
            "server_now_ts": now_ts,
            "prepare_until_ts": 0,
            "join_until_ts": 0,
            "prepare_seconds_left": 0,
            "join_seconds_left": 0,
            "viewer_role": "none",
            "viewer_is_starter": False,
            "can_start": True,
            "can_remind": False,
            "can_join_participant": False,
            "can_join_spectator": False,
            "start_cost_millisits": _sits_to_microsits(EVENT_COST),
            "join_cost_millisits": _sits_to_microsits(JOIN_COST),
            "participants": [],
            "spectators": [],
            "result": _build_group_result_state(chat_id),
        }

    created_at = int(event_row["created_at"] or now_ts)
    prepare_until = int(event_row["prepare_until"] or (created_at + GROUP_PREPARE_DELAY_SECONDS))
    join_until = int(event_row["join_until"] or (prepare_until + GROUP_JOIN_WINDOW_SECONDS))
    status = str(event_row["status"] or "").strip().lower()
    join_open = bool(int(event_row["join_open"] or 0))
    token = _event_token(chat_id, created_at)

    if join_open or status == "joining":
        phase = "joining"
    elif status == "preparing" and now_ts < prepare_until:
        phase = "preparing"
    else:
        phase = "finishing"

    members = _build_group_members_state(chat_id)
    participants = members["participants"]
    spectators = members["spectators"]

    viewer_role = "none"
    viewer_is_starter = False
    for member in participants:
        if int(member["user_id"]) == user_id:
            viewer_role = "participant"
            viewer_is_starter = bool(member["is_starter"])
            break
    if viewer_role == "none":
        for member in spectators:
            if int(member["user_id"]) == user_id:
                viewer_role = "spectator"
                viewer_is_starter = bool(member["is_starter"])
                break

    return {
        "active": True,
        "phase": phase,
        "event_token": token,
        "server_now_ts": now_ts,
        "prepare_until_ts": prepare_until,
        "join_until_ts": join_until,
        "prepare_seconds_left": _seconds_left(prepare_until, now_ts) if phase == "preparing" else 0,
        "join_seconds_left": _seconds_left(join_until, now_ts) if phase == "joining" else 0,
        "viewer_role": viewer_role,
        "viewer_is_starter": viewer_is_starter,
        "can_start": False,
        "can_remind": phase == "preparing" and viewer_role == "none",
        "can_join_participant": phase == "joining" and viewer_role == "none",
        "can_join_spectator": phase == "joining" and viewer_role == "none",
        "start_cost_millisits": _sits_to_microsits(EVENT_COST),
        "join_cost_millisits": _sits_to_microsits(JOIN_COST),
        "participants": participants,
        "spectators": spectators,
        "result": _build_group_result_state(chat_id),
    }


def _enqueue_group_text(chat_id: int, text: str, thread_id: int | None = None) -> None:
    group_store.enqueue_outbox(
        chat_id=chat_id,
        kind="send_text",
        payload={
            "text": str(text),
            "thread_id": int(thread_id) if thread_id is not None else None,
        },
    )


def _enqueue_group_html_text(chat_id: int, text: str, thread_id: int | None = None) -> None:
    group_store.enqueue_outbox(
        chat_id=chat_id,
        kind="send_html_text",
        payload={
            "text": str(text),
            "thread_id": int(thread_id) if thread_id is not None else None,
            "disable_preview": True,
        },
    )


def _enqueue_group_start_flow(chat_id: int) -> None:
    group_store.enqueue_outbox(chat_id=chat_id, kind="start_event_flow", payload={})


def _sanitize_web_chat_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Введите сообщение")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="Сообщение не должно быть длиннее 1000 символов")
    return text


def _daily_validation_error(field_errors: dict[str, str], message: str = "Проверьте заполнение полей") -> None:
    raise HTTPException(
        status_code=400,
        detail={
            "code": "VALIDATION_ERROR",
            "message": message,
            "field_errors": field_errors,
        },
    )


def _normalize_daily_cars(value: Any) -> str:
    if isinstance(value, bool):
        return "да" if value else "нет"
    raw = str(value or "").strip().lower()
    if raw in {"1", "true", "yes", "y", "да", "д"}:
        return "да"
    return "нет"


def _daily_cars_enabled(value: Any) -> bool:
    return _normalize_daily_cars(value) == "да"


def _normalize_daily_link(raw_link: Any) -> str | None:
    text = str(raw_link or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        _daily_validation_error({"link": "Укажите корректную ссылку (http/https)"}, "Некорректная ссылка")
    return text


def _parse_daily_datetime_input(raw_value: Any) -> datetime:
    text = str(raw_value or "").strip()
    if not text:
        _daily_validation_error({"datetime": "Укажите дату и время"})
    normalized = text.replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "VALIDATION_ERROR",
                "message": "Некорректный формат даты и времени",
                "field_errors": {"datetime": "Используйте формат ДД.ММ.ГГГГ ЧЧ:ММ или ISO"},
            },
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=DAILY_TIMEZONE)
    else:
        parsed = parsed.astimezone(DAILY_TIMEZONE)
    return parsed


def _combine_daily_datetime_from_row(row: sqlite3.Row | dict[str, Any]) -> datetime:
    date_value = str(row["date"] or "").strip()
    time_value = str(row["time"] or "").strip()
    parsed = datetime.strptime(f"{date_value} {time_value}", "%Y-%m-%d %H:%M")
    return parsed.replace(tzinfo=DAILY_TIMEZONE)


def _is_daily_expired(row: sqlite3.Row | dict[str, Any], now_dt: datetime | None = None) -> bool:
    current = now_dt or datetime.now(DAILY_TIMEZONE)
    return _combine_daily_datetime_from_row(row) < current


def _daily_can_manage_event(user_id: int, creator_user_id: int) -> bool:
    return int(user_id) == int(creator_user_id) or int(user_id) in ADMIN_IDS_SET


def _parse_daily_cursor(cursor_raw: str | None) -> tuple[str, int] | None:
    if not cursor_raw:
        return None
    raw = str(cursor_raw).strip()
    if not raw:
        return None
    if "|" not in raw:
        raise HTTPException(status_code=400, detail="Некорректный курсор")
    dt_part, id_part = raw.rsplit("|", 1)
    try:
        cursor_id = int(id_part)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Некорректный курсор") from exc
    dt_key = dt_part.strip()
    if len(dt_key) < 16:
        raise HTTPException(status_code=400, detail="Некорректный курсор")
    return dt_key, cursor_id


def _daily_cursor_from_row(row: sqlite3.Row) -> str:
    dt_key = f"{row['date']} {row['time']}"
    return f"{dt_key}|{int(row['id'])}"


def _fetch_daily_event_row(cur: sqlite3.Cursor, chat_id: int, daily_id: int) -> sqlite3.Row | None:
    cur.execute(
        """
        SELECT *
        FROM daily_events
        WHERE id = ? AND chat_id = ?
        LIMIT 1
        """,
        (daily_id, chat_id),
    )
    return cur.fetchone()


def _get_daily_participants_map(chat_id: int, daily_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not daily_ids:
        return {}
    placeholders = ",".join("?" for _ in daily_ids)
    params: list[Any] = [chat_id, *daily_ids]
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT
                p.daily_id AS daily_id,
                p.user_id AS user_id,
                COALESCE(p.is_driver, 0) AS is_driver,
                COALESCE(u.name, '') AS name,
                COALESCE(u.nick, '') AS nick
            FROM daily_participants p
            LEFT JOIN users u
              ON u.user_id = p.user_id
             AND u.chat_id = ?
            WHERE p.daily_id IN ({placeholders})
            ORDER BY p.daily_id ASC, COALESCE(p.is_driver, 0) DESC, p.user_id ASC
            """,
            tuple(params),
        )
        rows = cur.fetchall()

    result: dict[int, list[dict[str, Any]]] = {daily_id: [] for daily_id in daily_ids}
    for row in rows:
        daily_id = int(row["daily_id"])
        user_id = int(row["user_id"])
        name = str(row["name"] or "").strip()
        nick_raw = str(row["nick"] or "").strip()
        nick = nick_raw.lstrip("@")
        display_name = name or (f"@{nick}" if nick else f"Игрок {user_id}")
        mention_html = f"@{nick}" if nick else f'<a href="tg://user?id={user_id}">{escape(display_name)}</a>'
        result.setdefault(daily_id, []).append(
            {
                "user_id": user_id,
                "name": display_name,
                "nick": nick,
                "is_driver": bool(int(row["is_driver"] or 0)),
                "mention_html": mention_html,
            }
        )
    return result


def _serialize_daily_event(
    row: sqlite3.Row,
    viewer_user_id: int,
    participants: list[dict[str, Any]],
    now_dt: datetime | None = None,
) -> dict[str, Any]:
    current = now_dt or datetime.now(DAILY_TIMEZONE)
    dt_local = _combine_daily_datetime_from_row(row)
    expired = dt_local < current
    cars_enabled = _daily_cars_enabled(row["cars"])
    participant_ids = {int(item["user_id"]) for item in participants}
    viewer_participant = viewer_user_id in participant_ids
    viewer_driver = any(int(item["user_id"]) == viewer_user_id and bool(item["is_driver"]) for item in participants)
    can_manage = _daily_can_manage_event(viewer_user_id, int(row["creator_user_id"]))
    can_interact = not expired
    can_toggle_driver = can_interact and cars_enabled

    participant_count = len(participants)
    drivers = [item for item in participants if bool(item["is_driver"])]
    non_drivers = [item for item in participants if not bool(item["is_driver"])]
    driver_count = len(drivers)
    driver_capacity = driver_count * 5 if cars_enabled else None
    capacity_shortage = bool(cars_enabled and participant_count > (driver_capacity or 0))

    return {
        "id": int(row["id"]),
        "chat_id": int(row["chat_id"]),
        "creator_user_id": int(row["creator_user_id"]),
        "name": str(row["name"] or ""),
        "description": str(row["description"] or ""),
        "link": str(row["link"] or ""),
        "cars": "да" if cars_enabled else "нет",
        "cars_enabled": cars_enabled,
        "date": str(row["date"] or ""),
        "time": str(row["time"] or ""),
        "datetime_local": dt_local.strftime("%Y-%m-%dT%H:%M"),
        "datetime_iso": dt_local.isoformat(),
        "expired": expired,
        "is_creator": int(row["creator_user_id"]) == viewer_user_id,
        "viewer_is_participant": viewer_participant,
        "viewer_is_driver": viewer_driver,
        "can_edit": bool(can_interact and can_manage),
        "can_delete": bool(can_interact and can_manage),
        "can_toggle_participation": bool(can_interact),
        "can_toggle_driver": bool(can_toggle_driver),
        "can_tag_participants": bool(can_interact and viewer_participant),
        "participant_count": participant_count,
        "driver_count": driver_count,
        "driver_capacity": driver_capacity,
        "capacity_shortage": capacity_shortage,
        "participants": non_drivers,
        "drivers": drivers,
        "all_participants": participants,
    }


def _ensure_daily_schema_compatibility() -> None:
    required_daily_events_columns = {
        "id",
        "chat_id",
        "creator_user_id",
        "name",
        "description",
        "date",
        "time",
        "cars",
        "link",
    }
    required_participants_columns = {
        "daily_id",
        "user_id",
        "is_driver",
    }
    with _get_connection() as conn:
        cur = conn.cursor()
        if not _table_exists(cur, "daily_events"):
            logger.error("daily_events table is missing")
            return
        if not _table_exists(cur, "daily_participants"):
            logger.error("daily_participants table is missing")
            return

        cur.execute("PRAGMA table_info(daily_events)")
        daily_events_columns = {str(row["name"]).lower() for row in cur.fetchall()}
        missing_daily_events = sorted(required_daily_events_columns - daily_events_columns)
        if missing_daily_events:
            logger.error("daily_events is missing required columns: %s", ", ".join(missing_daily_events))

        cur.execute("PRAGMA table_info(daily_participants)")
        participants_columns = {str(row["name"]).lower() for row in cur.fetchall()}
        missing_participants = sorted(required_participants_columns - participants_columns)
        if missing_participants:
            logger.error("daily_participants is missing required columns: %s", ", ".join(missing_participants))


def _format_chat_attachment(row: sqlite3.Row) -> dict[str, Any]:
    attachment_id = int(row["id"])
    return {
        "id": attachment_id,
        "media_type": str(row["media_type"] or "photo"),
        "url": f"/api/chat/media/{attachment_id}",
        "mime_type": str(row["mime_type"] or "image/jpeg"),
        "width": int(row["width"]) if row["width"] is not None else None,
        "height": int(row["height"]) if row["height"] is not None else None,
        "file_size": int(row["file_size"]) if row["file_size"] is not None else None,
    }


def _format_chat_message(row: sqlite3.Row, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    user_id = int(row["user_id"] or 0)
    author_name = str(row["author_name"] or "").strip()
    author_nick = str(row["author_nick"] or "").strip()
    return {
        "chat_id": int(row["chat_id"]),
        "message_id": int(row["message_id"]),
        "user_id": user_id,
        "author_name": author_name or author_nick or f"Игрок {user_id}",
        "author_nick": author_nick,
        "text": str(row["message_text"] or ""),
        "reactions_count": int(row["reactions_count"] or 0),
        "date": str(row["date"] or ""),
        "attachments": attachments or [],
    }


def _get_chat_attachments(cur: sqlite3.Cursor, chat_id: int, message_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not message_ids:
        return {}
    placeholders = ",".join("?" for _ in message_ids)
    cur.execute(
        f"""
        SELECT
            id,
            chat_id,
            message_id,
            attachment_index,
            media_type,
            mime_type,
            width,
            height,
            file_size
        FROM web_chat_attachments
        WHERE chat_id = ?
            AND message_id IN ({placeholders})
            AND media_type = 'photo'
        ORDER BY message_id ASC, attachment_index ASC, id ASC
        """,
        [chat_id, *message_ids],
    )
    attachments_by_message: dict[int, list[dict[str, Any]]] = {}
    for row in cur.fetchall():
        message_id = int(row["message_id"])
        attachments_by_message.setdefault(message_id, []).append(_format_chat_attachment(row))
    return attachments_by_message


def _get_chat_messages(chat_id: int, after_message_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 100), 100))
    with _get_connection() as conn:
        cur = conn.cursor()
        if after_message_id is not None and after_message_id > 0:
            cur.execute(
                """
                SELECT
                    mr.chat_id,
                    mr.message_id,
                    mr.user_id,
                    mr.message_text,
                    mr.reactions_count,
                    mr.date,
                    COALESCE(u.name, '') AS author_name,
                    COALESCE(u.nick, '') AS author_nick
                FROM messages_reactions mr
                LEFT JOIN users u
                    ON u.chat_id = mr.chat_id AND u.user_id = mr.user_id
                WHERE mr.chat_id = ?
                    AND mr.message_id > ?
                    AND (
                        TRIM(COALESCE(mr.message_text, '')) != ''
                        OR EXISTS (
                            SELECT 1
                            FROM web_chat_attachments wca
                            WHERE wca.chat_id = mr.chat_id
                                AND wca.message_id = mr.message_id
                        )
                    )
                ORDER BY mr.message_id ASC
                LIMIT ?
                """,
                (chat_id, int(after_message_id), limit),
            )
            rows = cur.fetchall()
        else:
            cur.execute(
                """
                SELECT *
                FROM (
                    SELECT
                        mr.chat_id,
                        mr.message_id,
                        mr.user_id,
                        mr.message_text,
                        mr.reactions_count,
                        mr.date,
                        COALESCE(u.name, '') AS author_name,
                        COALESCE(u.nick, '') AS author_nick
                    FROM messages_reactions mr
                    LEFT JOIN users u
                        ON u.chat_id = mr.chat_id AND u.user_id = mr.user_id
                    WHERE mr.chat_id = ?
                        AND (
                            TRIM(COALESCE(mr.message_text, '')) != ''
                            OR EXISTS (
                                SELECT 1
                                FROM web_chat_attachments wca
                                WHERE wca.chat_id = mr.chat_id
                                    AND wca.message_id = mr.message_id
                            )
                        )
                    ORDER BY mr.message_id DESC
                    LIMIT ?
                )
                ORDER BY message_id ASC
                """,
                (chat_id, limit),
            )
            rows = cur.fetchall()
        message_ids = [int(row["message_id"]) for row in rows]
        attachments_by_message = _get_chat_attachments(cur, chat_id, message_ids)
    return [
        _format_chat_message(row, attachments_by_message.get(int(row["message_id"]), []))
        for row in rows
    ]


def _parse_transfer_amount(value: Any) -> float:
    if value is None:
        raise HTTPException(status_code=400, detail="Введите количество сита")

    if isinstance(value, (int, float)):
        raw = str(value)
    else:
        raw = str(value).strip()

    raw = raw.replace(" ", "").replace("\u202f", "").replace(",", ".")
    if not raw:
        raise HTTPException(status_code=400, detail="Введите количество сита")

    try:
        amount = float(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Сумма должна быть числом") from exc

    if not math.isfinite(amount):
        raise HTTPException(status_code=400, detail="Сумма должна быть конечным числом")
    return float(normalize_sits(amount))


def _transfer_sits(user_id: int, chat_id: int, receiver_user_id: int, amount_raw: Any) -> dict[str, Any]:
    amount = _parse_transfer_amount(amount_raw)
    if amount < 0:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "NEGATIVE_AMOUNT",
                "message": "Нельзя передать отрицательное значение",
            },
        )
    if amount == 0:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "ZERO_AMOUNT",
                "message": "Можно передать минимум 0,001 сит",
            },
        )
    amount_millisits = _to_microsits(amount)
    if amount_millisits <= 0:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "ZERO_AMOUNT",
                "message": "Можно передать минимум 0,001 сит",
            },
        )

    if receiver_user_id == user_id:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "SELF_TRANSFER",
                "message": "Нельзя передавать сит самому себе",
            },
        )

    now = datetime.now()
    date_value = now.date().isoformat()
    time_value = now.strftime("%H:%M:%S")

    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute(
            """
            SELECT COALESCE(name, '') AS name, COALESCE(sits, 0) AS sits
            FROM users
            WHERE user_id = ? AND chat_id = ?
            """,
            (user_id, chat_id),
        )
        sender_row = cur.fetchone()
        if not sender_row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Пользователь не найден в выбранном чате")

        cur.execute(
            """
            SELECT COALESCE(name, '') AS name, COALESCE(sits, 0) AS sits
            FROM users
            WHERE user_id = ? AND chat_id = ?
            """,
            (receiver_user_id, chat_id),
        )
        receiver_row = cur.fetchone()
        if not receiver_row:
            conn.rollback()
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "RECEIVER_NOT_FOUND",
                    "message": "Получатель не найден в этом чате",
                },
            )

        sender_balance = float(sender_row["sits"] or 0)
        sender_balance_millisits = _to_microsits(sender_balance)
        if sender_balance_millisits < amount_millisits:
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "INSUFFICIENT_FUNDS",
                    "message": "Недостаточно сита",
                    "balance": normalize_sits(sender_balance_millisits / IDLE_MICROSITS_IN_SIT),
                    "requested": normalize_sits(amount_millisits / IDLE_MICROSITS_IN_SIT),
                },
            )

        receiver_balance = float(receiver_row["sits"] or 0)
        receiver_balance_millisits = _to_microsits(receiver_balance)
        new_sender_balance_millisits = sender_balance_millisits - amount_millisits
        new_receiver_balance_millisits = receiver_balance_millisits + amount_millisits
        transferred_sits = normalize_sits(amount_millisits / IDLE_MICROSITS_IN_SIT)
        new_sender_balance = normalize_sits(new_sender_balance_millisits / IDLE_MICROSITS_IN_SIT)
        new_receiver_balance = normalize_sits(new_receiver_balance_millisits / IDLE_MICROSITS_IN_SIT)

        cur.execute(
            """
            UPDATE users
            SET sits = ?
            WHERE user_id = ? AND chat_id = ?
            """,
            (new_sender_balance, user_id, chat_id),
        )
        cur.execute(
            """
            UPDATE users
            SET sits = ?
            WHERE user_id = ? AND chat_id = ?
            """,
            (new_receiver_balance, receiver_user_id, chat_id),
        )

        if _table_exists(cur, "sit_stats"):
            sender_name = str(sender_row["name"] or "")
            receiver_name = str(receiver_row["name"] or "")
            cur.execute(
                """
                INSERT INTO sit_stats (date, time, chat_id, user_id, name, amount)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (date_value, time_value, chat_id, user_id, sender_name, -transferred_sits),
            )
            cur.execute(
                """
                INSERT INTO sit_stats (date, time, chat_id, user_id, name, amount)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (date_value, time_value, chat_id, receiver_user_id, receiver_name, transferred_sits),
            )

        conn.commit()

    return {
        "chat_id": chat_id,
        "sender_user_id": user_id,
        "receiver_user_id": receiver_user_id,
        "transferred": transferred_sits,
        "balance": new_sender_balance,
    }


def _get_idle_buildings_state(user_id: int, chat_id: int) -> list[dict[str, Any]]:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            WITH defs AS (
                SELECT
                    building_code,
                    building_name,
                    image_file,
                    "order"
                FROM idle_building_levels
                WHERE level = 1
            )
            SELECT
                defs.building_code AS building_code,
                defs.building_name AS building_name,
                defs.image_file AS image_file,
                defs."order" AS building_order,
                COALESCE(pb.current_level, 0) AS current_level,
                COALESCE(pb.lifetime_earned_microsits, 0) AS lifetime_earned_microsits,
                COALESCE(cur_lvl.upgrade_cost_sits, 0) AS current_level_upgrade_cost_sits,
                COALESCE(cur_lvl.income_microsits_per_hour, 0) AS current_income_microsits_per_hour,
                next_lvl.level AS next_level,
                next_lvl.upgrade_cost_sits AS next_upgrade_cost_sits,
                next_lvl.income_microsits_per_hour AS next_income_microsits_per_hour,
                prev_defs.building_code AS unlock_prev_building_code,
                prev_defs.building_name AS unlock_prev_building_name,
                COALESCE(prev_pb.current_level, 0) AS unlock_prev_building_level
            FROM defs
            LEFT JOIN idle_player_buildings pb
              ON pb.user_id = ?
             AND pb.chat_id = ?
             AND pb.building_code = defs.building_code
            LEFT JOIN idle_building_levels cur_lvl
              ON cur_lvl.building_code = defs.building_code
             AND cur_lvl.level = pb.current_level
            LEFT JOIN idle_building_levels next_lvl
              ON next_lvl.building_code = defs.building_code
             AND next_lvl.level = COALESCE(pb.current_level, 0) + 1
            LEFT JOIN defs prev_defs
              ON prev_defs."order" = defs."order" - 1
            LEFT JOIN idle_player_buildings prev_pb
              ON prev_pb.user_id = ?
             AND prev_pb.chat_id = ?
             AND prev_pb.building_code = prev_defs.building_code
            ORDER BY defs."order"
            """,
            (user_id, chat_id, user_id, chat_id),
        )
        rows = cur.fetchall()

    if not rows:
        return []

    items: list[dict[str, Any]] = []
    for row in rows:
        building_order = int(row["building_order"] or 0)
        current_level = int(row["current_level"] or 0)
        current_income = int(row["current_income_microsits_per_hour"] or 0)
        next_level = row["next_level"]
        next_upgrade_cost = row["next_upgrade_cost_sits"]
        next_income = int(row["next_income_microsits_per_hour"] or 0)
        unlock_prev_level = int(row["unlock_prev_building_level"] or 0)
        unlock_prev_name = row["unlock_prev_building_name"]
        unlock_prev_code = row["unlock_prev_building_code"]

        is_locked_for_buy = (
            current_level == 0
            and building_order > 1
            and unlock_prev_level < IDLE_UNLOCK_PREVIOUS_LEVEL
        )
        if current_level >= IDLE_MAX_LEVEL:
            card_state = "max_level"
        elif current_level == 0 and is_locked_for_buy:
            card_state = "zero_locked"
        elif current_level == 0:
            card_state = "zero_unlocked"
        else:
            card_state = "default"

        next_upgrade_cost_microsits = (
            _to_microsits(next_upgrade_cost) if next_upgrade_cost is not None else None
        )
        next_income_delta = max(next_income - current_income, 0) if next_level is not None else 0

        items.append(
            {
                "building_code": str(row["building_code"]),
                "building_order": building_order,
                "name": str(row["building_name"]),
                "image_file": str(row["image_file"]),
                "icon_file": _icon_file_name(str(row["image_file"])),
                "level": current_level,
                "max_level": IDLE_MAX_LEVEL,
                "income_microsits_per_hour": current_income,
                "lifetime_earned_microsits": int(row["lifetime_earned_microsits"] or 0),
                "current_level_upgrade_cost_sits": normalize_sits(row["current_level_upgrade_cost_sits"] or 0),
                "next_level": int(next_level) if next_level is not None else None,
                "next_upgrade_cost_sits": normalize_sits(next_upgrade_cost) if next_upgrade_cost is not None else None,
                "next_upgrade_cost_microsits": next_upgrade_cost_microsits,
                "next_income_microsits_per_hour": next_income if next_level is not None else None,
                "next_income_delta_microsits": next_income_delta if next_level is not None else None,
                "unlock_required_prev_level": IDLE_UNLOCK_PREVIOUS_LEVEL if building_order > 1 else None,
                "unlock_prev_building_code": str(unlock_prev_code) if unlock_prev_code else None,
                "unlock_prev_building_name": str(unlock_prev_name) if unlock_prev_name else None,
                "unlock_prev_building_level": unlock_prev_level if building_order > 1 else None,
                "unlock_condition_met": not is_locked_for_buy,
                "can_upgrade": next_level is not None and not is_locked_for_buy,
                "state": card_state,
            }
        )

    purchased_codes = {item["building_code"] for item in items if item["level"] > 0}
    purchased_orders = [item["building_order"] for item in items if item["level"] > 0]

    visible_codes = set(purchased_codes)
    if purchased_orders:
        next_order = max(purchased_orders) + 1
        next_item = next((item for item in items if item["building_order"] == next_order), None)
        if next_item:
            visible_codes.add(next_item["building_code"])
    else:
        first_item = min(items, key=lambda item: item["building_order"])
        visible_codes.add(first_item["building_code"])

    return [item for item in items if item["building_code"] in visible_codes]


def _get_idle_chat_players_state(user_id: int, chat_id: int) -> list[dict[str, Any]]:
    _ensure_web_settings_table()
    with _get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT
                    u.user_id AS user_id,
                    COALESCE(u.name, '') AS name,
                    COALESCE(u.nick, '') AS nick,
                    SUM(CASE WHEN pb.current_level > 0 THEN pb.current_level ELSE 0 END) AS total_levels
                FROM idle_player_buildings pb
                JOIN users u
                  ON u.user_id = pb.user_id
                 AND u.chat_id = pb.chat_id
                LEFT JOIN web_settings ws
                  ON ws.user_id = u.user_id
                 AND ws.chat_id = u.chat_id
                WHERE pb.chat_id = ?
                  AND pb.current_level > 0
                  AND u.user_id <> ?
                  AND COALESCE(ws.hide_base, 0) = 0
                GROUP BY u.user_id, u.name, u.nick
                HAVING total_levels > 0
                ORDER BY total_levels DESC, name COLLATE NOCASE ASC, u.user_id ASC
                """,
                (chat_id, user_id),
            )
        except sqlite3.OperationalError:
            cur.execute(
                """
                SELECT
                    u.user_id AS user_id,
                    COALESCE(u.name, '') AS name,
                    '' AS nick,
                    SUM(CASE WHEN pb.current_level > 0 THEN pb.current_level ELSE 0 END) AS total_levels
                FROM idle_player_buildings pb
                JOIN users u
                  ON u.user_id = pb.user_id
                 AND u.chat_id = pb.chat_id
                LEFT JOIN web_settings ws
                  ON ws.user_id = u.user_id
                 AND ws.chat_id = u.chat_id
                WHERE pb.chat_id = ?
                  AND pb.current_level > 0
                  AND u.user_id <> ?
                  AND COALESCE(ws.hide_base, 0) = 0
                GROUP BY u.user_id, u.name
                HAVING total_levels > 0
                ORDER BY total_levels DESC, name COLLATE NOCASE ASC, u.user_id ASC
                """,
                (chat_id, user_id),
            )
        rows = cur.fetchall()

    players: list[dict[str, Any]] = []
    for row in rows:
        nick_raw = str(row["nick"] or "").strip()
        name_raw = str(row["name"] or "").strip()
        display_name = name_raw or nick_raw or f"Игрок {int(row['user_id'])}"
        players.append(
            {
                "user_id": int(row["user_id"]),
                "name": display_name,
                "nick": nick_raw,
                "total_levels": int(row["total_levels"] or 0),
            }
        )
    return players


def _purchase_idle_building_legacy(user_id: int, chat_id: int, building_code: str) -> dict[str, Any]:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute(
            """
            SELECT COALESCE(sits, 0) AS sits
            FROM users
            WHERE user_id = ? AND chat_id = ?
            """,
            (user_id, chat_id),
        )
        user_row = cur.fetchone()
        if not user_row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Пользователь не найден в выбранном чате")

        cur.execute(
            """
            SELECT current_level, lifetime_earned_microsits
            FROM idle_player_buildings
            WHERE user_id = ? AND chat_id = ? AND building_code = ?
            """,
            (user_id, chat_id, building_code),
        )
        owned_row = cur.fetchone()
        current_level = int(owned_row["current_level"]) if owned_row else 0
        target_level = current_level + 1

        if target_level > IDLE_MAX_LEVEL:
            conn.rollback()
            raise HTTPException(status_code=409, detail="Достигнут максимальный уровень здания")

        cur.execute(
            """
            SELECT
                building_name,
                image_file,
                upgrade_cost_sits,
                income_microsits_per_hour
            FROM idle_building_levels
            WHERE building_code = ? AND level = ?
            """,
            (building_code, target_level),
        )
        target_level_row = cur.fetchone()
        if not target_level_row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Постройка не найдена")

        balance = float(user_row["sits"] or 0)
        upgrade_cost = float(target_level_row["upgrade_cost_sits"] or 0)
        if balance + 1e-9 < upgrade_cost:
            conn.rollback()
            raise HTTPException(status_code=409, detail="недостаточно сита")

        new_balance = normalize_sits(balance - upgrade_cost)
        cur.execute(
            """
            UPDATE users
            SET sits = ?
            WHERE user_id = ? AND chat_id = ?
            """,
            (new_balance, user_id, chat_id),
        )

        if owned_row:
            cur.execute(
                """
                UPDATE idle_player_buildings
                SET current_level = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND chat_id = ? AND building_code = ?
                """,
                (target_level, user_id, chat_id, building_code),
            )
            lifetime_earned = int(owned_row["lifetime_earned_microsits"] or 0)
        else:
            cur.execute(
                """
                INSERT INTO idle_player_buildings (
                    user_id,
                    chat_id,
                    building_code,
                    current_level,
                    lifetime_earned_microsits
                )
                VALUES (?, ?, ?, ?, 0)
                """,
                (user_id, chat_id, building_code, target_level),
            )
            lifetime_earned = 0

        conn.commit()

    return {
        "building_code": building_code,
        "name": str(target_level_row["building_name"]),
        "image_file": str(target_level_row["image_file"]),
        "level": target_level,
        "max_level": IDLE_MAX_LEVEL,
        "income_microsits_per_hour": int(target_level_row["income_microsits_per_hour"] or 0),
        "lifetime_earned_microsits": lifetime_earned,
        "balance": new_balance,
    }


def _purchase_idle_building(user_id: int, chat_id: int, building_code: str) -> dict[str, Any]:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")

        cur.execute(
            """
            SELECT COALESCE(sits, 0) AS sits
            FROM users
            WHERE user_id = ? AND chat_id = ?
            """,
            (user_id, chat_id),
        )
        user_row = cur.fetchone()
        if not user_row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Пользователь не найден в выбранном чате")

        cur.execute(
            """
            SELECT current_level, lifetime_earned_microsits
            FROM idle_player_buildings
            WHERE user_id = ? AND chat_id = ? AND building_code = ?
            """,
            (user_id, chat_id, building_code),
        )
        owned_row = cur.fetchone()
        current_level = int(owned_row["current_level"]) if owned_row else 0
        target_level = current_level + 1
        if target_level > IDLE_MAX_LEVEL:
            conn.rollback()
            raise HTTPException(status_code=409, detail="Достигнут максимальный уровень здания")

        cur.execute(
            """
            SELECT
                building_name,
                image_file,
                "order" AS building_order,
                upgrade_cost_sits,
                income_microsits_per_hour
            FROM idle_building_levels
            WHERE building_code = ? AND level = ?
            """,
            (building_code, target_level),
        )
        target_level_row = cur.fetchone()
        if not target_level_row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Постройка не найдена")

        building_order = int(target_level_row["building_order"] or 0)
        unlock_prev_name: str | None = None
        unlock_prev_level: int | None = None
        if building_order > 1 and current_level == 0:
            cur.execute(
                """
                SELECT
                    prev_defs.building_name AS building_name,
                    COALESCE(prev_pb.current_level, 0) AS current_level
                FROM idle_building_levels prev_defs
                LEFT JOIN idle_player_buildings prev_pb
                  ON prev_pb.user_id = ?
                 AND prev_pb.chat_id = ?
                 AND prev_pb.building_code = prev_defs.building_code
                WHERE prev_defs.level = 1
                  AND prev_defs."order" = ?
                """,
                (user_id, chat_id, building_order - 1),
            )
            prev_row = cur.fetchone()
            unlock_prev_name = str(prev_row["building_name"]) if prev_row else None
            unlock_prev_level = int(prev_row["current_level"] or 0) if prev_row else 0
            if unlock_prev_level < IDLE_UNLOCK_PREVIOUS_LEVEL:
                conn.rollback()
                if unlock_prev_name:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Открой {IDLE_UNLOCK_PREVIOUS_LEVEL} уровень {unlock_prev_name}",
                    )
                raise HTTPException(status_code=409, detail="Постройка пока заблокирована")

        balance = float(user_row["sits"] or 0)
        upgrade_cost = float(target_level_row["upgrade_cost_sits"] or 0)
        if balance + 1e-9 < upgrade_cost:
            conn.rollback()
            raise HTTPException(status_code=409, detail="недостаточно сита")

        new_balance = normalize_sits(balance - upgrade_cost)
        cur.execute(
            """
            UPDATE users
            SET sits = ?
            WHERE user_id = ? AND chat_id = ?
            """,
            (new_balance, user_id, chat_id),
        )

        if owned_row:
            cur.execute(
                """
                UPDATE idle_player_buildings
                SET current_level = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND chat_id = ? AND building_code = ?
                """,
                (target_level, user_id, chat_id, building_code),
            )
            lifetime_earned = int(owned_row["lifetime_earned_microsits"] or 0)
        else:
            cur.execute(
                """
                INSERT INTO idle_player_buildings (
                    user_id,
                    chat_id,
                    building_code,
                    current_level,
                    lifetime_earned_microsits
                )
                VALUES (?, ?, ?, ?, 0)
                """,
                (user_id, chat_id, building_code, target_level),
            )
            lifetime_earned = 0

        conn.commit()

    next_level = target_level + 1
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                level,
                upgrade_cost_sits,
                income_microsits_per_hour
            FROM idle_building_levels
            WHERE building_code = ? AND level = ?
            """,
            (building_code, next_level),
        )
        next_row = cur.fetchone()

    return {
        "building_code": building_code,
        "building_order": building_order,
        "name": str(target_level_row["building_name"]),
        "image_file": str(target_level_row["image_file"]),
        "icon_file": _icon_file_name(str(target_level_row["image_file"])),
        "level": target_level,
        "max_level": IDLE_MAX_LEVEL,
        "income_microsits_per_hour": int(target_level_row["income_microsits_per_hour"] or 0),
        "lifetime_earned_microsits": lifetime_earned,
        "balance": new_balance,
        "next_level": int(next_row["level"]) if next_row else None,
        "next_upgrade_cost_sits": normalize_sits(next_row["upgrade_cost_sits"]) if next_row else None,
        "next_upgrade_cost_microsits": _to_microsits(next_row["upgrade_cost_sits"]) if next_row else None,
        "next_income_microsits_per_hour": int(next_row["income_microsits_per_hour"]) if next_row else None,
        "unlock_required_prev_level": IDLE_UNLOCK_PREVIOUS_LEVEL if building_order > 1 else None,
        "unlock_prev_building_name": unlock_prev_name,
        "unlock_prev_building_level": unlock_prev_level,
    }


def _fallback_chat_label(user_id: int, chat_id: int) -> str:
    if user_id == chat_id:
        return "ЛС"
    return f"Чат {chat_id}"


def _ensure_chat_titles_table() -> None:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS web_chat_titles (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.commit()


def _read_cached_chat_title(chat_id: int) -> tuple[str | None, int | None]:
    _ensure_chat_titles_table()
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT title, updated_at FROM web_chat_titles WHERE chat_id = ?",
            (chat_id,),
        )
        row = cur.fetchone()
    if not row:
        return None, None
    return str(row["title"]), int(row["updated_at"])


def _upsert_cached_chat_title(chat_id: int, title: str) -> None:
    _ensure_chat_titles_table()
    now_ts = int(time.time())
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO web_chat_titles (chat_id, title, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                updated_at = excluded.updated_at
            """,
            (chat_id, title, now_ts),
        )
        conn.commit()


def _touch_cached_chat_title(chat_id: int) -> None:
    now_ts = int(time.time())
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE web_chat_titles SET updated_at = ? WHERE chat_id = ?",
            (now_ts, chat_id),
        )
        conn.commit()


def _fetch_chat_title_from_telegram(chat_id: int) -> str | None:
    if not BOT_TOKEN:
        return None

    query = urlencode({"chat_id": str(chat_id)})
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat?{query}"

    try:
        with urlopen(url, timeout=CHAT_TITLE_FETCH_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    if not isinstance(payload, dict) or not payload.get("ok"):
        return None

    result = payload.get("result")
    if not isinstance(result, dict):
        return None

    title = result.get("title")
    if not title:
        username = result.get("username")
        if username:
            title = f"@{username}"
    if not title:
        return None

    title = str(title).strip()
    return title or None


def _resolve_chat_label(user_id: int, chat_id: int) -> str:
    if user_id == chat_id:
        return "ЛС"

    fallback_label = _fallback_chat_label(user_id, chat_id)
    now_ts = int(time.time())
    cached_title, cached_updated_at = _read_cached_chat_title(chat_id)
    if cached_title and cached_updated_at and (now_ts - cached_updated_at) <= CHAT_TITLE_CACHE_TTL_SECONDS:
        return cached_title

    fetched_title = _fetch_chat_title_from_telegram(chat_id)
    if fetched_title:
        _upsert_cached_chat_title(chat_id, fetched_title)
        return fetched_title

    # Cache failed lookups too, so switching chats does not trigger repeated Telegram API calls.
    if cached_title:
        if cached_title != fallback_label:
            _upsert_cached_chat_title(chat_id, fallback_label)
        else:
            _touch_cached_chat_title(chat_id)
        return fallback_label

    _upsert_cached_chat_title(chat_id, fallback_label)
    return fallback_label



def _get_user_accounts(user_id: int) -> list[dict[str, Any]]:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id, chat_id, COALESCE(name, '') AS name, COALESCE(sits, 0) AS sits
            FROM users
            WHERE user_id = ?
            ORDER BY CASE WHEN chat_id = user_id THEN 0 ELSE 1 END, chat_id
            """,
            (user_id,),
        )
        rows = cur.fetchall()

    accounts: list[dict[str, Any]] = []
    for row in rows:
        chat_id = int(row["chat_id"])
        row_user_id = int(row["user_id"])
        accounts.append(
            {
                "chat_id": chat_id,
                "label": _resolve_chat_label(row_user_id, chat_id),
                "name": row["name"] or "",
                "balance": normalize_sits(row["sits"] or 0),
            }
        )
    return accounts


def _get_user_name_for_session(user_id: int) -> str:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COALESCE(name, '') AS name
            FROM users
            WHERE user_id = ? AND COALESCE(name, '') <> ''
            ORDER BY CASE WHEN chat_id = user_id THEN 0 ELSE 1 END, chat_id
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
    return row["name"] if row else ""


def _build_data_check_string(auth_data: dict[str, Any]) -> str:
    pairs: list[str] = []
    for key in sorted(auth_data.keys()):
        if key == "hash":
            continue
        value = auth_data.get(key)
        if value is None:
            continue
        pairs.append(f"{key}={value}")
    return "\n".join(pairs)


def _verify_telegram_auth(auth_data: dict[str, Any]) -> bool:
    if not BOT_TOKEN:
        return False
    recv_hash = str(auth_data.get("hash", ""))
    if not recv_hash:
        return False
    auth_date_raw = auth_data.get("auth_date")
    try:
        auth_date = int(auth_date_raw)
    except (TypeError, ValueError):
        return False

    if int(time.time()) - auth_date > TELEGRAM_AUTH_MAX_AGE_SECONDS:
        return False

    data_check_string = _build_data_check_string(auth_data)
    secret_key = hashlib.sha256(BOT_TOKEN.encode("utf-8")).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(calc_hash, recv_hash)


def _b64_url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64_url_decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign_payload(payload: dict[str, Any]) -> str:
    if not SESSION_SECRET:
        raise RuntimeError("WEB_SESSION_SECRET (or BOT_TOKEN) is required for session signing")
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload_b64 = _b64_url_encode(payload_json)
    signature = hmac.new(SESSION_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def _read_payload(token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token or not SESSION_SECRET:
        return None
    payload_b64, signature = token.split(".", 1)
    expected = hmac.new(SESSION_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload_json = _b64_url_decode(payload_b64).decode("utf-8")
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            return None
        return payload
    except Exception:
        return None


def _set_session_cookie(response: JSONResponse, payload: dict[str, Any]) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=_sign_payload(payload),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )


def _clear_session_cookie(response: JSONResponse) -> None:
    response.delete_cookie(COOKIE_NAME)


def _require_ai_worker(request: Request) -> None:
    if not AI_WORKER_TOKEN:
        raise HTTPException(status_code=503, detail="AI worker API is not configured")
    auth_header = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not auth_header.startswith(prefix):
        raise HTTPException(status_code=401, detail="Missing worker token")
    token = auth_header[len(prefix):].strip()
    if not hmac.compare_digest(token, AI_WORKER_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid worker token")


def _send_telegram_message(chat_id: int, text: str, *, reply_to_message_id: int | None = None) -> int | None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id
        payload["allow_sending_without_reply"] = True

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = UrlRequest(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Telegram sendMessage failed: {exc}") from exc

    if not response_data.get("ok"):
        raise RuntimeError(f"Telegram sendMessage returned error: {response_data}")
    result = response_data.get("result") or {}
    message_id = result.get("message_id")
    return int(message_id) if message_id is not None else None


def _set_telegram_reaction(chat_id: int, message_id: int, emoji: str) -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reaction": [{"type": "emoji", "emoji": emoji}],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = UrlRequest(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setMessageReaction",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Telegram setMessageReaction failed: {exc}") from exc

    if not response_data.get("ok"):
        raise RuntimeError(f"Telegram setMessageReaction returned error: {response_data}")


def _get_ai_task_user_context(chat_id: int, user_id: int) -> tuple[str, str | None]:
    with closing(sqlite3.connect(DB_FILE)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COALESCE(name, '') AS name, COALESCE(nick, '') AS nick
            FROM users
            WHERE chat_id = ? AND user_id = ?
            LIMIT 1
            """,
            (chat_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        return str(user_id), None
    name = str(row["name"] or "").strip() or str(user_id)
    nick = str(row["nick"] or "").strip() or None
    return name, nick


def _create_response_from_ai_context(
    *,
    chat_id: int,
    user_id: int,
    request_message_id: int,
    message_text: str,
    trigger_reason: str,
    web_context: str | None = None,
) -> int | None:
    requester_name, requester_nick = _get_ai_task_user_context(chat_id, user_id)
    return create_response_task(
        chat_id=chat_id,
        requester_user_id=user_id,
        request_message_id=request_message_id,
        message_text=message_text,
        requester_name=requester_name,
        requester_nick=requester_nick,
        trigger_reason=trigger_reason,
        web_context=web_context,
    )


def _set_in_progress_reaction_best_effort(chat_id: int, request_message_id: int, *, label: str, task_id: int) -> None:
    try:
        _set_telegram_reaction(chat_id, request_message_id, RESPONSE_REACTION_IN_PROGRESS)
    except Exception as exc:
        logger.warning("%s %s failed to set in-progress reaction: %s", label, task_id, exc)


def _require_session(request: Request) -> dict[str, Any]:
    payload = _read_payload(request.cookies.get(COOKIE_NAME))
    if not payload:
        raise HTTPException(status_code=401, detail="Not authorized")
    return payload


def _prepare_state(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    user_id = int(payload["telegram_user_id"])
    accounts = _get_user_accounts(user_id)
    chat_ids = {item["chat_id"] for item in accounts}

    selected_chat_id = payload.get("selected_chat_id")
    if selected_chat_id not in chat_ids:
        selected_chat_id = None

    selected = None
    if selected_chat_id is not None:
        for item in accounts:
            if item["chat_id"] == selected_chat_id:
                selected = item
                break
    visit_target: dict[str, Any] | None = None
    if selected:
        visit_target = _resolve_visit_target(
            owner_user_id=user_id,
            chat_id=int(selected["chat_id"]),
            visit_user_id_raw=payload.get("visit_user_id"),
            require_buildings=True,
        )
    default_settings = {
        "hide_base": False,
        "reject_geyser_catch_by_guest": False,
        "notify_group_masturbation": True,
        "notify_group_masturbation_sound": True,
    }
    selected_settings = default_settings
    visit_geyser_blocked = False
    geyser_caught_today = 0
    geyser_owner_user_id = user_id
    if selected:
        selected_chat = int(selected["chat_id"])
        selected_settings = _get_web_settings(user_id, selected_chat)
        geyser_owner_user_id = int(visit_target["user_id"]) if visit_target else user_id
        if visit_target:
            visit_settings = _get_web_settings(geyser_owner_user_id, selected_chat)
            visit_geyser_blocked = bool(visit_settings["reject_geyser_catch_by_guest"])
        geyser_caught_today = _get_geyser_catches_for_today(geyser_owner_user_id, selected_chat)
        group_event_state = _build_group_event_state(selected_chat, user_id)
    else:
        group_event_state = {
            "active": False,
            "phase": "idle",
            "event_token": None,
            "prepare_seconds_left": 0,
            "join_seconds_left": 0,
            "viewer_role": "none",
            "viewer_is_starter": False,
            "can_start": False,
            "can_remind": False,
            "can_join_participant": False,
            "can_join_spectator": False,
            "start_cost_millisits": _sits_to_microsits(EVENT_COST),
            "join_cost_millisits": _sits_to_microsits(JOIN_COST),
            "participants": [],
            "spectators": [],
            "result": None,
        }

    state = {
        "authorized": True,
        "bot_username": BOT_USERNAME,
        "server_now_iso": _server_now_iso(),
        "user": {
            "id": user_id,
            "first_name": payload.get("first_name", ""),
            "last_name": payload.get("last_name", ""),
            "username": payload.get("username", ""),
        },
        "chats": [{"chat_id": item["chat_id"], "label": item["label"]} for item in accounts],
        "selected_chat_id": selected["chat_id"] if selected else None,
        "selected_chat_label": selected["label"] if selected else None,
        "balance": selected["balance"] if selected else None,
        "geyser_caught_today": geyser_caught_today if selected else 0,
        "geyser_daily_limit": GEYSER_DAILY_LIMIT,
        "geyser_owner_user_id": geyser_owner_user_id if selected else None,
        "geyser_owner_name": str(visit_target["name"]) if visit_target else None,
        "visit_geyser_blocked": visit_geyser_blocked if selected else False,
        "web_settings": selected_settings if selected else default_settings,
        "view_mode": "visit" if visit_target else "self",
        "group_event": group_event_state,
        "visit": {
            "active": bool(visit_target),
            "user_id": int(visit_target["user_id"]) if visit_target else None,
            "name": str(visit_target["name"]) if visit_target else None,
        },
    }

    next_payload = dict(payload)
    next_payload["selected_chat_id"] = selected_chat_id
    next_payload["visit_user_id"] = int(visit_target["user_id"]) if visit_target else None
    return state, next_payload


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"bot_username": BOT_USERNAME},
    )


@app.get("/api/state")
def get_state(request: Request) -> JSONResponse:
    payload = _read_payload(request.cookies.get(COOKIE_NAME))
    if not payload:
        return JSONResponse(
            {
                "authorized": False,
                "bot_username": BOT_USERNAME,
                "server_now_iso": _server_now_iso(),
            }
        )

    state, next_payload = _prepare_state(payload)
    response = JSONResponse(state)
    _set_session_cookie(response, next_payload)
    return response


@app.post("/api/auth/telegram")
def auth_telegram(data: TelegramAuthRequest) -> JSONResponse:
    auth_data = data.auth_data
    if not _verify_telegram_auth(auth_data):
        raise HTTPException(status_code=401, detail="Invalid Telegram auth data")

    try:
        telegram_user_id = int(auth_data["id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Telegram user id is missing")

    payload: dict[str, Any] = {
        "telegram_user_id": telegram_user_id,
        "first_name": str(auth_data.get("first_name", "")),
        "last_name": str(auth_data.get("last_name", "")),
        "username": str(auth_data.get("username", "")),
        "selected_chat_id": None,
    }

    state, next_payload = _prepare_state(payload)
    response = JSONResponse(state)
    _set_session_cookie(response, next_payload)
    return response


@app.post("/api/auth/code")
def auth_by_code(data: CodeAuthRequest) -> JSONResponse:
    try:
        user_id = consume_auth_code(data.code)
    except AuthCodeInvalidError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except AuthCodeExpiredError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except AuthCodeUsedError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except AuthCodeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    display_name = _get_user_name_for_session(user_id)
    payload: dict[str, Any] = {
        "telegram_user_id": user_id,
        "first_name": display_name,
        "last_name": "",
        "username": "",
        "selected_chat_id": None,
    }

    state, next_payload = _prepare_state(payload)
    response = JSONResponse(state)
    _set_session_cookie(response, next_payload)
    return response


@app.post("/api/select-chat")
def select_chat(request: Request, data: SelectChatRequest) -> JSONResponse:
    payload = _require_session(request)
    user_id = int(payload["telegram_user_id"])
    accounts = _get_user_accounts(user_id)
    allowed_chat_ids = {item["chat_id"] for item in accounts}
    if data.chat_id not in allowed_chat_ids:
        raise HTTPException(status_code=403, detail="Chat is not available for this user")

    payload = dict(payload)
    payload["selected_chat_id"] = data.chat_id
    payload["visit_user_id"] = None
    state, next_payload = _prepare_state(payload)
    response = JSONResponse(state)
    _set_session_cookie(response, next_payload)
    return response


@app.post("/api/visit/start")
def start_visit(request: Request, data: StartVisitRequest) -> JSONResponse:
    payload = _require_session(request)
    user_id, chat_id = _require_selected_user_chat(request)
    visit_target = _resolve_visit_target(
        owner_user_id=user_id,
        chat_id=chat_id,
        visit_user_id_raw=data.target_user_id,
        require_buildings=True,
    )
    if not visit_target:
        raise HTTPException(status_code=404, detail="Игрок недоступен для визита")

    payload = dict(payload)
    payload["visit_user_id"] = int(visit_target["user_id"])
    state, next_payload = _prepare_state(payload)
    response = JSONResponse(state)
    _set_session_cookie(response, next_payload)
    return response


@app.post("/api/visit/leave")
def leave_visit(request: Request) -> JSONResponse:
    payload = _require_session(request)
    payload = dict(payload)
    payload["visit_user_id"] = None
    state, next_payload = _prepare_state(payload)
    response = JSONResponse(state)
    _set_session_cookie(response, next_payload)
    return response


@app.post("/api/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    _clear_session_cookie(response)
    return response


@app.on_event("startup")
async def startup_idle_income_worker() -> None:
    global idle_income_task
    group_store.initialize(reset_runtime_state=False)
    _ensure_idle_catalog_ready(force=True)
    _ensure_idle_service_tables()
    _ensure_geyser_tables()
    _ensure_web_settings_table()
    ensure_web_chat_media_schema()
    _ensure_daily_schema_compatibility()
    try:
        _catch_up_idle_income()
    except Exception:
        logger.exception("Initial idle income catch-up failed")
    if idle_income_task is None or idle_income_task.done():
        idle_income_task = asyncio.create_task(_idle_income_worker())


@app.on_event("shutdown")
async def shutdown_idle_income_worker() -> None:
    global idle_income_task
    if idle_income_task is None:
        return
    idle_income_task.cancel()
    try:
        await idle_income_task
    except asyncio.CancelledError:
        pass
    idle_income_task = None


@app.get("/api/idle/buildings")
def get_idle_buildings(request: Request) -> JSONResponse:
    _ensure_idle_catalog_ready()
    try:
        _catch_up_idle_income()
    except Exception:
        logger.exception("Idle income catch-up failed before reading buildings")

    payload = _require_session(request)
    user_id, chat_id = _require_selected_user_chat(request)
    visit_target = _resolve_visit_target(
        owner_user_id=user_id,
        chat_id=chat_id,
        visit_user_id_raw=payload.get("visit_user_id"),
        require_buildings=True,
    )
    buildings_owner_user_id = int(visit_target["user_id"]) if visit_target else user_id
    try:
        buildings = _get_idle_buildings_state(buildings_owner_user_id, chat_id)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=500,
            detail="Idle-таблицы не найдены. Выполните createdb.py на сервере.",
        ) from exc
    return JSONResponse(
        {
            "chat_id": chat_id,
            "user_id": user_id,
            "buildings_owner_user_id": buildings_owner_user_id,
            "view_mode": "visit" if visit_target else "self",
            "visit": {
                "active": bool(visit_target),
                "user_id": int(visit_target["user_id"]) if visit_target else None,
                "name": str(visit_target["name"]) if visit_target else None,
            },
            "buildings": buildings,
        }
    )


@app.get("/api/idle/players")
def get_idle_players(request: Request) -> JSONResponse:
    _ensure_idle_catalog_ready()
    user_id, chat_id = _require_selected_user_chat(request)
    try:
        players = _get_idle_chat_players_state(user_id, chat_id)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=500,
            detail="Idle-таблицы не найдены. Выполните createdb.py на сервере.",
        ) from exc
    return JSONResponse(
        {
            "chat_id": chat_id,
            "user_id": user_id,
            "players": players,
        }
    )


@app.get("/api/idle/sits/balance")
def get_idle_sits_balance(request: Request) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    balance = _get_user_balance(user_id, chat_id)
    return JSONResponse(
        {
            "chat_id": chat_id,
            "user_id": user_id,
            "balance": normalize_sits(balance),
        }
    )


@app.post("/api/web-settings")
def update_web_settings(request: Request, data: UpdateWebSettingsRequest) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    settings = _update_web_settings(
        user_id=user_id,
        chat_id=chat_id,
        hide_base=data.hide_base,
        reject_geyser_catch_by_guest=data.reject_geyser_catch_by_guest,
        notify_group_masturbation=data.notify_group_masturbation,
        notify_group_masturbation_sound=data.notify_group_masturbation_sound,
    )
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "web_settings": settings,
        }
    )


@app.post("/api/idle/sits/transfer")
def transfer_idle_sits(request: Request, data: TransferSitsRequest) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    transfer_result = _transfer_sits(
        user_id=user_id,
        chat_id=chat_id,
        receiver_user_id=int(data.receiver_user_id),
        amount_raw=data.amount,
    )
    return JSONResponse(
        {
            "ok": True,
            **transfer_result,
        }
    )


@app.post("/api/idle/buildings/purchase")
def purchase_idle_building(request: Request, data: PurchaseIdleBuildingRequest) -> JSONResponse:
    _ensure_idle_catalog_ready()
    building_code = str(data.building_code or "").strip().lower()
    if not building_code:
        raise HTTPException(status_code=400, detail="building_code is required")

    try:
        _catch_up_idle_income()
    except Exception:
        logger.exception("Idle income catch-up failed before purchase")

    payload = _require_session(request)
    user_id, chat_id = _require_selected_user_chat(request)
    visit_target = _resolve_visit_target(
        owner_user_id=user_id,
        chat_id=chat_id,
        visit_user_id_raw=payload.get("visit_user_id"),
        require_buildings=False,
    )
    if visit_target:
        raise HTTPException(status_code=409, detail="В режиме гостя улучшение недоступно. Нажмите ДОМОЙ.")
    try:
        purchased = _purchase_idle_building(user_id, chat_id, building_code)
        buildings = _get_idle_buildings_state(user_id, chat_id)
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=500,
            detail="Idle-таблицы не найдены. Выполните createdb.py на сервере.",
        ) from exc
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "balance": purchased["balance"],
            "purchased": purchased,
            "buildings": buildings,
        }
    )


@app.get("/api/geyser/state")
def geyser_state(request: Request) -> JSONResponse:
    payload = _require_session(request)
    user_id, chat_id = _require_selected_user_chat(request)
    visit_target = _resolve_visit_target(
        owner_user_id=user_id,
        chat_id=chat_id,
        visit_user_id_raw=payload.get("visit_user_id"),
        require_buildings=True,
    )
    geyser_owner_user_id = int(visit_target["user_id"]) if visit_target else user_id
    visit_geyser_blocked = False
    if visit_target:
        visit_geyser_blocked = bool(_get_web_settings(geyser_owner_user_id, chat_id)["reject_geyser_catch_by_guest"])
    caught_today = _get_geyser_catches_for_today(geyser_owner_user_id, chat_id)
    return JSONResponse(
        {
            "chat_id": chat_id,
            "user_id": user_id,
            "caught_today": caught_today,
            "daily_limit": GEYSER_DAILY_LIMIT,
            "geyser_owner_user_id": geyser_owner_user_id,
            "geyser_owner_name": str(visit_target["name"]) if visit_target else None,
            "visit_geyser_blocked": visit_geyser_blocked,
            "view_mode": "visit" if visit_target else "self",
            "visit": {
                "active": bool(visit_target),
                "user_id": int(visit_target["user_id"]) if visit_target else None,
                "name": str(visit_target["name"]) if visit_target else None,
            },
        }
    )


@app.post("/api/geyser/catch")
def geyser_catch(request: Request) -> JSONResponse:
    payload = _require_session(request)
    user_id, chat_id = _require_selected_user_chat(request)
    visit_target = _resolve_visit_target(
        owner_user_id=user_id,
        chat_id=chat_id,
        visit_user_id_raw=payload.get("visit_user_id"),
        require_buildings=True,
    )
    visit_geyser_blocked = False
    if visit_target:
        visit_geyser_blocked = bool(
            _get_web_settings(int(visit_target["user_id"]), chat_id)["reject_geyser_catch_by_guest"]
        )
        if visit_geyser_blocked:
            raise HTTPException(status_code=403, detail="Хозяин базы скрыл гейзеры от гостей")
    beneficiary_user_id = int(visit_target["user_id"]) if visit_target else None
    result = _catch_geyser_for_today(
        user_id=user_id,
        chat_id=chat_id,
        beneficiary_user_id=beneficiary_user_id,
    )
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "view_mode": "visit" if visit_target else "self",
            "visit": {
                "active": bool(visit_target),
                "user_id": int(visit_target["user_id"]) if visit_target else None,
                "name": str(visit_target["name"]) if visit_target else None,
            },
            "visit_geyser_blocked": visit_geyser_blocked,
            **result,
        }
    )


@app.get("/api/group-event/state")
def group_event_state(request: Request) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "group_event": _build_group_event_state(chat_id, user_id),
        }
    )


@app.get("/api/chat/messages")
def chat_messages(request: Request, after_message_id: int | None = None) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    messages = _get_chat_messages(chat_id=chat_id, after_message_id=after_message_id, limit=100)
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "messages": messages,
        }
    )


@app.get("/api/chat/media/{attachment_id}")
def chat_media(request: Request, attachment_id: int) -> FileResponse:
    _user_id, chat_id = _require_selected_user_chat(request)
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, chat_id, media_type, local_path, mime_type
            FROM web_chat_attachments
            WHERE id = ?
                AND chat_id = ?
                AND media_type = 'photo'
            """,
            (int(attachment_id), chat_id),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")

    media_root = Path(WEB_CHAT_MEDIA_DIR).resolve()
    file_path = Path(str(row["local_path"] or "")).resolve()
    try:
        file_path.relative_to(media_root)
    except ValueError:
        logger.warning("Blocked web chat media path outside storage: attachment_id=%s", attachment_id)
        raise HTTPException(status_code=404, detail="Attachment not found")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file not found")

    return FileResponse(file_path, media_type=str(row["mime_type"] or "image/jpeg"))


@app.post("/api/chat/messages")
def chat_message_send(request: Request, data: ChatMessageRequest) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    text = _sanitize_web_chat_text(data.text)
    profile = _get_user_profile(chat_id, user_id)
    display_name = str(profile["name"] or f"Игрок {user_id}")
    group_store.enqueue_outbox(
        chat_id=chat_id,
        kind="send_web_chat_message",
        payload={
            "user_id": user_id,
            "display_name": display_name,
            "text": text,
        },
    )
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "queued": True,
        }
    )


def _get_serialized_daily_event_or_404(chat_id: int, daily_id: int, viewer_user_id: int) -> dict[str, Any]:
    with _get_connection() as conn:
        cur = conn.cursor()
        row = _fetch_daily_event_row(cur, chat_id=chat_id, daily_id=daily_id)
        if not row:
            raise HTTPException(status_code=404, detail="Дейлик не найден")
    participants_map = _get_daily_participants_map(chat_id=chat_id, daily_ids=[daily_id])
    participants = participants_map.get(daily_id, [])
    return _serialize_daily_event(row=row, viewer_user_id=viewer_user_id, participants=participants)


@app.get("/api/daily/upcoming")
def daily_upcoming(request: Request) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    now_dt = datetime.now(DAILY_TIMEZONE)
    now_key = now_dt.strftime("%Y-%m-%d %H:%M")
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT *
            FROM daily_events
            WHERE chat_id = ?
              AND (date || ' ' || time) >= ?
            ORDER BY date || ' ' || time ASC, id ASC
            """,
            (chat_id, now_key),
        )
        rows = cur.fetchall()

    daily_ids = [int(row["id"]) for row in rows]
    participants_map = _get_daily_participants_map(chat_id=chat_id, daily_ids=daily_ids)
    events = [
        _serialize_daily_event(
            row=row,
            viewer_user_id=user_id,
            participants=participants_map.get(int(row["id"]), []),
            now_dt=now_dt,
        )
        for row in rows
    ]
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "events": events,
        }
    )


@app.get("/api/daily/expired")
def daily_expired(request: Request, cursor: str | None = None, limit: int = DAILY_EXPIRED_PAGE_SIZE_DEFAULT) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    page_size = max(1, min(int(limit or DAILY_EXPIRED_PAGE_SIZE_DEFAULT), DAILY_EXPIRED_PAGE_SIZE_MAX))
    parsed_cursor = _parse_daily_cursor(cursor)
    now_dt = datetime.now(DAILY_TIMEZONE)
    now_key = now_dt.strftime("%Y-%m-%d %H:%M")

    params: list[Any] = [chat_id, now_key]
    cursor_clause = ""
    if parsed_cursor:
        cursor_key, cursor_id = parsed_cursor
        cursor_clause = " AND ((date || ' ' || time) < ? OR ((date || ' ' || time) = ? AND id < ?)) "
        params.extend([cursor_key, cursor_key, cursor_id])
    params.append(page_size + 1)

    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT *
            FROM daily_events
            WHERE chat_id = ?
              AND (date || ' ' || time) < ?
              {cursor_clause}
            ORDER BY date || ' ' || time DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        rows = cur.fetchall()

    has_more = len(rows) > page_size
    page_rows = rows[:page_size]
    daily_ids = [int(row["id"]) for row in page_rows]
    participants_map = _get_daily_participants_map(chat_id=chat_id, daily_ids=daily_ids)
    events = [
        _serialize_daily_event(
            row=row,
            viewer_user_id=user_id,
            participants=participants_map.get(int(row["id"]), []),
            now_dt=now_dt,
        )
        for row in page_rows
    ]
    next_cursor = _daily_cursor_from_row(page_rows[-1]) if has_more and page_rows else None
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "events": events,
            "next_cursor": next_cursor,
        }
    )


@app.post("/api/daily/events")
async def daily_create_event(request: Request, data: DailyEventUpsertRequest) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    name = str(data.name or "").strip()
    if not name:
        _daily_validation_error({"name": "Укажите название дейлика"})

    parsed_datetime = _parse_daily_datetime_input(data.datetime)
    if parsed_datetime < datetime.now(DAILY_TIMEZONE):
        _daily_validation_error({"datetime": "Дата и время уже в прошлом"})
    date_value = parsed_datetime.strftime("%Y-%m-%d")
    time_value = parsed_datetime.strftime("%H:%M")
    description = str(data.description or "").strip()
    link = _normalize_daily_link(data.link)
    cars = _normalize_daily_cars(data.cars)

    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO daily_events (
                chat_id,
                creator_user_id,
                name,
                description,
                date,
                time,
                cars,
                link
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (chat_id, user_id, name, description, date_value, time_value, cars, link),
        )
        daily_id = int(cur.lastrowid)
        conn.commit()

    calendar_event_id: str | None = None
    try:
        calendar_event_id = await create_calendar_event(
            chat_id=chat_id,
            daily_name=name,
            daily_description=description,
            daily_datetime=parsed_datetime,
            daily_link=link,
            daily_id=daily_id,
            bot_instance=None,
        )
    except Exception:
        logger.exception("Daily create calendar sync failed for daily_id=%s", daily_id)
    if calendar_event_id:
        try:
            with _get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE daily_events SET calendar_event_id = ? WHERE id = ? AND chat_id = ?",
                    (calendar_event_id, daily_id, chat_id),
                )
                conn.commit()
        except sqlite3.OperationalError:
            logger.exception("daily_events has no calendar_event_id column, skipping persistence")

    if chat_id == TARGET_CHAT_ID:
        if calendar_event_id:
            _enqueue_group_html_text(chat_id, f"✅ Веб: дейлик <b>{escape(name)}</b> добавлен в Google Календарь.")
        else:
            _enqueue_group_html_text(chat_id, f"⚠️ Веб: не удалось синхронизировать дейлик <b>{escape(name)}</b> с Google Календарём.")

    event_payload = _get_serialized_daily_event_or_404(chat_id=chat_id, daily_id=daily_id, viewer_user_id=user_id)
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "event": event_payload,
        }
    )


@app.patch("/api/daily/events/{daily_id}")
async def daily_update_event(request: Request, daily_id: int, data: DailyEventUpsertRequest) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    with _get_connection() as conn:
        cur = conn.cursor()
        row = _fetch_daily_event_row(cur, chat_id=chat_id, daily_id=daily_id)
        if not row:
            raise HTTPException(status_code=404, detail="Дейлик не найден")
        if not _daily_can_manage_event(user_id=user_id, creator_user_id=int(row["creator_user_id"])):
            raise HTTPException(status_code=403, detail="Недостаточно прав для редактирования")
        if _is_daily_expired(row):
            raise HTTPException(status_code=409, detail="Прошедший дейлик доступен только для просмотра")

        updates: dict[str, Any] = {}
        if data.name is not None:
            name = str(data.name or "").strip()
            if not name:
                _daily_validation_error({"name": "Укажите название дейлика"})
            updates["name"] = name
        if data.description is not None:
            updates["description"] = str(data.description or "").strip()
        if data.datetime is not None:
            parsed_datetime = _parse_daily_datetime_input(data.datetime)
            if parsed_datetime < datetime.now(DAILY_TIMEZONE):
                _daily_validation_error({"datetime": "Дата и время уже в прошлом"})
            updates["date"] = parsed_datetime.strftime("%Y-%m-%d")
            updates["time"] = parsed_datetime.strftime("%H:%M")
        if data.link is not None:
            updates["link"] = _normalize_daily_link(data.link)
        if data.cars is not None:
            updates["cars"] = _normalize_daily_cars(data.cars)

        if updates:
            # If car mode is turned off, keep all drivers as participants.
            if updates.get("cars") == "нет":
                cur.execute(
                    "UPDATE daily_participants SET is_driver = 0 WHERE daily_id = ? AND COALESCE(is_driver, 0) != 0",
                    (daily_id,),
                )
            set_sql = ", ".join(f"{key} = ?" for key in updates.keys())
            cur.execute(
                f"UPDATE daily_events SET {set_sql} WHERE id = ? AND chat_id = ?",
                (*updates.values(), daily_id, chat_id),
            )
            conn.commit()

        updated_row = _fetch_daily_event_row(cur, chat_id=chat_id, daily_id=daily_id)
        if not updated_row:
            raise HTTPException(status_code=404, detail="Дейлик не найден")

    updated_name = str(updated_row["name"] or "")
    calendar_event_id = str(updated_row["calendar_event_id"] or "").strip() if "calendar_event_id" in updated_row.keys() else ""
    if calendar_event_id:
        try:
            await update_calendar_event(
                calendar_event_id=calendar_event_id,
                chat_id=chat_id,
                daily_name=str(updated_row["name"] or ""),
                daily_description=str(updated_row["description"] or ""),
                daily_datetime=_combine_daily_datetime_from_row(updated_row),
                daily_link=str(updated_row["link"] or "") or None,
                daily_id=int(updated_row["id"]),
                bot_instance=None,
            )
            if chat_id == TARGET_CHAT_ID:
                _enqueue_group_html_text(chat_id, f"✅ Веб: дейлик <b>{escape(updated_name)}</b> обновлён в Google Календаре.")
        except Exception:
            logger.exception("Daily update calendar sync failed for daily_id=%s", daily_id)
            if chat_id == TARGET_CHAT_ID:
                _enqueue_group_html_text(chat_id, f"⚠️ Веб: ошибка синхронизации дейлика <b>{escape(updated_name)}</b> с Google Календарём.")

    event_payload = _get_serialized_daily_event_or_404(chat_id=chat_id, daily_id=daily_id, viewer_user_id=user_id)
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "event": event_payload,
        }
    )


@app.delete("/api/daily/events/{daily_id}")
async def daily_delete_event(request: Request, daily_id: int) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    calendar_event_id = ""
    daily_name = ""
    with _get_connection() as conn:
        cur = conn.cursor()
        row = _fetch_daily_event_row(cur, chat_id=chat_id, daily_id=daily_id)
        if not row:
            raise HTTPException(status_code=404, detail="Дейлик не найден")
        if not _daily_can_manage_event(user_id=user_id, creator_user_id=int(row["creator_user_id"])):
            raise HTTPException(status_code=403, detail="Недостаточно прав для удаления")
        if _is_daily_expired(row):
            raise HTTPException(status_code=409, detail="Прошедший дейлик доступен только для просмотра")

        daily_name = str(row["name"] or "")
        calendar_event_id = str(row["calendar_event_id"] or "").strip() if "calendar_event_id" in row.keys() else ""
        cur.execute("DELETE FROM daily_participants WHERE daily_id = ?", (daily_id,))
        cur.execute("DELETE FROM daily_events WHERE id = ? AND chat_id = ?", (daily_id, chat_id))
        conn.commit()

    if calendar_event_id:
        try:
            await delete_calendar_event(
                calendar_event_id=calendar_event_id,
                chat_id=chat_id,
                bot_instance=None,
            )
            if chat_id == TARGET_CHAT_ID:
                _enqueue_group_html_text(chat_id, f"🗑️ Веб: дейлик <b>{escape(daily_name)}</b> удалён из Google Календаря.")
        except Exception:
            logger.exception("Daily delete calendar sync failed for daily_id=%s", daily_id)
            if chat_id == TARGET_CHAT_ID:
                _enqueue_group_html_text(chat_id, f"⚠️ Веб: ошибка удаления дейлика <b>{escape(daily_name)}</b> из Google Календаря.")

    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "deleted_id": daily_id,
        }
    )


@app.post("/api/daily/events/{daily_id}/toggle-participation")
def daily_toggle_participation(request: Request, daily_id: int) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    with _get_connection() as conn:
        cur = conn.cursor()
        row = _fetch_daily_event_row(cur, chat_id=chat_id, daily_id=daily_id)
        if not row:
            raise HTTPException(status_code=404, detail="Дейлик не найден")
        if _is_daily_expired(row):
            raise HTTPException(status_code=409, detail="Прошедший дейлик доступен только для просмотра")

        cur.execute(
            "SELECT 1 FROM daily_participants WHERE daily_id = ? AND user_id = ? LIMIT 1",
            (daily_id, user_id),
        )
        exists = cur.fetchone() is not None
        if exists:
            cur.execute(
                "DELETE FROM daily_participants WHERE daily_id = ? AND user_id = ?",
                (daily_id, user_id),
            )
            participant_state = False
        else:
            cur.execute(
                "INSERT INTO daily_participants (daily_id, user_id, is_driver) VALUES (?, ?, 0)",
                (daily_id, user_id),
            )
            participant_state = True
        conn.commit()

    event_payload = _get_serialized_daily_event_or_404(chat_id=chat_id, daily_id=daily_id, viewer_user_id=user_id)
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "viewer_is_participant": participant_state,
            "event": event_payload,
        }
    )


@app.post("/api/daily/events/{daily_id}/toggle-driver")
def daily_toggle_driver(request: Request, daily_id: int) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    with _get_connection() as conn:
        cur = conn.cursor()
        row = _fetch_daily_event_row(cur, chat_id=chat_id, daily_id=daily_id)
        if not row:
            raise HTTPException(status_code=404, detail="Дейлик не найден")
        if _is_daily_expired(row):
            raise HTTPException(status_code=409, detail="Прошедший дейлик доступен только для просмотра")
        if not _daily_cars_enabled(row["cars"]):
            raise HTTPException(status_code=409, detail="Для этого дейлика режим водителя недоступен")

        cur.execute(
            "SELECT COALESCE(is_driver, 0) AS is_driver FROM daily_participants WHERE daily_id = ? AND user_id = ? LIMIT 1",
            (daily_id, user_id),
        )
        participant_row = cur.fetchone()
        if not participant_row:
            next_driver_state = 1
            cur.execute(
                "INSERT INTO daily_participants (daily_id, user_id, is_driver) VALUES (?, ?, ?)",
                (daily_id, user_id, next_driver_state),
            )
        else:
            next_driver_state = 0 if bool(int(participant_row["is_driver"] or 0)) else 1
            cur.execute(
                "UPDATE daily_participants SET is_driver = ? WHERE daily_id = ? AND user_id = ?",
                (next_driver_state, daily_id, user_id),
            )
        conn.commit()

    event_payload = _get_serialized_daily_event_or_404(chat_id=chat_id, daily_id=daily_id, viewer_user_id=user_id)
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "viewer_is_driver": bool(next_driver_state),
            "event": event_payload,
        }
    )


@app.post("/api/daily/events/{daily_id}/tag-participants")
def daily_tag_participants(request: Request, daily_id: int) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    event_payload = _get_serialized_daily_event_or_404(chat_id=chat_id, daily_id=daily_id, viewer_user_id=user_id)
    if bool(event_payload["expired"]):
        raise HTTPException(status_code=409, detail="Прошедший дейлик доступен только для просмотра")
    if not bool(event_payload["viewer_is_participant"]):
        raise HTTPException(status_code=403, detail="Тегнуть участников может только участник дейлика")

    all_participants = list(event_payload["all_participants"] or [])
    if not all_participants:
        raise HTTPException(status_code=409, detail="В этом дейлике пока нет участников")

    clicked_profile = _get_user_profile(chat_id, user_id)
    clicked_name = str(clicked_profile["name"] or f"Игрок {user_id}")
    mentions = [str(item["mention_html"]) for item in all_participants]
    message_text = (
        f"{escape(clicked_name)} тегает всех участников дейлика <b>{escape(str(event_payload['name']))}</b>:\n\n"
        f"{', '.join(mentions)}"
    )
    _enqueue_group_html_text(chat_id=chat_id, text=message_text)
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "tagged_count": len(mentions),
        }
    )


def _group_event_http_error(code: str) -> HTTPException:
    if code == "insufficient_sits":
        return HTTPException(
            status_code=409,
            detail={
                "code": "INSUFFICIENT_SITS",
                "message": "Недостаточно сит для участия. Вы можете бесплатно посмотреть",
            },
        )
    if code == "event_already_active":
        return HTTPException(
            status_code=409,
            detail={
                "code": "EVENT_ALREADY_ACTIVE",
                "message": "Групповая мастурбация уже идёт",
            },
        )
    if code == "no_active_event":
        return HTTPException(
            status_code=409,
            detail={
                "code": "NO_ACTIVE_EVENT",
                "message": "Сейчас нет активного сеанса",
            },
        )
    if code == "join_window_closed":
        return HTTPException(
            status_code=409,
            detail={
                "code": "JOIN_WINDOW_CLOSED",
                "message": "Окно регистрации закрыто",
            },
        )
    if code == "already_joined":
        return HTTPException(
            status_code=409,
            detail={
                "code": "ALREADY_JOINED",
                "message": "Вы уже участвуете в этом сеансе",
            },
        )
    if code == "reminder_exists":
        return HTTPException(
            status_code=409,
            detail={
                "code": "REMINDER_EXISTS",
                "message": "Напоминание уже включено",
            },
        )
    return HTTPException(
        status_code=500,
        detail={
            "code": "UNEXPECTED_ERROR",
            "message": "Не удалось выполнить действие",
        },
    )


@app.post("/api/group-event/start")
def group_event_start(request: Request, _: GroupEventActionRequest) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    profile = _get_user_profile(chat_id, user_id)
    display_name = str(profile["name"] or f"Игрок {user_id}")

    result = group_engine.start_event(
        chat_id=chat_id,
        user_id=user_id,
        display_name=display_name,
        thread_id=None,
        source="web",
    )
    if not result.ok:
        raise _group_event_http_error(result.code)

    _enqueue_group_text(chat_id=chat_id, text=f"С твоего счёта списано {EVENT_COST} сит за запуск ивента")
    if GROUP_EVENT_STICKER_FILE_ID:
        group_store.enqueue_outbox(
            chat_id=chat_id,
            kind="send_sticker",
            payload={"sticker": GROUP_EVENT_STICKER_FILE_ID, "thread_id": None},
        )
    _enqueue_group_start_flow(chat_id=chat_id)

    balance = _get_user_balance(user_id, chat_id)
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "balance": normalize_sits(balance),
            "group_event": _build_group_event_state(chat_id, user_id),
        }
    )


@app.post("/api/group-event/remind")
def group_event_remind(request: Request, _: GroupEventActionRequest) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    profile = _get_user_profile(chat_id, user_id)
    display_name = str(profile["name"] or f"Игрок {user_id}")

    result = group_engine.add_reminder(chat_id=chat_id, user_id=user_id, display_name=display_name)
    if not result.ok:
        raise _group_event_http_error(result.code)

    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "group_event": _build_group_event_state(chat_id, user_id),
        }
    )


@app.post("/api/group-event/join-participant")
def group_event_join_participant(request: Request, _: GroupEventActionRequest) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    profile = _get_user_profile(chat_id, user_id)
    display_name = str(profile["name"] or f"Игрок {user_id}")

    result = group_engine.join_as_participant(
        chat_id=chat_id,
        user_id=user_id,
        display_name=display_name,
        source="web",
        allow_freebie_on_insufficient=False,
    )
    if not result.ok:
        raise _group_event_http_error(result.code)

    phrase = random.choice(GROUP_JOIN_ANNOUNCE_MESSAGES).format(name=display_name)
    _enqueue_group_text(chat_id=chat_id, text=phrase, thread_id=result.thread_id)

    balance = _get_user_balance(user_id, chat_id)
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "balance": normalize_sits(balance),
            "group_event": _build_group_event_state(chat_id, user_id),
        }
    )


@app.post("/api/group-event/join-spectator")
def group_event_join_spectator(request: Request, _: GroupEventActionRequest) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    profile = _get_user_profile(chat_id, user_id)
    display_name = str(profile["name"] or f"Игрок {user_id}")

    result = group_engine.join_as_spectator(
        chat_id=chat_id,
        user_id=user_id,
        display_name=display_name,
        source="web",
    )
    if not result.ok:
        raise _group_event_http_error(result.code)

    _enqueue_group_text(chat_id=chat_id, text=f"👀 {display_name} просто посмотрит онлайн-трансляцию", thread_id=result.thread_id)

    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "group_event": _build_group_event_state(chat_id, user_id),
        }
    )


@app.post("/api/group-event/result/clear")
def group_event_clear_result(request: Request, _: GroupEventActionRequest) -> JSONResponse:
    user_id, chat_id = _require_selected_user_chat(request)
    group_store.clear_event_result(chat_id)
    return JSONResponse(
        {
            "ok": True,
            "chat_id": chat_id,
            "user_id": user_id,
            "group_event": _build_group_event_state(chat_id, user_id),
        }
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/ai/tasks/next")
def ai_task_next(request: Request) -> JSONResponse:
    _require_ai_worker(request)
    task = claim_next_task()
    return JSONResponse({"ok": True, "task": task})


@app.get("/api/ai/type-checks/next")
def ai_type_check_next(request: Request) -> JSONResponse:
    _require_ai_worker(request)
    task = claim_next_type_check()
    return JSONResponse({"ok": True, "task": task})


@app.get("/api/ai/search-plans/next")
def ai_search_plan_next(request: Request) -> JSONResponse:
    _require_ai_worker(request)
    task = claim_next_search_plan()
    return JSONResponse({"ok": True, "task": task})


@app.post("/api/ai/type-checks/{type_check_id}/result")
def ai_type_check_result(type_check_id: int, request: Request, data: AiTaskResultRequest) -> JSONResponse:
    _require_ai_worker(request)
    task = get_type_check(type_check_id)
    if not task:
        raise HTTPException(status_code=404, detail="Type-check task not found")
    if task["status"] != TASK_STATUS_PROCESSING:
        raise HTTPException(status_code=409, detail=f"Type-check task is not processing: {task['status']}")

    raw_output = (data.output or "").strip()
    worker_error = (data.error or "").strip()
    if worker_error:
        mark_type_check_failed(type_check_id, error_text=f"Worker/Ollama error: {worker_error}")
        return JSONResponse({"ok": True, "status": "failed", "task_id": type_check_id, "error": worker_error})

    try:
        result_type = validate_type_check_output(raw_output)
    except Exception as exc:
        logger.warning("AI type-check task %s failed during validation: %s", type_check_id, exc)
        mark_type_check_failed(type_check_id, error_text=str(exc))
        return JSONResponse({"ok": True, "status": "failed", "task_id": type_check_id, "error": str(exc)})

    chat_id = int(task["chat_id"])
    user_id = int(task["user_id"])
    request_message_id = int(task["request_message_id"])
    message_text = str(task["message_text"] or "")
    trigger_reason = str(task["trigger_reason"] or "type_check")
    final_task_id: int | None = None
    search_plan_id: int | None = None
    skipped_reason: str | None = None

    if result_type == TYPE_CHECK_RESULT_IGNORE:
        skipped_reason = "ignore"
    elif result_type == TYPE_CHECK_RESULT_WEB_SEARCH:
        search_plan_id = create_search_plan_task(
            chat_id=chat_id,
            user_id=user_id,
            request_message_id=request_message_id,
            message_text=message_text,
            trigger_reason=trigger_reason,
        )
        if search_plan_id is None:
            skipped_reason = "search plan already pending"
    else:
        requester_name, requester_nick = _get_ai_task_user_context(chat_id, user_id)
        try:
            if result_type == TYPE_CHECK_RESULT_RESPONSE:
                cooldown_left = get_response_cooldown_left(
                    chat_id,
                    cooldown_seconds=RESPONSE_DIRECT_COOLDOWN_SECONDS,
                )
                if cooldown_left > 0:
                    skipped_reason = "response cooldown"
                else:
                    final_task_id = create_response_task(
                        chat_id=chat_id,
                        requester_user_id=user_id,
                        request_message_id=request_message_id,
                        message_text=message_text,
                        requester_name=requester_name,
                        requester_nick=requester_nick,
                        trigger_reason=trigger_reason,
                    )
                    if final_task_id is None:
                        skipped_reason = "response task already pending"
            elif result_type == TYPE_CHECK_RESULT_TEXT_TO_SQL:
                cooldown_left = get_text_to_sql_cooldown(chat_id)
                if cooldown_left > 0:
                    skipped_reason = "text_to_sql cooldown"
                else:
                    final_task_id = create_text_to_sql_task(
                        chat_id=chat_id,
                        user_id=user_id,
                        request_message_id=request_message_id,
                        user_query=message_text,
                        requester_name=requester_name,
                        requester_nick=requester_nick,
                    )
            elif result_type == TYPE_CHECK_RESULT_DATA_ANALYSIS:
                final_task_id = create_data_analysis_task(
                    chat_id=chat_id,
                    user_id=user_id,
                    request_message_id=request_message_id,
                    user_query=message_text,
                    requester_name=requester_name,
                    requester_nick=requester_nick,
                )
                if final_task_id is None:
                    skipped_reason = "data analysis already pending"
        except Exception as exc:
            logger.exception("AI type-check task %s failed to create final task", type_check_id)
            mark_type_check_failed(type_check_id, error_text=str(exc))
            return JSONResponse({"ok": True, "status": "failed", "task_id": type_check_id, "error": str(exc)})

    mark_type_check_done(type_check_id, result_type=result_type)
    if final_task_id is not None:
        if result_type != TYPE_CHECK_RESULT_WEB_SEARCH:
            _set_in_progress_reaction_best_effort(
                chat_id,
                request_message_id,
                label="AI type-check task",
                task_id=type_check_id,
            )

    return JSONResponse(
        {
            "ok": True,
            "status": "done",
            "task_id": type_check_id,
            "result_type": result_type,
            "final_task_id": final_task_id,
            "search_plan_id": search_plan_id,
            "skipped_reason": skipped_reason,
        }
    )


@app.post("/api/ai/search-plans/{search_plan_id}/result")
def ai_search_plan_result(search_plan_id: int, request: Request, data: AiTaskResultRequest) -> JSONResponse:
    _require_ai_worker(request)
    task = get_search_plan(search_plan_id)
    if not task:
        raise HTTPException(status_code=404, detail="Search-plan task not found")
    if task["status"] != TASK_STATUS_PROCESSING:
        raise HTTPException(status_code=409, detail=f"Search-plan task is not processing: {task['status']}")

    raw_output = (data.output or "").strip()
    worker_error = (data.error or "").strip()
    chat_id = int(task["chat_id"])
    user_id = int(task["user_id"])
    request_message_id = int(task["request_message_id"])
    message_text = str(task["message_text"] or "")
    trigger_reason = str(task["trigger_reason"] or "web_search")

    def fallback_response(reason: str) -> JSONResponse:
        web_context = (
            "Актуальный веб-контекст получить не удалось. "
            f"Причина: {reason}. Ответь осторожно и не выдавай непроверенные актуальные факты за точные."
        )
        final_task_id = _create_response_from_ai_context(
            chat_id=chat_id,
            user_id=user_id,
            request_message_id=request_message_id,
            message_text=message_text,
            trigger_reason="web_search_fallback",
            web_context=web_context,
        )
        if final_task_id is not None:
            _set_in_progress_reaction_best_effort(
                chat_id,
                request_message_id,
                label="AI search-plan task",
                task_id=search_plan_id,
            )
        return JSONResponse(
            {
                "ok": True,
                "status": "fallback",
                "task_id": search_plan_id,
                "final_task_id": final_task_id,
                "error": reason,
            }
        )

    if worker_error:
        failure_reason = f"Worker/Ollama error: {worker_error}"
        requeued, updated_task = requeue_or_fail_search_plan(
            search_plan_id,
            previous_response=raw_output or None,
            error_text=failure_reason,
        )
        if requeued:
            return JSONResponse(
                {
                    "ok": True,
                    "status": "retry",
                    "task_id": search_plan_id,
                    "attempt": int(updated_task["attempt"] or 0) if updated_task else None,
                }
            )
        return fallback_response(failure_reason)

    try:
        search_plan = validate_search_plan_output(raw_output)
    except Exception as exc:
        logger.warning("AI search-plan task %s failed during validation: %s", search_plan_id, exc)
        failure_reason = str(exc)
        requeued, updated_task = requeue_or_fail_search_plan(
            search_plan_id,
            previous_response=raw_output or None,
            error_text=failure_reason,
        )
        if requeued:
            return JSONResponse(
                {
                    "ok": True,
                    "status": "retry",
                    "task_id": search_plan_id,
                    "attempt": int(updated_task["attempt"] or 0) if updated_task else None,
                }
            )
        return fallback_response(failure_reason)

    mark_search_plan_done(search_plan_id, result=search_plan)
    try:
        web_context = build_web_context(question=message_text, search_plan=search_plan)
    except WebSearchError as exc:
        logger.warning("AI search-plan task %s failed during SearXNG search: %s", search_plan_id, exc)
        return fallback_response(str(exc))

    final_task_id = _create_response_from_ai_context(
        chat_id=chat_id,
        user_id=user_id,
        request_message_id=request_message_id,
        message_text=message_text,
        trigger_reason="web_search",
        web_context=web_context,
    )
    if final_task_id is not None:
        _set_in_progress_reaction_best_effort(
            chat_id,
            request_message_id,
            label="AI search-plan task",
            task_id=search_plan_id,
        )
    return JSONResponse(
        {
            "ok": True,
            "status": "done",
            "task_id": search_plan_id,
            "final_task_id": final_task_id,
        }
    )


def _format_data_analysis_message(answer_text: str, preview_text: str | None) -> str:
    answer_html = escape(answer_text.strip())
    preview = (preview_text or "").strip()
    if not preview:
        return answer_html

    def build(preview_body: str) -> str:
        return f"{answer_html}\n\n<b>Данные:</b>\n<pre>{escape(preview_body)}</pre>"

    message = build(preview)
    if len(message) <= 3900:
        return message

    max_preview_len = max(200, 3600 - len(answer_html))
    short_preview = preview[:max_preview_len].rstrip() + "\n... данные обрезаны для сообщения"
    message = build(short_preview)
    if len(message) <= 3900:
        return message

    short_answer = answer_text.strip()[:2600].rstrip() + "\n... анализ обрезан для сообщения"
    answer_html = escape(short_answer)
    max_preview_len = max(200, 3600 - len(answer_html))
    short_preview = preview[:max_preview_len].rstrip() + "\n... данные обрезаны для сообщения"
    return f"{answer_html}\n\n<b>Данные:</b>\n<pre>{escape(short_preview)}</pre>"


@app.post("/api/ai/tasks/{task_id}/result")
def ai_task_result(task_id: int, request: Request, data: AiTaskResultRequest) -> JSONResponse:
    _require_ai_worker(request)
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] != TASK_STATUS_PROCESSING:
        raise HTTPException(status_code=409, detail=f"Task is not processing: {task['status']}")

    raw_output = (data.output or "").strip()
    worker_error = (data.error or "").strip()
    sql_for_retry: str | None = raw_output or None
    failure_reason: str | None = None

    if task["task_type"] == TASK_TYPE_RESPONSE:
        if worker_error:
            failure_reason = f"Worker/Ollama error: {worker_error}"
        else:
            try:
                response_text = validate_response_output(raw_output)
            except Exception as exc:
                logger.warning("AI response task %s failed during validation: %s", task_id, exc)
                failure_reason = str(exc)
            else:
                try:
                    response_message_id = _send_telegram_message(
                        int(task["chat_id"]),
                        escape(response_text),
                        reply_to_message_id=int(task["request_message_id"]),
                    )
                except Exception as exc:
                    logger.exception("AI response task %s failed to send Telegram response", task_id)
                    failure_reason = str(exc)
                else:
                    mark_response_task_done(
                        task_id,
                        response_text=response_text,
                        response_message_id=response_message_id,
                    )
                    try:
                        _set_telegram_reaction(
                            int(task["chat_id"]),
                            int(task["request_message_id"]),
                            RESPONSE_REACTION_DONE,
                        )
                    except Exception as exc:
                        logger.warning("AI response task %s failed to set done reaction: %s", task_id, exc)
                    return JSONResponse(
                        {
                            "ok": True,
                            "status": "done",
                            "task_id": task_id,
                            "response_message_id": response_message_id,
                        }
                    )

        assert failure_reason is not None
        requeued, updated_task = requeue_or_fail_response_task(
            task_id,
            previous_response=raw_output or None,
            error_text=failure_reason,
        )
        if requeued:
            return JSONResponse(
                {
                    "ok": True,
                    "status": "retry",
                    "task_id": task_id,
                    "attempt": int(updated_task["attempt"] or 0) if updated_task else None,
                }
            )
        try:
            _set_telegram_reaction(
                int(task["chat_id"]),
                int(task["request_message_id"]),
                RESPONSE_REACTION_ERROR,
            )
        except Exception as exc:
            logger.warning("AI response task %s failed to set error reaction: %s", task_id, exc)
        return JSONResponse(
            {
                "ok": True,
                "status": "failed",
                "task_id": task_id,
                "error": failure_reason,
            }
        )

    if task["task_type"] == TASK_TYPE_DATA_ANALYSIS_SQL:
        if worker_error:
            failure_reason = f"Worker/Ollama error: {worker_error}"
        else:
            try:
                sql = validate_text_to_sql(raw_output, chat_id=int(task["chat_id"]))
                sql_for_retry = sql
                columns, rows, truncated = execute_readonly_sql(
                    sql,
                    max_rows=DATA_ANALYSIS_RESULT_ROW_LIMIT,
                )
                analysis = get_data_analysis_by_task(task)
                if not analysis:
                    raise RuntimeError("Data analysis workflow not found")
                preview_text = format_data_analysis_preview(columns, rows, truncated=truncated)
                mark_data_analysis_sql_done(
                    int(analysis["id"]),
                    sql=sql,
                    columns=columns,
                    rows=rows,
                    truncated=truncated,
                    preview_text=preview_text,
                )
                response_task_id = create_data_analysis_response_task(int(analysis["id"]))
                if response_task_id is None:
                    raise RuntimeError("Failed to create analysis response task")
                mark_task_done(task_id, sql=sql, response_message_id=None)
            except Exception as exc:
                logger.warning("AI data-analysis SQL task %s failed: %s", task_id, exc)
                failure_reason = str(exc)
            else:
                return JSONResponse(
                    {
                        "ok": True,
                        "status": "done",
                        "task_id": task_id,
                        "response_task_id": response_task_id,
                    }
                )

        assert failure_reason is not None
        requeued, updated_task = requeue_or_fail_data_analysis_sql_task(
            task_id,
            previous_sql=sql_for_retry,
            error_text=failure_reason,
        )
        if requeued:
            return JSONResponse(
                {
                    "ok": True,
                    "status": "retry",
                    "task_id": task_id,
                    "attempt": int(updated_task["attempt"] or 0) if updated_task else None,
                }
            )
        error_message = escape(failure_reason[:1200])
        try:
            _set_telegram_reaction(
                int(task["chat_id"]),
                int(task["request_message_id"]),
                RESPONSE_REACTION_ERROR,
            )
        except Exception as exc:
            logger.warning("AI data-analysis SQL task %s failed to set error reaction: %s", task_id, exc)
        try:
            _send_telegram_message(
                int(task["chat_id"]),
                f"Не удалось подготовить данные для анализа после повторной попытки.\n<pre>{error_message}</pre>",
                reply_to_message_id=int(task["request_message_id"]),
            )
        except Exception:
            logger.exception("AI data-analysis SQL task %s failed to send final error message", task_id)
        return JSONResponse(
            {
                "ok": True,
                "status": "failed",
                "task_id": task_id,
                "error": failure_reason,
            }
        )

    if task["task_type"] == TASK_TYPE_DATA_ANALYSIS_RESPONSE:
        analysis = get_data_analysis_by_task(task)
        if worker_error:
            failure_reason = f"Worker/Ollama error: {worker_error}"
        else:
            try:
                response_text = validate_response_output(raw_output)
                if not analysis:
                    raise RuntimeError("Data analysis workflow not found")
                message_text = _format_data_analysis_message(response_text, str(analysis["preview_text"] or ""))
            except Exception as exc:
                logger.warning("AI data-analysis response task %s failed during validation: %s", task_id, exc)
                failure_reason = str(exc)
            else:
                try:
                    response_message_id = _send_telegram_message(
                        int(task["chat_id"]),
                        message_text,
                        reply_to_message_id=int(task["request_message_id"]),
                    )
                except Exception as exc:
                    logger.exception("AI data-analysis response task %s failed to send Telegram response", task_id)
                    failure_reason = str(exc)
                else:
                    mark_response_task_done(
                        task_id,
                        response_text=response_text,
                        response_message_id=response_message_id,
                    )
                    mark_data_analysis_done(
                        int(analysis["id"]),
                        final_answer=response_text,
                        response_message_id=response_message_id,
                    )
                    try:
                        _set_telegram_reaction(
                            int(task["chat_id"]),
                            int(task["request_message_id"]),
                            RESPONSE_REACTION_DONE,
                        )
                    except Exception as exc:
                        logger.warning("AI data-analysis response task %s failed to set done reaction: %s", task_id, exc)
                    return JSONResponse(
                        {
                            "ok": True,
                            "status": "done",
                            "task_id": task_id,
                            "response_message_id": response_message_id,
                        }
                    )

        assert failure_reason is not None
        requeued, updated_task = requeue_or_fail_data_analysis_response_task(
            task_id,
            previous_response=raw_output or None,
            error_text=failure_reason,
        )
        if requeued:
            return JSONResponse(
                {
                    "ok": True,
                    "status": "retry",
                    "task_id": task_id,
                    "attempt": int(updated_task["attempt"] or 0) if updated_task else None,
                }
            )
        try:
            _set_telegram_reaction(
                int(task["chat_id"]),
                int(task["request_message_id"]),
                RESPONSE_REACTION_ERROR,
            )
        except Exception as exc:
            logger.warning("AI data-analysis response task %s failed to set error reaction: %s", task_id, exc)
        try:
            _send_telegram_message(
                int(task["chat_id"]),
                f"Не удалось подготовить аналитический ответ.\n<pre>{escape(failure_reason[:1200])}</pre>",
                reply_to_message_id=int(task["request_message_id"]),
            )
        except Exception:
            logger.exception("AI data-analysis response task %s failed to send final error message", task_id)
        return JSONResponse(
            {
                "ok": True,
                "status": "failed",
                "task_id": task_id,
                "error": failure_reason,
            }
        )

    if task["task_type"] == TASK_TYPE_CHAT_SUMMARY:
        if worker_error:
            failure_reason = f"Worker/Ollama error: {worker_error}"
        else:
            try:
                summary_text = validate_chat_summary_output(raw_output)
            except Exception as exc:
                logger.warning("AI chat summary task %s failed during validation: %s", task_id, exc)
                failure_reason = str(exc)
            else:
                mark_chat_summary_task_done(task_id, summary_text=summary_text)
                return JSONResponse(
                    {
                        "ok": True,
                        "status": "done",
                        "task_id": task_id,
                    }
                )

        assert failure_reason is not None
        requeued, updated_task = requeue_or_fail_chat_summary_task(
            task_id,
            previous_response=raw_output or None,
            error_text=failure_reason,
        )
        if requeued:
            return JSONResponse(
                {
                    "ok": True,
                    "status": "retry",
                    "task_id": task_id,
                    "attempt": int(updated_task["attempt"] or 0) if updated_task else None,
                }
            )
        return JSONResponse(
            {
                "ok": True,
                "status": "failed",
                "task_id": task_id,
                "error": failure_reason,
            }
        )

    if task["task_type"] == TASK_TYPE_PROFILE_UPDATE:
        if worker_error:
            failure_reason = f"Worker/Ollama error: {worker_error}"
        else:
            try:
                profile = validate_profile_update_output(raw_output)
            except Exception as exc:
                logger.warning("AI profile task %s failed during JSON validation: %s", task_id, exc)
                failure_reason = str(exc)
            else:
                mark_profile_task_done(task_id, profile=profile, raw_output=raw_output)
                return JSONResponse(
                    {
                        "ok": True,
                        "status": "done",
                        "task_id": task_id,
                    }
                )

        assert failure_reason is not None
        requeued, updated_task = requeue_or_fail_profile_task(
            task_id,
            previous_response=raw_output or None,
            error_text=failure_reason,
        )
        if requeued:
            return JSONResponse(
                {
                    "ok": True,
                    "status": "retry",
                    "task_id": task_id,
                    "attempt": int(updated_task["attempt"] or 0) if updated_task else None,
                }
            )
        return JSONResponse(
            {
                "ok": True,
                "status": "failed",
                "task_id": task_id,
                "error": failure_reason,
            }
        )

    if task["task_type"] != TASK_TYPE_TEXT_TO_SQL:
        raise HTTPException(status_code=400, detail=f"Unsupported task type: {task['task_type']}")

    if worker_error:
        failure_reason = f"Worker/Ollama error: {worker_error}"
    else:
        try:
            sql = validate_text_to_sql(raw_output, chat_id=int(task["chat_id"]))
            sql_for_retry = sql
            columns, rows, truncated = execute_readonly_sql(sql)
            message_text = format_sql_result_for_telegram(columns, rows, truncated=truncated)
        except Exception as exc:
            logger.warning("AI task %s failed during SQL validation/execution: %s", task_id, exc)
            failure_reason = str(exc)
        else:
            try:
                response_message_id = _send_telegram_message(
                    int(task["chat_id"]),
                    message_text,
                    reply_to_message_id=int(task["request_message_id"]),
                )
            except Exception as exc:
                logger.exception("AI task %s failed to send Telegram response", task_id)
                failure_reason = str(exc)
            else:
                mark_task_done(task_id, sql=sql, response_message_id=response_message_id)
                try:
                    _set_telegram_reaction(
                        int(task["chat_id"]),
                        int(task["request_message_id"]),
                        RESPONSE_REACTION_DONE,
                    )
                except Exception as exc:
                    logger.warning("AI task %s failed to set done reaction: %s", task_id, exc)
                return JSONResponse(
                    {
                        "ok": True,
                        "status": "done",
                        "task_id": task_id,
                        "response_message_id": response_message_id,
                    }
                )

    assert failure_reason is not None
    requeued, updated_task = requeue_or_fail_task(
        task_id,
        previous_sql=sql_for_retry,
        error_text=failure_reason,
    )
    if requeued:
        return JSONResponse(
            {
                "ok": True,
                "status": "retry",
                "task_id": task_id,
                "attempt": int(updated_task["attempt"] or 0) if updated_task else None,
            }
        )

    error_message = escape(failure_reason[:1200])
    try:
        _set_telegram_reaction(
            int(task["chat_id"]),
            int(task["request_message_id"]),
            RESPONSE_REACTION_ERROR,
        )
    except Exception as exc:
        logger.warning("AI task %s failed to set error reaction: %s", task_id, exc)
    try:
        _send_telegram_message(
            int(task["chat_id"]),
            f"Не удалось выполнить запрос к базе после повторной попытки.\n<pre>{error_message}</pre>",
            reply_to_message_id=int(task["request_message_id"]),
        )
    except Exception:
        logger.exception("AI task %s failed to send final error message", task_id)

    return JSONResponse(
        {
            "ok": True,
            "status": "failed",
            "task_id": task_id,
            "error": failure_reason,
        }
    )
