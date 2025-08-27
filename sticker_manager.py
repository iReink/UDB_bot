import asyncio
import logging
from datetime import datetime, timedelta, timezone
from db import get_connection  # твоя функция подключения к SQLite
from aiogram import Bot

# ====== НАСТРОЙКИ ======
SILENCE_STICKER_ID = "CAACAgIAAyEFAASixe81AAEBKBZonrxM7qEb65AQWLINQj-igCqgZQACjHYAAu1RQErYR3VajrrA1TYE"
WINDOW_START_HOUR = 11
WINDOW_END_HOUR = 21
SILENCE_DELTA = timedelta(hours=2)
CHECK_INTERVAL_SECONDS = 300  # 5 минут

# ====== ВНУТРЕННЕЕ СОСТОЯНИЕ ======
_last_sent_date: dict[int, datetime.date] = {}

bot: Bot = None  # будет инициализирован в main.py

# -------------------------------
# Функция для проверки тишины
# -------------------------------
async def silence_checker_task():
    global bot
    if bot is None:
        logging.error("[silence_checker] bot не инициализирован")
        return

    while True:
        try:
            now = datetime.now(timezone(timedelta(hours=3)))  # локальное время +3
            if WINDOW_START_HOUR <= now.hour < WINDOW_END_HOUR:
                with get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT DISTINCT chat_id FROM messages_reactions")
                    chat_ids = [row[0] for row in cur.fetchall()]

                    for chat_id in chat_ids:
                        cur.execute(
                            "SELECT MAX(timestamp) FROM messages_reactions WHERE chat_id = ?",
                            (chat_id,)
                        )
                        last_msg_utc = cur.fetchone()[0]

                        if last_msg_utc is None:
                            continue  # сообщений нет

                        # преобразуем UTC → локальное время
                        last_msg_utc = datetime.fromisoformat(last_msg_utc).replace(tzinfo=timezone.utc)
                        last_msg_local = last_msg_utc.astimezone(timezone(timedelta(hours=3)))

                        if now - last_msg_local < SILENCE_DELTA:
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

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
