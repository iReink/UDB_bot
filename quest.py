# quest.py
import asyncio
import random
import logging
from contextlib import closing
from datetime import date
from typing import Optional

from aiogram import types, Bot
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db import get_connection, get_user_display_name
from sosalsa import add_sits

# ==============================
# ОБНОВЛЕНИЕ ПРОГРЕССА
# ==============================

async def update_quest_progress(user_id: int, chat_id: int, quest_type: str, increment: int = 1, bot: Optional[Bot] = None):
    """Обновляет прогресс активного квеста пользователя."""
    today = date.today().isoformat()

    def db_logic():
        with closing(get_connection()) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT uq.quest_id, uq.progress, qc.target, qc.reward, qc.type
                FROM user_quests uq
                JOIN quests_catalog qc ON uq.quest_id = qc.quest_id
                WHERE uq.user_id = ? AND uq.chat_id = ? AND uq.date_taken = ? AND uq.status = 'active'
            """, (user_id, chat_id, today))
            row = cur.fetchone()
            if not row:
                return None
            quest_id, progress, target, reward, q_type = row
            if q_type != quest_type:
                return None

            new_progress = progress + increment
            cur.execute("""
                UPDATE user_quests SET progress = ? WHERE user_id = ? AND chat_id = ? AND date_taken = ?
            """, (new_progress, user_id, chat_id, today))
            conn.commit()
            return (new_progress, target, reward)

    result = await asyncio.to_thread(db_logic)
    if result:
        new_progress, target, reward = result
        if new_progress >= target:
            await complete_quest(user_id, chat_id, reward, bot)

# ==============================
# ЗАВЕРШЕНИЕ КВЕСТА
# ==============================

async def complete_quest(user_id: int, chat_id: int, reward: int, bot: Optional[Bot] = None):
    """Отмечает квест выполненным, выдает награду и шлёт сообщение в чат."""
    today = date.today().isoformat()

    def db_logic():
        with closing(get_connection()) as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE user_quests
                SET status = 'completed', date_completed = ?
                WHERE user_id = ? AND chat_id = ? AND date_taken = ?
            """, (today, user_id, chat_id, today))
            conn.commit()

    await asyncio.to_thread(db_logic)

    user_name = get_user_display_name(user_id, chat_id)
    add_sits(chat_id, user_id, reward)
    logging.info(f"Квест выполнен: {user_name} (user_id={user_id}) получил {reward} сит")

    if bot:
        await bot.send_message(chat_id, f"🎉 Поздравляем! {user_name} выполнил квест и получил {reward} сит!")

# ==============================
# УТИЛИТЫ
# ==============================

def get_user_daily_quest(user_id: int, chat_id: int):
    """Возвращает активный квест пользователя, если есть."""
    today = date.today().isoformat()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT uq.quest_id, uq.progress, qc.description, qc.target, qc.reward
            FROM user_quests uq
            JOIN quests_catalog qc ON uq.quest_id = qc.quest_id
            WHERE uq.user_id = ? AND uq.chat_id = ? AND uq.date_taken = ? AND uq.status = 'active'
        """, (user_id, chat_id, today))
        return cur.fetchone()

def get_random_quests(count=3):
    """Возвращает случайные квесты из каталога."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT quest_id, description, target, reward FROM quests_catalog")
        quests = cur.fetchall()
    return random.sample(quests, min(count, len(quests)))

def assign_quest(user_id: int, chat_id: int, quest_id: int):
    """Назначает квест пользователю."""
    today = date.today().isoformat()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_quests (user_id, chat_id, quest_id, date_taken, status, progress)
            VALUES (?, ?, ?, ?, 'active', 0)
        """, (user_id, chat_id, quest_id, today))
        conn.commit()

# ==============================
# РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ
# ==============================

def register_quest_handlers(dp):
    @dp.message(Command(commands=["quest"]))
    async def cmd_quest(message: types.Message):
        user_id = message.from_user.id
        chat_id = message.chat.id

        quest = get_user_daily_quest(user_id, chat_id)
        if quest:
            quest_id, progress, description, target, reward = quest
            await message.answer(
                f"📜 У тебя уже есть активный квест:\n"
                f"— {description}\n"
                f"📈 Прогресс: {progress}/{target}\n"
                f"🏆 Награда: {reward} сит"
            )
            return

        quests = get_random_quests(3)
        kb = InlineKeyboardBuilder()
        for q in quests:
            q_id, description, target, reward = q
            kb.button(
                text=f"{description} (🏆 {reward})",
                callback_data=f"quest_pick:{q_id}"
            )
        kb.adjust(1)
        await message.answer("🎯 Выбери квест на сегодня:", reply_markup=kb.as_markup())

    @dp.callback_query(lambda c: c.data.startswith("quest_pick:"))
    async def on_quest_pick(query: types.CallbackQuery):
        user_id = query.from_user.id
        chat_id = query.message.chat.id
        quest_id = int(query.data.split(":")[1])

        if get_user_daily_quest(user_id, chat_id):
            await query.answer("Ты уже выбрал квест на сегодня!", show_alert=True)
            return

        assign_quest(user_id, chat_id, quest_id)

        with closing(get_connection()) as conn:
            cur = conn.cursor()
            cur.execute("SELECT description, target, reward FROM quests_catalog WHERE quest_id = ?", (quest_id,))
            quest = cur.fetchone()

        if quest:
            description, target, reward = quest
            await query.message.answer(
                f"✅ Квест принят:\n"
                f"— {description}\n"
                f"📈 Прогресс: 0/{target}\n"
                f"🏆 Награда: {reward} сит"
            )
        await query.answer("Квест выбран!")
