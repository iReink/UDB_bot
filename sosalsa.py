# sosalsa.py
import random
from datetime import datetime, timedelta
from contextlib import closing
from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db import get_connection, get_user_sex

# ==========================
# ЖЕНАТЫЕ ПАРЫ (можно добавлять новых)
# ==========================
MARRIED_PAIRS = [
    (749027951, 884940984),
]

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


def get_active_users(chat_id: int, days: int = 7):
    """Возвращает список user_id активных пользователей за последние N дней,
    у которых messages > 0 хотя бы в один из последних N дней."""
    date_threshold = (datetime.now() - timedelta(days=days)).date().isoformat()
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT user_id
            FROM daily_stats
            WHERE chat_id = ? AND date >= ? AND messages > 0
        """, (chat_id, date_threshold))
        return [row[0] for row in cur.fetchall()]



def get_random_active_user(chat_id: int, buyer_id: int):
    """Выбирает случайного активного пользователя, исключая женатые пары и себя."""
    active_users = get_active_users(chat_id)
    candidates = []

    for uid in active_users:
        # Исключаем себя
        if uid == buyer_id:
            continue

        # Проверяем женатые пары: если buyer или uid в MARRIED_PAIRS, оставляем только друг с другом
        in_pair = None
        for u1, u2 in MARRIED_PAIRS:
            if buyer_id in (u1, u2):
                in_pair = (u1, u2)
                break

        if in_pair:
            # Если buyer в паре, партнёр должен быть другой половинкой
            if uid in in_pair:
                candidates.append(uid)
        else:
            # Если buyer не в паре, исключаем всех из пар
            if any(uid in pair for pair in MARRIED_PAIRS):
                continue
            candidates.append(uid)

    return random.choice(candidates) if candidates else None


def get_possible_shpeh_partners(chat_id: int, buyer_id: int):
    """Возвращает список user_id для шпёха (>=3 сосаний) и активных."""
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

    # Фильтруем по женатым парам
    filtered = []
    for uid in candidates:
        in_pair = None
        for u1, u2 in MARRIED_PAIRS:
            if buyer_id in (u1, u2):
                in_pair = (u1, u2)
                break

        if in_pair:
            if uid in in_pair:
                filtered.append(uid)
        else:
            if any(uid in pair for pair in MARRIED_PAIRS):
                continue
            filtered.append(uid)

    return filtered

def get_user_stats(chat_id: int, user_id: int, shpeh: bool = False):
    """Возвращает список партнёров и количество взаимодействий для конкретного пользователя."""
    column = "shpehalsa_count" if shpeh else "sosalsa_count"
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT user_id1, user_id2, {column}
            FROM sosalsa_stats
            WHERE chat_id = ? AND {column} > 0 AND (user_id1 = ? OR user_id2 = ?)
            ORDER BY {column} DESC
        """, (chat_id, user_id, user_id))
        return cur.fetchall()


# ==========================
# INLINE-МЕНЮ
# ==========================

def get_sos_menu():
    kb = InlineKeyboardBuilder()
    kb.row(
        types.InlineKeyboardButton(
            text="💋 Рандомно пососаться (2 сита)",
            callback_data="sos_random"
        )
    )
    kb.row(
        types.InlineKeyboardButton(
            text="🔥 Рандомно пошпёхаться (5 ситов)",
            callback_data="shpeh_random"
        )
    )
    kb.row(
        types.InlineKeyboardButton(
            text="📊 Статистика сосания",
            callback_data="sos_stats"
        )
    )
    kb.row(
        types.InlineKeyboardButton(
            text="📊 Статистика шпёха",
            callback_data="shpeh_stats"
        )
    )
    kb.row(
        types.InlineKeyboardButton(
            text="👤 Моя статистика сосания",
            callback_data="my_sos_stats"
        )
    )
    kb.row(
        types.InlineKeyboardButton(
            text="👤 Моя статистика шпёха",
            callback_data="my_shpeh_stats"
        )
    )
    return kb.as_markup()


def get_user_display_name(user_id: int, chat_id: int) -> str:
    """Возвращает красивое имя пользователя по user_id."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT name
            FROM users
            WHERE user_id = ? AND chat_id = ?
        """, (user_id, chat_id))
        row = cur.fetchone()

    if row and row[0]:
        return row[0]
    return str(user_id)  # fallback


# ==========================
# СИТЫ
# ==========================

