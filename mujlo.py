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
        if not 1 < hour < 5:
            return

        chat_id = message.chat.id
        user_id = message.from_user.id

        # Проверяем пол пользователя
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT sex FROM users WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
            row = cur.fetchone()
            if not row or row["sex"] != "m":
                return

        # Проверяем таймаут 30 минут
        last_time = _last_mujlo_sent.get((chat_id, user_id))
        if last_time and (now - last_time) < timedelta(minutes=30):
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
            text="2 сита",
            callback_data=f"mujlo_buy:{chat_id}:{user_id}:{message.from_user.id}"
        )
        kb.adjust(1)

        await message.answer_sticker(MUJLO, reply_to_message_id=message.message_id)
        await message.answer("Купить право высказаться", reply_markup=kb.as_markup())

        _last_mujlo_sent[(chat_id, user_id)] = now

    except Exception as e:
        logging.error(f"[mujlo] Ошибка обработки сообщения: {e}")


# @dp.callback_query(lambda c: c.data.startswith("mujlo_buy:"))
async def handle_mujlo_buy(callback: types.CallbackQuery):
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("Некорректные данные кнопки.", show_alert=True)
            return

        # Берём только chat_id и user_id
        chat_id = int(parts[1])
        target_user_id = int(parts[2])
        pressing_user_id = callback.from_user.id

        logging.info(f"[mujlo_buy] Triggered by pressing_user_id={pressing_user_id}, target_user_id={target_user_id}, data={callback.data}")

        # 🔒 Проверяем владельца кнопки
        if pressing_user_id != target_user_id:
            await callback.answer("❌ Эта кнопка не для вас", show_alert=True)
            return

        user = get_user(target_user_id, chat_id)
        if not user:
            await callback.answer("Пользователь не найден.", show_alert=True)
            return

        # Проверка покупки
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT mujlo_freed FROM mujlo WHERE chat_id=? AND user_id=?", (chat_id, target_user_id))
            row = cur.fetchone()
            if row and row["mujlo_freed"]:
                await callback.answer("Пользователь уже купил право говорить!", show_alert=True)
                await callback.message.edit_reply_markup(reply_markup=None)
                return

        if user["sits"] < 2:
            await callback.answer("Недостаточно сит для покупки права.", show_alert=True)
            return

        # Обновляем данные
        add_or_update_user(target_user_id, chat_id, user["name"], sits=user["sits"] - 2)
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE mujlo SET mujlo_freed=1 WHERE chat_id=? AND user_id=?", (chat_id, target_user_id))
            conn.commit()

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"✅ Пользователь {user['name']} теперь может говорить свободно")

    except Exception as e:
        logging.exception(f"[mujlo_buy] Ошибка при обработке кнопки: {e}")
        await callback.answer("Произошла ошибка при обработке кнопки.", show_alert=True)


async def reset_mujlo_daily():
    while True:
        now = datetime.now()
        reset_time = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= reset_time:
            reset_time += timedelta(days=1)
        wait_seconds = (reset_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE mujlo SET mujlo_freed = 0")
            conn.commit()
        logging.info("[mujlo] Сброс состояния mujlo_freed для всех пользователей")

