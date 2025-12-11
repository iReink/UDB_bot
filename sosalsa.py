# sosalsa.py
import random
from datetime import datetime, timedelta
from contextlib import closing
from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
import logging

from db import get_connection, get_user_sex

# ==========================
# ЖЕНАТЫЕ ПАРЫ (можно добавлять новых)
# ==========================
MARRIED_PAIRS = [
    (749027951, 884940984),
    (166083474,209887368)
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

from db import get_connection

def get_last_7_daily_bites(user_id: int, chat_id: int):
    """Возвращает список словарей за последние 7 дней с полями bites_given и bites_received."""
    from datetime import date, timedelta
    from db import get_connection

    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(7)]

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT date, bites_given, bites_received
            FROM daily_stats
            WHERE user_id=? AND chat_id=? AND date BETWEEN ? AND ?
        """, (user_id, chat_id, dates[-1], dates[0]))
        rows = cur.fetchall()

    # Приводим к словарю по дате
    rows_by_date = {row["date"]: row for row in rows}
    result = []
    for d in dates:
        if d in rows_by_date:
            r = rows_by_date[d]
            result.append({
                "date": d,
                "bites_given": r["bites_given"] or 0,
                "bites_received": r["bites_received"] or 0
            })
        else:
            result.append({"date": d, "bites_given": 0, "bites_received": 0})
    return result

def get_total_bites(user_id: int, chat_id: int):
    """Возвращает словарь с общей статистикой по кусам."""
    from db import get_connection

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT bites_given, bites_received
            FROM total_stats
            WHERE user_id=? AND chat_id=?
        """, (user_id, chat_id))
        row = cur.fetchone()
        if row:
            return {"bites_given": row["bites_given"], "bites_received": row["bites_received"]}
        return {"bites_given": 0, "bites_received": 0}


def ensure_user_body_parts(user_id: int, chat_id: int):
    """Проверяет, есть ли у пользователя все части тела в user_body_parts.
    Если какой-то части нет, создаёт запись с state=1.
    """
    with get_connection() as conn:
        cur = conn.cursor()

        # Получаем все части тела
        cur.execute("SELECT id FROM body_parts")
        all_parts = [row["id"] for row in cur.fetchall()]

        # Получаем уже существующие части у пользователя
        cur.execute("SELECT body_part_id FROM user_body_parts WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        existing_parts = {row["body_part_id"] for row in cur.fetchall()}

        # Определяем, чего не хватает
        missing_parts = set(all_parts) - existing_parts

        # Добавляем недостающие части
        for part_id in missing_parts:
            cur.execute(
                "INSERT INTO user_body_parts (user_id, chat_id, body_part_id, state) VALUES (?, ?, ?, 1)",
                (user_id, chat_id, part_id)
            )
        conn.commit()

def format_user_body_status(user_id: int, chat_id: int) -> str:
    """
    Возвращает строку с состоянием тела пользователя, используя эмодзи:
    ✅ = на месте, ❌ = откушено
    """
    from db import get_connection
    from contextlib import closing

    with closing(get_connection()) as conn:
        cur = conn.cursor()

        # Берем все части тела
        cur.execute("SELECT * FROM body_parts ORDER BY id")
        body_parts_rows = cur.fetchall()

        # Берем состояния пользователя
        cur.execute("""
            SELECT body_part_id, state
            FROM user_body_parts
            WHERE user_id = ? AND chat_id = ?
        """, (user_id, chat_id))
        user_parts_rows = cur.fetchall()
        user_body_parts_dict = {row["body_part_id"]: row for row in user_parts_rows}

    # Формируем текст
    text = "Состояние твоего тела:\n"
    for part in body_parts_rows:
        part_state_row = user_body_parts_dict.get(part["id"])
        emoji = "✅" if part_state_row and part_state_row["state"] else "❌"
        text += f"{emoji} {part['name_nom']}\n"

    return text


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


def get_active_users(chat_id: int, days: int = 5):
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


import asyncio
import random
import logging
from datetime import datetime, timedelta
from contextlib import closing
from db import get_connection, get_user, get_user_display_name

async def daily_regeneration_task(dp):
    """Бесконечная задача — каждый день в 01:00 восстанавливает случайные части тела пользователей."""
    while True:
        now = datetime.now()
        # Время следующего срабатывания: завтра в 01:00, либо сегодня, если ещё до 01:00
        regen_time = now.replace(hour=0, minute=53, second=0, microsecond=0)
        if regen_time <= now:
            regen_time += timedelta(days=1)

        wait_seconds = (regen_time - now).total_seconds()
        logging.info(f"[daily_regeneration] Следующее восстановление через {wait_seconds/3600:.1f} часов ({regen_time})")
        await asyncio.sleep(wait_seconds)

        # Выполняем восстановление
        await process_daily_regeneration(dp)

async def process_daily_regeneration(dp):
    """Проходит по всем пользователям и восстанавливает 1-3 части тела случайным образом."""
    restored_report = []

    with get_connection() as conn:
        cur = conn.cursor()

        # Получаем всех пользователей, у которых есть части тела
        cur.execute("SELECT DISTINCT user_id, chat_id FROM user_body_parts")
        users = cur.fetchall()

        for user_row in users:
            user_id = user_row["user_id"]
            chat_id = user_row["chat_id"]

            # Получаем части тела пользователя, которые уже "откушены" (state=0)
            cur.execute("""
                SELECT ubp.id, bp.name_nom
                FROM user_body_parts ubp
                JOIN body_parts bp ON ubp.body_part_id = bp.id
                WHERE ubp.user_id=? AND ubp.chat_id=? AND ubp.state=0
            """, (user_id, chat_id))
            lost_parts = cur.fetchall()

            if not lost_parts:
                continue  # нечего восстанавливать

            # Восстанавливаем от 1 до 3 частей (если есть меньше — восстанавливаем все)
            num_to_restore = min(len(lost_parts), random.randint(1, 3))
            parts_to_restore = random.sample(lost_parts, num_to_restore)

            restored_names = []
            for part in parts_to_restore:
                cur.execute("UPDATE user_body_parts SET state=1 WHERE id=?", (part["id"],))
                restored_names.append(part["name_nom"])

            if restored_names:
                user_name = get_user_display_name(user_id, chat_id)
                restored_report.append(f"🩺 {user_name} ({', '.join(restored_names)})")

        conn.commit()

    # Отправляем сообщение в чат. Собираем список уникальных чатов
    chat_ids = set(row["chat_id"] for row in users)
    if restored_report:
        report_text = "💚 Покусанные отрастили себе некоторые части тела:\n" + "\n".join(restored_report)
        for chat_id in chat_ids:
            try:
                await dp.bot.send_message(chat_id, report_text)
            except Exception as e:
                logging.warning(f"Не удалось отправить сообщение в чат {chat_id}: {e}")



# ==========================
# INLINE-МЕНЮ
# ==========================

def get_sos_menu():
    kb = InlineKeyboardBuilder()
    kb.row(
        types.InlineKeyboardButton(
            text="🦷 Рандомный кусь (1 сит)",
            callback_data="random_bite"
        )
    )
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
            text="📊 Статистика",
            callback_data="sos_stats_menu"
        )
    )
    return kb.as_markup()



