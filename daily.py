# daily.py
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import List

from aiogram import types, Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.command import Command
from aiogram import Dispatcher

DB_PATH = "stats.db"
admin_ids = [6010666986, 884940984, 749027951]


# ==========================
# УТИЛИТЫ
# ==========================
def format_daily_text(daily: dict, participants: List[dict]) -> str:
    # формат даты
    dt_obj = datetime.strptime(f"{daily['date']} {daily['time']}", "%Y-%m-%d %H:%M")
    date_str = dt_obj.strftime("%d.%m")  # только день и месяц

    # сколько осталось
    delta = dt_obj - datetime.now()
    total_seconds = int(delta.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    if days > 0:
        remaining = f"{days}д {hours}ч"
    elif hours > 0:
        remaining = f"{hours}ч {minutes}м"
    else:
        remaining = f"{minutes}м"

    lines = [
        f"{date_str} {daily['time']}. До него осталось: {remaining}",
        "",
        f"<b>{daily['name']}</b> — {daily['description']}"
    ]

    if daily.get("link"):
        lines.append(f"[Информация]({daily['link']})")

    lines.append("")  # пустая строка перед участниками

    names = [p['name'] for p in participants]
    lines.append("Участвуют: " + (", ".join(names) if names else "никого"))

    if daily['cars'] in ('да', '1'):
        num_participants = len(participants)
        num_drivers = sum(1 for p in participants if p['is_driver'])
        capacity = num_drivers * 5
        if num_participants > capacity:
            lines.append(f"\n⛔️ Не хватает машин! Участников {num_participants}, а мест только для {capacity}")

    return "\n".join(lines)



def get_daily_participants(daily_id: int, chat_id: int) -> List[dict]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT u.user_id, u.name, p.is_driver
            FROM daily_participants p
            JOIN users u ON u.user_id = p.user_id AND u.chat_id = ?
            WHERE p.daily_id = ?
        """, (chat_id, daily_id))
        rows = cur.fetchall()
    return [{'user_id': r[0], 'name': r[1], 'is_driver': bool(r[2])} for r in rows]


def daily_buttons(user_id: int, daily_id: int, cars: str, participants: List[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    is_participant = any(p['user_id'] == user_id for p in participants)
    is_driver = any(p['user_id'] == user_id and p['is_driver'] for p in participants)

    if not is_participant:
        kb.add(InlineKeyboardButton(text="Участвовать", callback_data=f"daily_join:{daily_id}"))
    else:
        kb.add(InlineKeyboardButton(text="Не участвовать", callback_data=f"daily_leave:{daily_id}"))

    if cars in ('да', '1'):
        if not is_driver:
            kb.add(InlineKeyboardButton(text="Стать водителем с машиной", callback_data=f"daily_driver:{daily_id}"))
        else:
            kb.add(InlineKeyboardButton(text="Я не водитель с машиной", callback_data=f"daily_nodriver:{daily_id}"))

    return kb.as_markup()


# ==========================
# ОБРАБОТЧИКИ
# ==========================
def register_daily_handlers(dp: Dispatcher):
    @dp.message(Command("daily"))
    async def daily_menu(message: types.Message):
        chat_id = message.chat.id
        user_id = message.from_user.id

        # Ближайший дейлик
        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM daily_events
                WHERE chat_id = ? AND date || ' ' || time >= ?
                ORDER BY date || ' ' || time ASC
                LIMIT 1
            """, (chat_id, datetime.now().strftime("%Y-%m-%d %H:%M")))
            row = cur.fetchone()
            if not row:
                await message.answer("Ближайших дейли нет.")
                return
            daily = dict(zip([column[0] for column in cur.description], row))

        participants = get_daily_participants(daily['id'], chat_id)
        text = format_daily_text(daily, participants)

        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(text="🚩 Дейли, на которые я иду", callback_data="daily_my_dailies"),
            InlineKeyboardButton(text="📆 Все дейли", callback_data="daily_all_dailies")
        )
        kb.row(
            InlineKeyboardButton(text="👾 Создать новый дейлик", callback_data="daily_new_daily"),
            InlineKeyboardButton(text="✍️ Редактировать свой дейлик", callback_data="daily_edit_daily")
        )
        await message.answer(text, reply_markup=kb.as_markup())

    # ==========================
    # CALLBACK-ОБРАБОТЧИКИ
    # ==========================
    @dp.callback_query(lambda c: c.data.startswith("daily_"))
    async def daily_callback(query: types.CallbackQuery):
        chat_id = query.message.chat.id
        user_id = query.from_user.id
        data = query.data

        # Получение дейлика
        def get_daily(daily_id: int) -> dict | None:
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM daily_events WHERE id = ?", (daily_id,))
                row = cur.fetchone()
                if row:
                    return dict(zip([column[0] for column in cur.description], row))
                return None

        if data.startswith("daily_join:"):
            daily_id = int(data.split(":")[1])
            participants = get_daily_participants(daily_id, chat_id)
            if any(p['user_id'] == user_id for p in participants):
                await query.answer("Вы уже участвуете!")
                return
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("INSERT INTO daily_participants(daily_id, user_id, is_driver) VALUES (?, ?, 0)",
                            (daily_id, user_id))
                conn.commit()
            await query.answer("Вы присоединились к дейли!")

        elif data.startswith("daily_leave:"):
            daily_id = int(data.split(":")[1])
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM daily_participants WHERE daily_id=? AND user_id=?",
                            (daily_id, user_id))
                conn.commit()
            await query.answer("Вы отказались от участия.")

        elif data.startswith("daily_driver:"):
            daily_id = int(data.split(":")[1])
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("UPDATE daily_participants SET is_driver=1 WHERE daily_id=? AND user_id=?",
                            (daily_id, user_id))
                conn.commit()
            await query.answer("Вы теперь водитель с машиной!")

        elif data.startswith("daily_nodriver:"):
            daily_id = int(data.split(":")[1])
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("UPDATE daily_participants SET is_driver=0 WHERE daily_id=? AND user_id=?",
                            (daily_id, user_id))
                conn.commit()
            await query.answer("Вы больше не водитель с машиной!")

        elif data == "daily_my_dailies":
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT * FROM daily_events WHERE chat_id=? AND date || ' ' || time >= ?
                    ORDER BY date || ' ' || time ASC
                """, (chat_id, datetime.now().strftime("%Y-%m-%d %H:%M")))
                all_rows = cur.fetchall()

            for row in all_rows:
                daily = dict(zip([column[0] for column in cur.description], row))
                participants = get_daily_participants(daily['id'], chat_id)
                if any(p['user_id'] == user_id for p in participants):
                    text = format_daily_text(daily, participants)
                    kb = daily_buttons(user_id, daily['id'], daily['cars'], participants)
                    await query.message.answer(text, reply_markup=kb)

        elif data == "daily_all_dailies":
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT * FROM daily_events WHERE chat_id=? AND date || ' ' || time >= ?
                    ORDER BY date || ' ' || time ASC
                """, (chat_id, datetime.now().strftime("%Y-%m-%d %H:%M")))
                all_rows = cur.fetchall()

            for row in all_rows:
                daily = dict(zip([column[0] for column in cur.description], row))
                participants = get_daily_participants(daily['id'], chat_id)
                text = format_daily_text(daily, participants)
                kb = daily_buttons(user_id, daily['id'], daily['cars'], participants)
                await query.message.answer(text, reply_markup=kb)

        elif data == "daily_new_daily":
            await query.answer("Заглушка: создание нового дейлика")

        elif data == "daily_edit_daily":
            # Проверка прав
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT COUNT(*) FROM daily_events
                    WHERE creator_user_id=? AND chat_id=? AND date || ' ' || time >= ?
                """, (user_id, chat_id, datetime.now().strftime("%Y-%m-%d %H:%M")))
                count = cur.fetchone()[0]
            if user_id in admin_ids or count > 0:
                await query.answer("Кнопка нажата")
            else:
                await query.answer("У вас нет дейликов, которые можно редактировать", show_alert=True)
