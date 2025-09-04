# quest.py
import asyncio
import sqlite3
import logging
from contextlib import closing
from datetime import date
from typing import Optional

from db import get_connection, get_user_display_name
from sosalsa import add_sits
from aiogram import Bot
from aiogram.filters import Command


# Лок для безопасного обновления прогресса квестов
QUEST_DB_LOCK = asyncio.Lock()

# ==============================
# ОБНОВЛЕНИЕ ПРОГРЕССА (async)
# ==============================
async def update_quest_progress(user_id: int, chat_id: int, quest_type: str, increment: int = 1, bot: Optional[Bot] = None):
    """
    Безопасно обновляет прогресс активного квеста пользователя.
    Использует асинхронный Lock для избежания блокировки базы.
    """
    async with QUEST_DB_LOCK:
        today = date.today().isoformat()

        with closing(get_connection()) as conn:
            cur = conn.cursor()

            # Получаем активный квест пользователя
            cur.execute("""
                SELECT uq.user_id, uq.chat_id, uq.quest_id, uq.progress, qc.target, qc.reward, qc.type
                FROM user_quests uq
                JOIN quests_catalog qc ON uq.quest_id = qc.quest_id
                WHERE uq.user_id = ? AND uq.chat_id = ? AND uq.date_taken = ? AND uq.status = 'active'
            """, (user_id, chat_id, today))
            row = cur.fetchone()

            if not row:
                return  # Нет активного квеста

            _, _, quest_id, progress, target, reward, quest_type_db = row

            if quest_type_db != quest_type:
                return  # Квест не соответствует типу действия

            # Обновляем прогресс
            new_progress = progress + increment
            cur.execute("""
                UPDATE user_quests SET progress = ? WHERE user_id = ? AND chat_id = ? AND quest_id = ? AND date_taken = ?
            """, (new_progress, user_id, chat_id, quest_id, today))
            conn.commit()

            # Проверяем выполнение
            if new_progress >= target:
                # Завершаем квест
                await complete_quest(user_id, chat_id, quest_id, reward, bot)


# ==============================
# ЗАВЕРШЕНИЕ КВЕСТА (async)
# ==============================
async def complete_quest(user_id: int, chat_id: int, quest_id: int, reward: int, bot: Optional[Bot] = None):
    """Отмечает квест выполненным, выдает награду и шлёт сообщение в чат."""
    async with QUEST_DB_LOCK:
        with closing(get_connection()) as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE user_quests SET status = 'completed', date_completed = ?
                WHERE user_id = ? AND chat_id = ? AND quest_id = ? AND status = 'active'
            """, (date.today().isoformat(), user_id, chat_id, quest_id))
            conn.commit()

    # Получаем имя пользователя
    user_name = get_user_display_name(user_id, chat_id)

    # Выдаём награду
    add_sits(chat_id, user_id, reward)

    # Лог
    logging.info(f"Квест выполнен: {user_name} (user_id={user_id}) получил {reward} сит")

    # Сообщение в чат
    if bot:
        import asyncio
        asyncio.create_task(
            bot.send_message(chat_id, f"🎉 Поздравляем! {user_name} выполнил квест и получил {reward} сит!")
        )

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
# РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# ==============================

def register_quest_handlers(dp):

    @dp.message(Command(commands=["quest"]))
    async def cmd_quest(message: types.Message):
        user_id = message.from_user.id
        chat_id = message.chat.id

        # Проверяем, есть ли активный квест
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

        # Выбираем 3 случайных квеста
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

        # Проверка, что квест уже не назначен
        if get_user_daily_quest(user_id, chat_id):
            await query.answer("Ты уже выбрал квест на сегодня!", show_alert=True)
            return

        # Назначаем квест
        assign_quest(user_id, chat_id, quest_id)

        # Получаем данные квеста для отображения
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
