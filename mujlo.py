# Модуль для отправки стикера «Тише,мужло, пора спать!» в случае если после полуночи и до 5 утра в чат пишет мужло
# Мужло имеет право куптиь свободу голоса за 2 сита, бот должен показать кнопку

import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import types, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db import get_connection, add_or_update_user
# или лучше сделать отдельный импорт bot через общий модуль, если есть

MUJLO = "CAACAgIAAyEFAASixe81AAEBo3posMDwzO10nION2l0m2Rzk7L_UJAACcl4AAq0s-Uufvzuo1oaf2jYE"

# --- Внутреннее состояние ---
_last_mujlo_sent: dict[tuple[int, int], datetime] = {}  # ключ (chat_id, user_id) -> время последнего стикера

async def handle_mujlo_message(message: types.Message):
    """Обрабатываем сообщение пользователя для MUJLO-стикера."""
    try:
        now = datetime.now()
        hour = now.hour
        if not (22 <= hour or hour < 3):
            return  # только между 22:00 и 03:00

        chat_id = message.chat.id
        user_id = message.from_user.id

        # Проверяем пол пользователя
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT sex FROM users WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
            row = cur.fetchone()
            if not row or row["sex"] != "m":
                return

        # Проверяем таймаут 3 минуты
        last_time = _last_mujlo_sent.get((chat_id, user_id))
        if last_time and (now - last_time) < timedelta(minutes=3):
            return

        # Проверяем, не купил ли пользователь право говорить до конца ночи
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT mujlo_freed FROM mujlo WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            mujlo_row = cur.fetchone()
            if mujlo_row and mujlo_row["mujlo_freed"]:
                return

        # --- Отправка стикера + кнопки ---
        kb = InlineKeyboardBuilder()
        kb.button(
            text="Купить право высказаться (2 сита)",
            callback_data=f"mujlo_buy:{chat_id}:{user_id}"
        )
        kb.adjust(1)

        await message.answer_sticker(MUJLO, reply_to_message_id=message.message_id)
        await message.answer("😶", reply_markup=kb.as_markup())

        _last_mujlo_sent[(chat_id, user_id)] = now

    except Exception as e:
        logging.error(f"[mujlo] Ошибка обработки сообщения: {e}")


@dp.callback_query(lambda c: c.data.startswith("mujlo_buy:"))
async def handle_mujlo_buy(callback: types.CallbackQuery):
    """Обрабатываем покупку права говорить."""
    try:
        bot = callback.bot  # берём объект бота из callback
        _, chat_id_str, user_id_str = callback.data.split(":")
        chat_id, user_id = int(chat_id_str), int(user_id_str)

        # Получаем пользователя и проверяем баланс сит
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name, sits FROM users WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            user = cur.fetchone()
            if not user:
                await callback.answer("Пользователь не найден", show_alert=True)
                return
            name, sits = user["name"], user["sits"]

            if sits < 2:
                await callback.answer("Недостаточно сит", show_alert=True)
                return

            # Списываем 2 сита
            cur.execute("UPDATE users SET sits = sits - 2 WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            # Отмечаем, что пользователь освобождён до конца ночи
            cur.execute("UPDATE mujlo SET mujlo_freed = 1 WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            conn.commit()

        # Отправляем сообщение в чат
        await bot.send_message(chat_id, f"✅ Пользователь {name} теперь может говорить свободно")
        await callback.answer()  # визуально отпускаем кнопку

    except Exception as e:
        logging.error(f"[mujlo] Ошибка обработки покупки: {e}")