def add_sits(chat_id: int, user_id: int, amount: int):
    """Добавляет или вычитает сит для пользователя."""
    from db import get_user, add_or_update_user

    user = get_user(user_id, chat_id)
    if user is None:
        add_or_update_user(user_id, chat_id, name="", sits=amount)
    else:
        new_sits = (user["sits"] or 0) + amount
        add_or_update_user(user_id, chat_id, name=user["name"], sits=new_sits)


def get_sits(chat_id: int, user_id: int) -> int:
    """Возвращает баланс сит пользователя."""
    from db import get_user
    user = get_user(user_id, chat_id)
    if user and user["chat_id"] == chat_id:
        return user["sits"] or 0
    return 0


# ==========================
# РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# ==========================

def register_sos_handlers(dp):
    @dp.message(Command("sos"))
    async def sos_command(message: types.Message):
        await message.answer("Выбирай действие:", reply_markup=get_sos_menu())

    @dp.callback_query(lambda c: c.data in [
        "sos_random", "shpeh_random",
        "sos_stats", "shpeh_stats",
        "my_sos_stats", "my_shpeh_stats"
    ])
    async def sos_callback(query: types.CallbackQuery):
        action = query.data
        user_id = query.from_user.id
        chat_id = query.message.chat.id

        buyer_name = get_user_display_name(user_id, chat_id)
        buyer_sex = get_user_sex(user_id, chat_id)

        def verb_sos(sex): return "пососалась" if sex == "f" else "пососался"
        def verb_shpeh(sex): return "пошпёхалась" if sex == "f" else "пошпёхался"

        # ----------------------
        # Рандомно пососаться
        # ----------------------
        if action == "sos_random":
            cost = 2
            if get_sits(chat_id, user_id) < cost:
                await query.answer("Недостаточно сит для покупки действия!", show_alert=True)
                return

            target_id = get_random_active_user(chat_id, user_id)
            if not target_id:
                await query.answer("Нет активных участников!", show_alert=True)
                return

            target_name = get_user_display_name(target_id, chat_id)
            increment_sosalsa(chat_id, user_id, target_id, shpeh=False)
            add_sits(chat_id, user_id, -cost)

            await query.message.answer(f"💋 {buyer_name} {verb_sos(buyer_sex)} с {target_name}")

        # ----------------------
        # Рандомно пошпёхаться
        # ----------------------
        elif action == "shpeh_random":
            cost = 5
            if get_sits(chat_id, user_id) < cost:
                await query.answer("Недостаточно сит для покупки действия!", show_alert=True)
                return

            partners = get_possible_shpeh_partners(chat_id, user_id)
            if not partners:
                await query.answer("Извини, не с кем. Попробуй сначала пососаться.", show_alert=True)
                return

            target_id = random.choice(partners)
            target_name = get_user_display_name(target_id, chat_id)
            increment_sosalsa(chat_id, user_id, target_id, shpeh=True)
            add_sits(chat_id, user_id, -cost)

            await query.message.answer(f"🔥 {buyer_name} {verb_shpeh(buyer_sex)} с {target_name}")

        # ----------------------
        # Статистика сосания
        # ----------------------
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

        # ----------------------
        # Статистика шпёха
        # ----------------------
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

        # ----------------------
        # Моя статистика сосания
        # ----------------------
        elif action == "my_sos_stats":
            rows = get_user_stats(chat_id, user_id, shpeh=False)
            if not rows:
                await query.message.answer("У тебя ещё нет сосаний 😢")
            else:
                text = f"👤 Личная статистика сосания ({buyer_name}):\n"
                for i, (u1, u2, cnt) in enumerate(rows, 1):
                    partner_id = u2 if u1 == user_id else u1
                    partner_name = get_user_display_name(partner_id, chat_id)
                    text += f"{i}. ❤️ {partner_name} — {cnt} раз(а)\n"
                await query.message.answer(text)

        # ----------------------
        # Моя статистика шпёха
        # ----------------------
        elif action == "my_shpeh_stats":
            rows = get_user_stats(chat_id, user_id, shpeh=True)
            if not rows:
                await query.message.answer("У тебя ещё нет шпёха 😢")
            else:
                text = f"👤 Личная статистика шпёха ({buyer_name}):\n"
                for i, (u1, u2, cnt) in enumerate(rows, 1):
                    partner_id = u2 if u1 == user_id else u1
                    partner_name = get_user_display_name(partner_id, chat_id)
                    text += f"{i}. 🔥 {partner_name} — {cnt} раз(а)\n"
                await query.message.answer(text)

        await query.answer()
