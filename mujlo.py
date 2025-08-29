# Модуль для отправки стикера «Тише,мужло, пора спать!» в случае если после полуночи и до 5 утра в чат пишет мужло
# Мужло имеет право куптиь свободу голоса за 2 сита, бот должен показать кнопку

import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db import get_connection, add_or_update_user
from main import dp, bot  # если bot объявлен в main.py
# или лучше сделать отдельный импорт bot через общий модуль, если есть

MUJLO = "CAACAgIAAyEFAASixe81AAEBo3posMDwzO10nION2l0m2Rzk7L_UJAACcl4AAq0s-Uufvzuo1oaf2jYE"

# словарь для кулдаунов: (chat_id, user_id) -> datetime последнего ответа
_last_mujlo_reply = {}


async def handle_mujlo_message(message: types.Message):
    """
    Обрабатывает сообщения для мужло-режима:
    - Только ночью (22:00–03:00)
    - Только пользователи с sex = 'm'
    - Не чаще одного раза в 3 минуты
    - Если mujlo_freed = 1, то больше не трогаем
    """
    try:
        now = datetime.now()
        hour = now.hour

        # --- Проверка времени ---
        if not (hour >= 22 or hour < 3):
            return

        chat_id = message.chat.id
        user_id = message.from_user.id

        # --- Проверка пола и статуса ---
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT sex FROM users WHERE user_id=? AND chat_id=?", (user_id, chat_id))
            row = cur.fetchone()
            if not row or row["sex"] != "m":
                return

            cur.execute("SELECT mujlo_freed FROM mujlo WHERE user_id=? AND chat_id=?", (user_id, chat_id))
            mj = cur.fetchone()
            if not mj or mj["mujlo_freed"] == 1:
                return

        # --- Проверка кулдауна ---
        last_time = _last_mujlo_reply.get((chat_id, user_id))
        if last_time and (now - last_time) < timedelta(minutes=3):
            return
        _last_mujlo_reply[(chat_id, user_id)] = now

        # --- Отправка стикера + кнопки ---
        kb = InlineKeyboardBuilder()
        kb.button(text="Купить право высказаться (2 сита)", callback_data=f"mujlo_buy:{chat_id}:{user_id}")
        kb.adjust(1)

        await bot.send_sticker(chat_id, MUJLO, reply_to_message_id=message.message_id)
        await bot.send_message(chat_id, "😶", reply_markup=kb.as_markup())

    except Exception as e:
        logging.error(f"[mujlo] Ошибка обработки: {e}")


@dp.callback_query(lambda c: c.data.startswith("mujlo_buy:"))
async def handle_mujlo_buy(callback: types.CallbackQuery):
    try:
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Ошибка данных", show_alert=True)
            return

        chat_id = int(parts[1])
        user_id = int(parts[2])

        # проверка пользователя и его сита
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT sits, name FROM users WHERE user_id=? AND chat_id=?", (user_id, chat_id))
            user = cur.fetchone()
            if not user:
                await callback.answer("Пользователь не найден в БД", show_alert=True)
                return

            sits = user["sits"] or 0
            name = user["name"]

            if sits < 2:
                await callback.answer("Недостаточно ситов!", show_alert=True)
                return

            # списываем 2 сита
            new_sits = sits - 2
            add_or_update_user(user_id, chat_id, name, sits=new_sits)

            # помечаем что освобождён
            cur.execute(
                "UPDATE mujlo SET mujlo_freed=1 WHERE user_id=? AND chat_id=?",
                (user_id, chat_id)
            )
            conn.commit()

        # редактируем кнопку, чтобы её нельзя было жать снова
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass  # бывает если кнопки уже нет

        # уведомляем чат
        await bot.send_message(chat_id, f"✅ Пользователь {name} теперь может говорить свободно")

        # подтверждаем колбэк
        await callback.answer("Вы купили право говорить")

    except Exception as e:
        logging.error(f"[mujlo] Ошибка при покупке: {e}")
        await callback.answer("Ошибка при обработке", show_alert=True)