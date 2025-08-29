# Модуль для отправки стикера «Тише,мужло, пора спать!» в случае если после полуночи и до 5 утра в чат пишет мужло
# Мужло имеет право куптиь свободу голоса за 2 сита, бот должен показать кнопку

import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import types, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db import get_connection, add_or_update_user, get_user
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


# @dp.callback_query(lambda c: c.data.startswith("mujlo_buy:"))
async def handle_mujlo_buy(callback: types.CallbackQuery):
    chat_id, user_id = map(int, callback.data.split(":")[1:])
    user = get_user(user_id, chat_id)

    if not user:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    # Проверяем, не купил ли уже право
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT mujlo_freed FROM mujlo WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        row = cur.fetchone()
        if row and row["mujlo_freed"]:
            await callback.answer("Пользователь уже купил право говорить!", show_alert=True)
            # Удаляем кнопку, чтобы нельзя было нажать
            await callback.message.edit_reply_markup(reply_markup=None)
            return

    # Проверяем баланс сит
    if user["sits"] < 2:
        await callback.answer("Недостаточно сит для покупки права.", show_alert=True)
        return

    # Списываем сита и помечаем право купленным
    add_or_update_user(user_id, chat_id, user["name"], sits=user["sits"] - 2)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE mujlo SET mujlo_freed=1 WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        conn.commit()

    # Сообщение в чат
    await callback.message.edit_reply_markup(reply_markup=None)  # убираем кнопку
    await callback.message.answer(f"✅ Пользователь {user['name']} теперь может говорить свободно")
