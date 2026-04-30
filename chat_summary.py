import json
import logging
import os
import re
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aiogram import Bot

from db import get_connection


BASE_DIR = Path(__file__).resolve().parent
EXCHANGE_DIR = BASE_DIR / "ai_exchange"


@dataclass(frozen=True)
class Window:
    date_key: str
    start: datetime
    end: datetime


def _window_for_daily_export(now: datetime | None = None) -> Window:
    current = now or datetime.now()
    day_start = current.replace(hour=23, minute=30, second=0, microsecond=0)
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


def _git_commit_and_push_for_exchange(date_key: str) -> None:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            env=env,
            capture_output=True,
            text=True,
            check=check,
        )

    def current_branch() -> str:
        branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], check=False).stdout.strip()
        return branch or "main"

    def push_with_optional_token() -> None:
        token = (os.getenv("UDB_GIT_PUSH_TOKEN") or os.getenv("GITHUB_TOKEN") or "").strip()
        username = (os.getenv("UDB_GIT_PUSH_USERNAME") or "x-access-token").strip()
        if not token:
            run(["git", "push"])
            return

        origin = run(["git", "remote", "get-url", "origin"], check=False).stdout.strip()
        match = re.match(r"^https://([^/]+)/(.+)$", origin)
        if not match:
            # SSH or unknown URL format: fallback to standard push
            run(["git", "push"])
            return

        host, path = match.group(1), match.group(2)
        auth_url = f"https://{username}:{token}@{host}/{path}"
        branch = current_branch()
        # Push current HEAD to current branch over authenticated URL.
        run(["git", "push", auth_url, f"HEAD:{branch}"])

    try:
        run(["git", "add", "ai_exchange/"])
        status = run(["git", "status", "--porcelain", "ai_exchange/"], check=False)
        if not status.stdout.strip():
            logging.info("[chat_summary] No ai_exchange changes to commit")
            return

        run(["git", "commit", "-m", f"chatlog: {date_key}"])
        push_with_optional_token()
        logging.info("[chat_summary] chatlog pushed to git for %s", date_key)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        logging.error(
            "[chat_summary] git sync failed (export is still saved). code=%s stdout=%s stderr=%s",
            exc.returncode,
            stdout,
            stderr,
        )


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
                ORDER BY mr.chat_id ASC, mr.date ASC, mr.message_id ASC
                """
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
                ORDER BY mr.chat_id ASC, mr.date ASC, mr.message_id ASC
                """
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

    if exported:
        _git_commit_and_push_for_exchange(window.date_key)
    else:
        logging.info("[chat_summary] No messages exported for %s", window.date_key)
    return exported


async def export_daily_chatlogs_task(bot: Bot) -> None:
    _ = bot
    while True:
        now = datetime.now()
        target = now.replace(hour=23, minute=30, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await _sleep_until(target)
        try:
            exported = export_daily_chatlogs()
            logging.info("[chat_summary] Exported %d chatlog files", len(exported))
        except Exception:
            logging.exception("[chat_summary] export_daily_chatlogs failed")


async def publish_daily_summary_task(bot: Bot) -> None:
    while True:
        now = datetime.now()
        target = now.replace(hour=23, minute=55, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await _sleep_until(target)
        try:
            await publish_daily_summaries(bot)
        except Exception:
            logging.exception("[chat_summary] publish_daily_summaries failed")


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
    date_key = datetime.now().strftime("%Y_%m_%d")
    if not EXCHANGE_DIR.exists():
        logging.info("[chat_summary] ai_exchange folder is missing")
        return 0

    files = sorted(EXCHANGE_DIR.glob(f"{date_key}_*_summary.json"))
    published = 0
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
