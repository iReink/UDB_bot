import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aiogram import Bot

from db import get_connection


BASE_DIR = Path(__file__).resolve().parent
EXCHANGE_DIR = BASE_DIR / "ai_exchange"
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DAILY_EXPORT_HOUR = 23
DAILY_EXPORT_MINUTE = 20
DAILY_PUBLISH_HOUR = 23
DAILY_PUBLISH_MINUTE = 55
_EXPORT_LOCK = threading.Lock()


@dataclass(frozen=True)
class Window:
    date_key: str
    start: datetime
    end: datetime


def _sheets_enabled() -> bool:
    return (os.getenv("UDB_SHEETS_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"})


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logging.warning("[chat_summary] invalid %s=%r, using %s", name, raw, default)
        return default
    return value if value > 0 else default


def _get_sheets_config() -> dict[str, str]:
    spreadsheet_id = (os.getenv("UDB_SHEETS_ID") or "").strip()
    service_account_file = (os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()
    log_sheet = (os.getenv("UDB_SHEETS_LOG_SHEET") or "log").strip()
    summary_sheet = (os.getenv("UDB_SHEETS_SUMMARY_SHEET") or "summary").strip()
    return {
        "spreadsheet_id": spreadsheet_id,
        "service_account_file": service_account_file,
        "log_sheet": log_sheet,
        "summary_sheet": summary_sheet,
    }


def _get_sheets_service():
    cfg = _get_sheets_config()
    if not cfg["spreadsheet_id"]:
        raise RuntimeError("UDB_SHEETS_ID is not configured")
    if not cfg["service_account_file"]:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_FILE is not configured")
    if not Path(cfg["service_account_file"]).exists():
        raise RuntimeError(f"Service account file not found: {cfg['service_account_file']}")

    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except Exception as exc:
        raise RuntimeError("google-api-python-client/google-auth are not installed") from exc

    credentials = Credentials.from_service_account_file(
        cfg["service_account_file"],
        scopes=SHEETS_SCOPES,
    )
    http_timeout = _env_float("UDB_SHEETS_HTTP_TIMEOUT_SECONDS", 30.0)
    try:
        from google_auth_httplib2 import AuthorizedHttp
        import httplib2
    except Exception:
        logging.warning(
            "[chat_summary] google-auth-httplib2/httplib2 is not installed; "
            "Google Sheets per-request HTTP timeout is disabled"
        )
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)

    http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=http_timeout))
    return build("sheets", "v4", http=http, cache_discovery=False)


def _execute_google_request(request: Any, operation: str) -> Any:
    attempts = max(1, int(_env_float("UDB_SHEETS_RETRY_ATTEMPTS", 3.0)))
    delay = _env_float("UDB_SHEETS_RETRY_DELAY_SECONDS", 2.0)
    for attempt in range(1, attempts + 1):
        try:
            return request.execute()
        except Exception:
            if attempt >= attempts:
                raise
            logging.warning(
                "[chat_summary] Google Sheets %s failed on attempt %d/%d; retrying",
                operation,
                attempt,
                attempts,
                exc_info=True,
            )
            time.sleep(delay * attempt)


def _window_for_daily_export(now: datetime | None = None) -> Window:
    current = now or datetime.now()
    day_start = current.replace(hour=DAILY_EXPORT_HOUR, minute=DAILY_EXPORT_MINUTE, second=0, microsecond=0)
    start = day_start - timedelta(days=1)
    end = day_start
    return Window(
        date_key=end.strftime("%Y_%m_%d"),
        start=start,
        end=end,
    )


def _safe_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _should_skip_message(text: str) -> bool:
    if not text or not text.strip():
        return True
    if text.strip().startswith("/"):
        return True
    return False


def _author_from_row(row: sqlite3.Row) -> str:
    nick = (row["nick"] or "").strip() if "nick" in row.keys() else ""
    if nick:
        return nick
    name = (row["name"] or "").strip() if "name" in row.keys() else ""
    if name:
        return name
    return f"User {int(row['user_id'])}"


def _users_has_nick_column() -> bool:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(users)")
        cols = {str(r[1]) for r in cur.fetchall()}
        return "nick" in cols