def get_sos_stats_menu():
    kb = InlineKeyboardBuilder()

    kb.row(
        types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="sos_back"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            text="🦷 Статистика укусов",
            callback_data="bite_stats"
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
        "sos_stats_menu", "sos_back",
        "sos_stats", "shpeh_stats",
        "my_sos_stats", "my_shpeh_stats",
        "bite_stats", "random_bite"
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
        # Открыть меню статистики (2-й уровень)
        # ----------------------
        if action == "sos_stats_menu":
            await query.message.edit_reply_markup(reply_markup=get_sos_stats_menu())
            await query.answer()
            return

        # ----------------------
        # Вернуться в главное меню
        # ----------------------
        if action == "sos_back":
            await query.message.edit_reply_markup(reply_markup=get_sos_menu())
            await query.answer()
            return


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
            target_sex = get_user_sex(target_id, chat_id)

            # Глагол "получил / получила"
            def verb_received(sex):
                return "получила" if sex == "f" else "получил"

            increment_sosalsa(chat_id, user_id, target_id, shpeh=True)

            # Снимаем 5 ситов у инициатора
            add_sits(chat_id, user_id, -cost)

            # Случайная награда партнёру: 1–3 сита
            reward = random.randint(1, 3)
            add_sits(chat_id, target_id, reward)

            await query.message.answer(
                f"🔥 {buyer_name} {verb_shpeh(buyer_sex)} с {target_name}\n"
                f"💦 {target_name} {verb_received(target_sex)} {reward} сит(а)"
            )

        # ----------------------
        # Рандомно покусать
        # ----------------------
        elif action == "random_bite":
            cost = 1
            if get_sits(chat_id, user_id) < cost:
                await query.answer("Недостаточно сит для покупки действия!", show_alert=True)
                return

            # Получаем активных пользователей за последние 1 день
            active_users = get_active_users(chat_id, days=1)
            active_users = [uid for uid in active_users if uid != user_id]  # исключаем кусающего
            if not active_users:
                await query.answer("Нет активных пользователей для укуса 😢", show_alert=True)
                return

            # Инициализируем части тела у всех активных пользователей и кусающего
            for uid in active_users + [user_id]:
                ensure_user_body_parts(uid, chat_id)

            # Фильтруем пользователей с хотя бы одной живой частью тела
            with get_connection() as conn:
                cur = conn.cursor()
                users_with_parts = []
                for uid in active_users:
                    cur.execute("""
                        SELECT 1
                        FROM user_body_parts
                        WHERE user_id=? AND chat_id=? AND state=1
                        LIMIT 1
                    """, (uid, chat_id))
                    if cur.fetchone():
                        users_with_parts.append(uid)

                if not users_with_parts:
                    await query.answer("Извини, уже всё откусили до тебя 😢", show_alert=True)
                    return

                # Списываем сит
                add_sits(chat_id, user_id, -cost)

                # Выбираем случайного «жертву»
                victim_id = random.choice(users_with_parts)

                # Выбираем случайную живую часть тела жертвы
                cur.execute("""
                    SELECT ubp.body_part_id, bp.name_acc
                    FROM user_body_parts ubp
                    JOIN body_parts bp ON ubp.body_part_id = bp.id
                    WHERE ubp.user_id=? AND ubp.chat_id=? AND ubp.state=1
                """, (victim_id, chat_id))
                parts = cur.fetchall()
                victim_part = random.choice(parts)
                part_id = victim_part["body_part_id"]
                part_name = victim_part["name_acc"]

                # Откусываем жертву
                cur.execute("""
                    UPDATE user_body_parts
                    SET state=0
                    WHERE user_id=? AND chat_id=? AND body_part_id=?
                """, (victim_id, chat_id, part_id))

                # 20% шанс, что кусавший «пришивает» часть к себе (только если state=0)
                cur.execute("""
                    SELECT state FROM user_body_parts
                    WHERE user_id=? AND chat_id=? AND body_part_id=?
                """, (user_id, chat_id, part_id))
                row = cur.fetchone()
                added_to_biter = False
                if row and row["state"] == 0:
                    if random.random() < 0.2:
                        cur.execute("""
                            UPDATE user_body_parts
                            SET state=1
                            WHERE user_id=? AND chat_id=? AND body_part_id=?
                        """, (user_id, chat_id, part_id))
                        added_to_biter = True

                # Обновляем статистику
                today = datetime.now().date().isoformat()

                # --- daily_stats ---
                cur.execute("""
                    INSERT INTO daily_stats (user_id, chat_id, date, bites_given)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(user_id, chat_id, date)
                    DO UPDATE SET bites_given = bites_given + 1
                """, (user_id, chat_id, today))

                cur.execute("""
                    INSERT INTO daily_stats (user_id, chat_id, date, bites_received)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(user_id, chat_id, date)
                    DO UPDATE SET bites_received = bites_received + 1
                """, (victim_id, chat_id, today))

                # --- total_stats ---
                cur.execute("""
                    INSERT INTO total_stats (user_id, chat_id, bites_given, bites_received)
                    VALUES (?, ?, 1, 0)
                    ON CONFLICT(user_id, chat_id)
                    DO UPDATE SET bites_given = bites_given + 1
                """, (user_id, chat_id))

                cur.execute("""
                    INSERT INTO total_stats (user_id, chat_id, bites_given, bites_received)
                    VALUES (?, ?, 0, 1)
                    ON CONFLICT(user_id, chat_id)
                    DO UPDATE SET bites_received = bites_received + 1
                """, (victim_id, chat_id))

                conn.commit()

            # Формируем сообщение
            victim_name = get_user_display_name(victim_id, chat_id)
            biter_name = get_user_display_name(user_id, chat_id)

            biter_sex = get_user_sex(user_id, chat_id)
            verb_bite = "откусила" if biter_sex == "f" else "откусил"

            text = f"🦷 {biter_name} {verb_bite} {part_name} у {victim_name}!"

            if added_to_biter:
                text += f" 😏 Кусавший присоединил {part_name} к себе!"

            await query.message.answer(text)
            await query.answer()



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


        # ----------------------
        # Моя статистика кусаний
        # ----------------------
        elif action == "bite_stats":

            logging.info("Вызов статистики куся")

            # Инициализация тела (создаёт записи в user_body_parts, если отсутствуют)
            ensure_user_body_parts(user_id, chat_id)

            # Получаем статистику
            last7 = get_last_7_daily_bites(user_id, chat_id)  # возвращает список словарей
            total = get_total_bites(user_id, chat_id) or {}  # <-- исправлено здесь

            # Суммируем за последние 7 дней
            given_last7 = sum(day.get('bites_given', 0) for day in last7)
            received_last7 = sum(day.get('bites_received', 0) for day in last7)

            # Берём общую статистику
            given_total = total.get('bites_given', 0)
            received_total = total.get('bites_received', 0)

            # Формируем текст сообщения
            text = (
                "🦷 Статистика укусов:\n"
                f"За последние 7 дней:\n"
                f"— Укусил: {given_last7} раз(а) (всего: {given_total})\n"
                f"— Был укушен: {received_last7} раз(а) (всего: {received_total})\n\n"
                f"{format_user_body_status(user_id, chat_id)}"
            )

            # Отправляем сообщение
            await query.message.answer(text)
            await query.answer()

        await query.answer()
