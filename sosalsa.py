# sosalsa.py
import random
from datetime import datetime, timedelta
from contextlib import closing
from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db import get_connection, get_user_sex
from main import dp  # Импортируем Dispatcher из main.py


# ==========================
# БАЗА ДАННЫХ
# ==========================

def increment_sosalsa(chat_id: int, u1: int, u2: int, shpeh: bool = False):
    """Увеличивает счётчик сосания или шпёха для пары (u1, u2)."""
    user_id1, user_id2 = sorted([u1, u2])
    column = "shpehalsa_count" if shpeh else "sosalsa_count"

    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(f"""
            INSERT INTO sosalsa_stats (chat_id, user_id1, user_id2, {column})
            VALUES (?, ?, ?, 1)
            ON CONFLICT(chat_id, user_id1, user_id2)
            DO UPDATE SET {column} = {column} + 1
        """, (chat_id, user_id1, user_id2))
        conn.commit()


def get_top_pairs(chat_id: int, shpeh: bool = False, limit: int = 10):
    """Возвращает топ пар по сосанию или шпёху."""
    column = "shpehalsa_count" if shpeh else "sosalsa_count"
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT user_id1, user_id2, {column}
            FROM sosalsa_stats
            WHERE chat_id = ? AND {column} > 0
            ORDER BY {column} DESC
            LIMIT ?
        """, (chat_id, limit))
        return cur.fetchall()


# ==========================
# ЛОГИКА ВЫБОРА ПАРТНЁРОВ
# ==========================

def get_active_users(chat_id: int, days: int = 7):
    """Возвращает список user_id активных пользователей за последние N дней."""
    date_threshold = (datetime.now() - timedelta(days=days)).date().isoformat()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT user_id
            FROM daily_stats
            WHERE chat_id = ? AND date >= ?
        """, (chat_id, date_threshold))
        return [row[0] for row in cur.fetchall()]


def get_random_active_user(chat_id: int, exclude_user: int):
    """Выбирает случайного активного пользователя, кроме указанного."""
    active_users = get_active_users(chat_id)
    candidates = [uid for uid in active_users if uid != exclude_user]
    return random.choice(candidates) if candidates else None


def get_possible_shpeh_partners(chat_id: int, buyer_id: int):
    """Возвращает список user_id, с кем buyer_id сосался >=3 раз, и кто активен."""
    active_users = set(get_active_users(chat_id))
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT user_id1, user_id2, sosalsa_count
            FROM sosalsa_stats
            WHERE chat_id = ? AND sosalsa_count >= 3
        """, (chat_id,))
        candidates = set()
        for u1, u2, _ in cur.fetchall():
            if buyer_id in (u1, u2):
                partner = u1 if u2 == buyer_id else u2
                if partner in active_users:
                    candidates.add(partner)
    return list(candidates)


# ==========================
# INLINE-МЕНЮ
# ==========================

def get_sos_menu():
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="💋 Рандомно пососаться", callback_data="sos_random"))
    kb.row(types.InlineKeyboardButton(text="🔥 Рандомно пошпёхаться", callback_data="shpeh_random"))
    kb.row(types.InlineKeyboardButton(text="📊 Статистика сосания", callback_data="sos_stats"))
    kb.row(types.InlineKeyboardButton(text="📊 Статистика шпёха", callback_data="shpeh_stats"))
    return kb.as_markup()


@dp.message(Command("sos"))
async def sos_command(message: types.Message):
    """Команда /sos: показывает меню."""
    await message.answer("Выбирай действие:", reply_markup=get_sos_menu())


# ==========================
# CALLBACK-ОБРАБОТЧИКИ
# ==========================

@dp.callback_query(lambda c: c.data in ["sos_random", "shpeh_random", "sos_stats", "shpeh_stats"])
async def sos_callback(query: types.CallbackQuery):
    action = query.data
    user_id = query.from_user.id
    chat_id = query.message.chat.id

    # Определяем имя и пол покупателя
    buyer_name = get_user_display_name(user_id, chat_id)
    buyer_sex = get_user_sex(user_id, chat_id)  # 'male', 'female' или None

    def verb_sos(sex):
        return "пососалась" if sex == "female" else "пососался"

    def verb_shpeh(sex):
        return "пошпёхалась" if sex == "female" else "пошпёхался"

    # ==========================
    # Рандомно пососаться
    # ==========================
    if action == "sos_random":
        target_id = get_random_active_user(chat_id, user_id)
        if not target_id:
            await query.answer("Нет активных участников!")
            return

        target_name = get_user_display_name(target_id, chat_id)
        increment_sosalsa(chat_id, user_id, target_id, shpeh=False)

        await query.message.answer(
            f"💋 {buyer_name} {verb_sos(buyer_sex)} с {target_name}"
        )

    # ==========================
    # Рандомно пошпёхаться
    # ==========================
    elif action == "shpeh_random":
        partners = get_possible_shpeh_partners(chat_id, user_id)
        if not partners:
            await query.answer("Извини, не с кем. Попробуй сначала пососаться.")
            return

        target_id = random.choice(partners)
        target_name = get_user_display_name(target_id, chat_id)
        increment_sosalsa(chat_id, user_id, target_id, shpeh=True)

        await query.message.answer(
            f"🔥 {buyer_name} {verb_shpeh(buyer_sex)} с {target_name}"
        )

    # ==========================
    # Статистика сосания
    # ==========================
    elif action == "sos_stats":
        rows = get_top_pairs(chat_id, shpeh=False)
        if not rows:
            await query.message.answer("Статистика пуста.")
        else:
            text = "📊 Топ по сосанию:\n"
            for i, (u1, u2, cnt) in enumerate(rows, 1):
                name1 = get_user_display_name(u1, chat_id)
                name2 = get_user_display_name(u2, chat_id)
                text += f"{i}. {name1} ❤️ {name2} — {cnt} раз(а)\n"
            await query.message.answer(text)

    # ==========================
    # Статистика шпёха
    # ==========================
    elif action == "shpeh_stats":
        rows = get_top_pairs(chat_id, shpeh=True)
        if not rows:
            await query.message.answer("Статистика пуста.")
        else:
            text = "📊 Топ по шпёху:\n"
            for i, (u1, u2, cnt) in enumerate(rows, 1):
                name1 = get_user_display_name(u1, chat_id)
                name2 = get_user_display_name(u2, chat_id)
                text += f"{i}. {name1} 🔥 {name2} — {cnt} раз(а)\n"
            await query.message.answer(text)

    await query.answer()


def get_user_display_name(user_id: int, chat_id: int) -> str:
    """Возвращает красивое имя пользователя по user_id."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT username, first_name, last_name
            FROM users
            WHERE user_id = ? AND chat_id = ?
        """, (user_id, chat_id))
        row = cur.fetchone()

    if row:
        username, first_name, last_name = row
        if username:
            return f"@{username}"
        elif last_name:
            return f"{first_name} {last_name}"
        return first_name
    return str(user_id)  # fallback