def _ensure_summary_publish_table() -> None:
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS summary_publish_log (
                chat_id INTEGER NOT NULL,
                date_key TEXT NOT NULL,
                published_at TEXT NOT NULL,
                summary_file TEXT NOT NULL,
                message_id INTEGER,
                PRIMARY KEY (chat_id, date_key)
            )
            """
        )
        conn.commit()


def _is_already_published(chat_id: int, date_key: str) -> bool:
    _ensure_summary_publish_table()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM summary_publish_log WHERE chat_id=? AND date_key=? LIMIT 1",
            (chat_id, date_key),
        )
        return cur.fetchone() is not None


def _mark_published(chat_id: int, date_key: str, summary_file: str, message_id: int | None) -> None:
    _ensure_summary_publish_table()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO summary_publish_log
            (chat_id, date_key, published_at, summary_file, message_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, date_key, datetime.now().isoformat(), summary_file, message_id),
        )
        conn.commit()


def _rewrite_log_sheet(date_key: str, window: Window, by_chat: dict[int, list[dict[str, str]]]) -> None:
    cfg = _get_sheets_config()
    service = _get_sheets_service()
    sheet_name = cfg["log_sheet"]
    spreadsheet_id = cfg["spreadsheet_id"]

    rows: list[list[str]] = [
        ["date_key", "chat_id", "author", "text", "message_datetime", "window_start", "window_end"]
    ]
    for chat_id in sorted(by_chat.keys()):
        for item in by_chat[chat_id]:
            rows.append(
                [
                    date_key,
                    str(chat_id),
                    str(item["author"]),
                    str(item["text"]),
                    str(item["date"]),
                    window.start.isoformat(),
                    window.end.isoformat(),
                ]
            )

    # Keep only current day in log sheet: clear and write full body.
    _execute_google_request(
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A:Z",
            body={},
        ),
        "clear log sheet",
    )
    _execute_google_request(
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="RAW",
            body={"values": rows},
        ),
        "update log sheet",
    )
    logging.info("[chat_summary] Google Sheet '%s' rewritten with %d message rows", sheet_name, max(0, len(rows) - 1))


def _read_summary_from_sheet(date_key: str) -> dict[int, list[str]]:
    cfg = _get_sheets_config()
    service = _get_sheets_service()
    sheet_name = cfg["summary_sheet"]
    spreadsheet_id = cfg["spreadsheet_id"]

    resp = _execute_google_request(
        service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A:D",
        ),
        "read summary sheet",
    )
    values = resp.get("values", [])
    if not values:
        return {}

    grouped: dict[int, list[tuple[int, str]]] = {}
    # Expected columns: date_key, chat_id, bullet_order, bullet_text
    for row in values[1:]:
        if len(row) < 4:
            continue
        row_date = str(row[0]).strip()
        if row_date != date_key:
            continue
        try:
            chat_id = int(str(row[1]).strip())
        except Exception:
            continue
        try:
            order = int(str(row[2]).strip() or "0")
        except Exception:
            order = 0
        bullet = str(row[3]).strip()
        if not bullet:
            continue
        grouped.setdefault(chat_id, []).append((order, bullet))

    result: dict[int, list[str]] = {}
    for chat_id, items in grouped.items():
        items.sort(key=lambda x: x[0])
        result[chat_id] = [text for _, text in items]
    return result


def export_daily_chatlogs() -> list[Path]:
    window = _window_for_daily_export()
    EXCHANGE_DIR.mkdir(parents=True, exist_ok=True)
    exported: list[Path] = []

    has_nick = _users_has_nick_column()

    with closing(get_connection()) as conn:
        cur = conn.cursor()
        if has_nick:
            cur.execute(
                """
                SELECT
                    mr.chat_id,
                    mr.user_id,
                    mr.message_text,
                    mr.date,
                    u.name,
                    u.nick
                FROM messages_reactions mr
                LEFT JOIN users u
                    ON u.chat_id = mr.chat_id
                   AND u.user_id = mr.user_id
                WHERE mr.date IS NOT NULL
                  AND mr.date >= ?
                  AND mr.date < ?
                ORDER BY mr.chat_id ASC, mr.date ASC, mr.message_id ASC
                """,
                (window.start.isoformat(), window.end.isoformat()),
            )
        else:
            cur.execute(
                """
                SELECT
                    mr.chat_id,
                    mr.user_id,
                    mr.message_text,
                    mr.date,
                    u.name,
                    '' AS nick
                FROM messages_reactions mr
                LEFT JOIN users u
                    ON u.chat_id = mr.chat_id
                   AND u.user_id = mr.user_id
                WHERE mr.date IS NOT NULL
                  AND mr.date >= ?
                  AND mr.date < ?
                ORDER BY mr.chat_id ASC, mr.date ASC, mr.message_id ASC
                """,
                (window.start.isoformat(), window.end.isoformat()),
            )
        rows = cur.fetchall()

    by_chat: dict[int, list[dict[str, str]]] = {}
    for row in rows:
        text = str(row["message_text"] or "")
        if _should_skip_message(text):
            continue
        dt = _safe_dt(str(row["date"] or ""))
        if not dt:
            continue
        if not (window.start <= dt < window.end):
            continue
        chat_id = int(row["chat_id"])
        by_chat.setdefault(chat_id, []).append(
            {
                "author": _author_from_row(row),
                "text": text,
                "date": dt.isoformat(),
            }
        )

    for chat_id, messages in by_chat.items():
        if not messages:
            continue
        payload = {
            "chat_id": chat_id,
            "window_start": window.start.isoformat(),
            "window_end": window.end.isoformat(),
            "messages": messages,
        }
        output_path = EXCHANGE_DIR / f"{window.date_key}_{chat_id}_chatlog.json"
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        exported.append(output_path)

    if not exported:
        logging.info("[chat_summary] No messages exported for %s", window.date_key)

    if _sheets_enabled():
        try:
            _rewrite_log_sheet(window.date_key, window, by_chat)
        except Exception:
            logging.exception("[chat_summary] failed to rewrite Google Sheet log")
    return exported


async def export_daily_chatlogs_task(bot: Bot) -> None:
    import asyncio

    _ = bot
    while True:
        now = datetime.now()
        target = now.replace(hour=DAILY_EXPORT_HOUR, minute=DAILY_EXPORT_MINUTE, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await _sleep_until(target)
        try:
            timeout = _env_float("UDB_CHATLOG_EXPORT_TIMEOUT_SECONDS", 300.0)
            exported = await asyncio.wait_for(asyncio.to_thread(_export_daily_chatlogs_locked), timeout=timeout)
            logging.info("[chat_summary] Exported %d chatlog files", len(exported))
        except asyncio.TimeoutError:
            logging.error(
                "[chat_summary] export_daily_chatlogs timed out after %.1fs; bot event loop remains alive",
                _env_float("UDB_CHATLOG_EXPORT_TIMEOUT_SECONDS", 300.0),
            )
        except Exception:
            logging.exception("[chat_summary] export_daily_chatlogs failed")


async def publish_daily_summary_task(bot: Bot) -> None:
    while True:
        now = datetime.now()
        target = now.replace(hour=DAILY_PUBLISH_HOUR, minute=DAILY_PUBLISH_MINUTE, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await _sleep_until(target)
        try:
            await publish_daily_summaries(bot)
        except Exception:
            logging.exception("[chat_summary] publish_daily_summaries failed")


def _export_daily_chatlogs_locked() -> list[Path]:
    if not _EXPORT_LOCK.acquire(blocking=False):
        logging.warning("[chat_summary] previous export is still running; skip this scheduled run")
        return []
    try:
        return export_daily_chatlogs()
    finally:
        _EXPORT_LOCK.release()


async def _sleep_until(target: datetime) -> None:
    import asyncio

    wait_seconds = max(0.0, (target - datetime.now()).total_seconds())
    await asyncio.sleep(wait_seconds)


def _load_summary(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("[chat_summary] cannot parse summary file: %s", path)
        return None

    if not isinstance(data, dict):
        return None
    bullets = data.get("bullets")
    if not isinstance(bullets, list) or not bullets:
        return None
    filtered = [str(item).strip() for item in bullets if str(item).strip()]
    if not filtered:
        return None
    data["bullets"] = filtered
    return data


def _format_summary_message(date_key: str, bullets: list[str]) -> str:
    lines = [f"Итоги дня {date_key.replace('_', '.')}:", ""]
    lines.extend([f"{idx + 1}. {item}" for idx, item in enumerate(bullets)])
    return "\n".join(lines)


async def publish_daily_summaries(bot: Bot) -> int:
    import asyncio

    date_key = datetime.now().strftime("%Y_%m_%d")
    published = 0
    if _sheets_enabled():
        try:
            timeout = _env_float("UDB_SUMMARY_READ_TIMEOUT_SECONDS", 120.0)
            summaries = await asyncio.wait_for(
                asyncio.to_thread(_read_summary_from_sheet, date_key),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logging.error(
                "[chat_summary] read summary sheet timed out after %.1fs; skip publish for now",
                _env_float("UDB_SUMMARY_READ_TIMEOUT_SECONDS", 120.0),
            )
            summaries = {}
        except Exception:
            logging.exception("[chat_summary] failed to read summary sheet")
            summaries = {}

        for chat_id, bullets in summaries.items():
            if _is_already_published(chat_id, date_key):
                continue
            text = _format_summary_message(date_key, bullets)
            try:
                sent = await bot.send_message(chat_id, text)
            except Exception:
                logging.exception("[chat_summary] failed to send summary to chat %s", chat_id)
                continue
            _mark_published(chat_id, date_key, "google_sheets", sent.message_id)
            published += 1
    else:
        if not EXCHANGE_DIR.exists():
            logging.info("[chat_summary] ai_exchange folder is missing")
            return 0

        files = sorted(EXCHANGE_DIR.glob(f"{date_key}_*_summary.json"))
        for path in files:
            data = _load_summary(path)
            if not data:
                logging.warning("[chat_summary] skip invalid summary file: %s", path.name)
                continue
            try:
                chat_id = int(data.get("chat_id"))
            except Exception:
                logging.warning("[chat_summary] bad chat_id in %s", path.name)
                continue
            if _is_already_published(chat_id, date_key):
                continue

            text = _format_summary_message(date_key, list(data["bullets"]))
            try:
                sent = await bot.send_message(chat_id, text)
            except Exception:
                logging.exception("[chat_summary] failed to send summary to chat %s", chat_id)
                continue

            _mark_published(chat_id, date_key, path.name, sent.message_id)
            published += 1

    logging.info("[chat_summary] published %d summaries for %s", published, date_key)
    return published
