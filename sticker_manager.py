# sticker_manager.py (БД-версия, без часовых поясов)
import asyncio
import logging
from datetime import datetime, timedelta
import sqlite3
from db import get_connection  # ваш модуль db.py

# ====== НАСТРОЙКИ ======
SILENCE_STICKER_ID = "CAACAgIAAyEFAASixe81AAEBKBZonrxM7qEb65AQWLINQj-igCqgZQACjHYAAu1RQErYR3VajrrA1TYE"
WINDOW_START_HOUR = 11
WINDOW_END_HOUR = 21
SILENCE_DELTA = timedelta(hours=2)
CHECK_INTERVAL_SECONDS = 300  # 5 минут

# ====== ВНУТРЕННЕЕ СОСТОЯНИЕ ======
_known_chats: set[int] = set()
_last_message_time: dict[int, datetime] = {}
_last_sent_date: dict[int, datetime.date] = {}

# -------------------------------
# Инициализация списка чатов из БД
# -------------------------------
def seed_known_chats_from_db():
    """Заполняет _known_chats на основе таблицы users."""
    global _known_chats
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT chat_id FROM users")
        rows = cur.fetchall()
        for row in rows:
            try:
                _known_chats.add(int(row["chat_id"]))
            except Exception:
                continue
    logging.info(f"[sticker_manager] seed_known_chats_from_db: {_known_chats}")

# -------------------------------
# Отметка активности
# -------------------------------
def note_activity(chat_id: int, message_dt: datetime) -> None:
    """Отметить активность в чате."""
    _known_chats.add(int(chat_id))
    if not isinstance(message_dt, datetime):
        message_dt = datetime.now()
    _last_message_time[chat_id] = max(_last_message_time.get(chat_id, message_dt), message_dt)

# -------------------------------
# Фоновая задача проверки тишины
# -------------------------------
async def silence_checker_task(bot, check_interval_seconds: int = CHECK_INTERVAL_SECONDS) -> None:
    """Фоновая задача по проверке тишины и отправке стикера."""
    while True:
        try:
            now = datetime.now()
            if WINDOW_START_HOUR <= now.hour < WINDOW_END_HOUR:
                for chat_id in list(_known_chats):
                    last_msg = _last_message_time.get(chat_id)
                    if not last_msg or (now - last_msg) < SILENCE_DELTA:
                        continue
                    if _last_sent_date.get(chat_id) == now.date():
                        continue

                    try:
                        await bot.send_sticker(chat_id, SILENCE_STICKER_ID)
                        _last_sent_date[chat_id] = now.date()
                        logging.info(f"[silence_checker] sent sticker to chat {chat_id} at {now.isoformat()}")
                    except Exception as e:
                        logging.exception(f"[silence_checker] failed to send sticker to chat {chat_id}: {e}")

        except Exception:
            logging.exception("[silence_checker] loop exception")

        await asyncio.sleep(check_interval_seconds)
