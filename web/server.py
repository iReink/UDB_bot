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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from auth_code import (
    AuthCodeConflictError,
    AuthCodeExpiredError,
    AuthCodeInvalidError,
    AuthCodeUsedError,
    consume_auth_code,
)
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sits import normalize_sits


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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip()
SESSION_SECRET = os.getenv("WEB_SESSION_SECRET", "").strip()
logger = logging.getLogger(__name__)
idle_income_task: asyncio.Task[None] | None = None
idle_catalog_ready = False
idle_catalog_lock = threading.Lock()

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
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, chat_id)
            )
            """
        )
        cur.execute(
            """
            INSERT OR IGNORE INTO web_settings (user_id, chat_id, hide_base, reject_geyser_catch_by_guest)
            SELECT u.user_id, u.chat_id, 0, 0
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
                COALESCE(reject_geyser_catch_by_guest, 0) AS reject_geyser_catch_by_guest
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
        }
    return {
        "hide_base": bool(int(row["hide_base"] or 0)),
        "reject_geyser_catch_by_guest": bool(int(row["reject_geyser_catch_by_guest"] or 0)),
    }


def _update_web_settings(
    user_id: int,
    chat_id: int,
    hide_base: bool | None = None,
    reject_geyser_catch_by_guest: bool | None = None,
) -> dict[str, bool]:
    _ensure_web_settings_table()
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            SELECT
                COALESCE(hide_base, 0) AS hide_base,
                COALESCE(reject_geyser_catch_by_guest, 0) AS reject_geyser_catch_by_guest
            FROM web_settings
            WHERE user_id = ? AND chat_id = ?
            """,
            (user_id, chat_id),
        )
        row = cur.fetchone()
        current_hide = bool(int(row["hide_base"] or 0)) if row else False
        current_reject = bool(int(row["reject_geyser_catch_by_guest"] or 0)) if row else False
        next_hide = current_hide if hide_base is None else bool(hide_base)
        next_reject = current_reject if reject_geyser_catch_by_guest is None else bool(reject_geyser_catch_by_guest)
        cur.execute(
            """
            INSERT INTO web_settings (
                user_id,
                chat_id,
                hide_base,
                reject_geyser_catch_by_guest,
                updated_at
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                hide_base = excluded.hide_base,
                reject_geyser_catch_by_guest = excluded.reject_geyser_catch_by_guest,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, chat_id, int(next_hide), int(next_reject)),
        )
        conn.commit()
    return {
        "hide_base": next_hide,
        "reject_geyser_catch_by_guest": next_reject,
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
        if sender_balance + 1e-9 < amount:
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "INSUFFICIENT_FUNDS",
                    "message": "Недостаточно сита",
                    "balance": normalize_sits(sender_balance),
                    "requested": normalize_sits(amount),
                },
            )

        receiver_balance = float(receiver_row["sits"] or 0)
        new_sender_balance = normalize_sits(sender_balance - amount)
        new_receiver_balance = normalize_sits(receiver_balance + amount)

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
                (date_value, time_value, chat_id, user_id, sender_name, -amount),
            )
            cur.execute(
                """
                INSERT INTO sit_stats (date, time, chat_id, user_id, name, amount)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (date_value, time_value, chat_id, receiver_user_id, receiver_name, amount),
            )

        conn.commit()

    return {
        "chat_id": chat_id,
        "sender_user_id": user_id,
        "receiver_user_id": receiver_user_id,
        "transferred": normalize_sits(amount),
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
    _ensure_idle_catalog_ready(force=True)
    _ensure_idle_service_tables()
    _ensure_geyser_tables()
    _ensure_web_settings_table()
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


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
