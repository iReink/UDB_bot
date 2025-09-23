# daily.py
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import List

from aiogram import types, Bot, Dispatcher
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.command import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters.callback_data import CallbackData

DB_PATH = "stats.db"
admin_ids = [6010666986, 884940984, 749027951]

# Словарь для блокировки кнопки создания нового дейлика
active_creators = {}

# ==========================
# FSM
# ==========================
class CreateDaily(StatesGroup):
    name = State()
    description = State()
    datetime = State()
    link = State()
    cars = State()

# ==========================
# УТИЛИТЫ
# ==========================
def format_daily_text(daily: dict, participants: List[dict]) -> str:
    dt_obj = datetime.strptime(f"{daily['date']} {daily['time']}", "%Y-%m-%d %H:%M")
    date_str = dt_obj.strftime("%d.%m")

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
        f"📆 {date_str} {daily['time']}. До него осталось: {remaining}",
        "",
        f"🎉 <b>{daily['name']}</b> — {daily['description']}"
    ]

    if daily.get('link'):
        lines.append(f'<a href="{daily["link"]}">Информация</a>')

    lines.append("")
    names = [p['name'] for p in participants]
    lines.append("👨‍👩‍👦‍👦 Участвуют: " + (", ".join(names) if names else "никого"))

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
# CALLBACKS FSM
# ==========================
class CarsCallback(CallbackData, prefix="cars"):
    choice: str

# ==========================
# ОБРАБОТЧИКИ
# ==========================
def register_daily_handlers(dp: Dispatcher):
    # ==========================
    # FSM для создания дейлика
    # ==========================
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import StatesGroup, State
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

    class DailyCreation(StatesGroup):
        name = State()
        description = State()
        datetime = State()
        link = State()
        cars = State()


    # ==========================
    # FSM для создания дейлика
    # ==========================
    class DailyCreation(StatesGroup):
        name = State()
        description = State()
        datetime = State()
        link = State()
        cars = State()

    # Inline-кнопка отмены для FSM
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    cancel_inline_kb = InlineKeyboardBuilder()
    cancel_inline_kb.add(
        InlineKeyboardButton(text="❌ Отмена", callback_data="daily_cancel")
    )

    # Inline-кнопки для выбора машины с отменой
    cars_kb = InlineKeyboardBuilder()
    cars_kb.row(
        InlineKeyboardButton(text="Да", callback_data="daily_cars_yes"),
        InlineKeyboardButton(text="Нет", callback_data="daily_cars_no"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="daily_cancel")
    )

    @dp.callback_query(lambda c: c.data == "daily_new_daily")
    async def start_daily_creation(query: types.CallbackQuery, state: FSMContext):
        user_id = query.from_user.id
        chat_id = query.message.chat.id

        # Блокируем кнопку для пользователя
        await query.answer("Начинаем создание дейлика! Следуйте инструкциям. ❗️", show_alert=True)

        await query.message.answer(
            "Введите название дейлика:",
            reply_markup=cancel_inline_kb.as_markup()
        )
        await state.set_state(DailyCreation.name)

    # Обработка всех текстовых сообщений в FSM
    from aiogram.filters.state import StateFilter
    @dp.message(StateFilter(DailyCreation.name), lambda m: m.text != "❌ Отмена")
    async def process_name(message: types.Message, state: FSMContext):
        await state.update_data(name=message.text)
        await message.answer(
            "Введите описание дейлика:",
            reply_markup=cancel_inline_kb.as_markup()
        )
        await state.set_state(DailyCreation.description)

    from aiogram.filters.state import StateFilter
    @dp.message(lambda m: m.text != "❌ Отмена", StateFilter(DailyCreation.description))
    async def process_description(message: types.Message, state: FSMContext):
        await state.update_data(description=message.text)
        await message.answer(
            "Введите дату и время (ДД.ММ ЧЧ:ММ):",
            reply_markup=cancel_inline_kb.as_markup()
        )
        await state.set_state(DailyCreation.datetime)

    @dp.message(lambda m: m.text != "❌ Отмена", StateFilter(DailyCreation.datetime))
    async def process_datetime(message: types.Message, state: FSMContext):
        try:
            dt = datetime.strptime(message.text, "%d.%m %H:%M")
            now = datetime.now()
            # Если дата раньше текущей, переносим на следующий год
            if dt.replace(year=now.year) < now:
                dt = dt.replace(year=now.year + 1)
            else:
                dt = dt.replace(year=now.year)
            await state.update_data(datetime=dt)
        except ValueError:
            await message.answer("Неверный формат. Введите дату и время в формате ДД.ММ ЧЧ:ММ, например 17.07 19:00")
            return
        await message.answer(
            "Введите ссылку на информацию или '-' если нет:",
            reply_markup=cancel_inline_kb.as_markup()
        )
        await state.set_state(DailyCreation.link)

    @dp.message(lambda m: m.text != "❌ Отмена", StateFilter(DailyCreation.link))
    async def process_link(message: types.Message, state: FSMContext):
        text = message.text.strip()
        if text in ("-", "–", "—"):
            link = None
        else:
            link = text
        await state.update_data(link=link)
        await message.answer("Нужно ли ехать на машине?", reply_markup=cars_kb.as_markup())
        await state.set_state(DailyCreation.cars)

    # Обработка кнопок Да/Нет про машину
    @dp.callback_query(lambda c: c.data in ("daily_cars_yes", "daily_cars_no"), StateFilter(DailyCreation.cars))
    async def process_cars(query: types.CallbackQuery, state: FSMContext):
        cars = "да" if query.data == "daily_cars_yes" else "нет"
        await state.update_data(cars=cars)

        data = await state.get_data()
        chat_id = query.message.chat.id

        # Сохраняем в базу
        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO daily_events (chat_id, name, description, date, time, link, cars, creator_user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chat_id,
                    data['name'],
                    data['description'],
                    data['datetime'].strftime("%Y-%m-%d"),
                    data['datetime'].strftime("%H:%M"),
                    data['link'],
                    cars,
                    query.from_user.id
                )
            )
            conn.commit()
            daily_id = cur.lastrowid

        await query.message.answer("📆 Дейлик создан!", reply_markup=types.ReplyKeyboardRemove())

        # Показываем дейлик как для обычного /daily
        participants = get_daily_participants(daily_id, chat_id)
        text = format_daily_text({
            "id": daily_id,
            "name": data['name'],
            "description": data['description'],
            "date": data['datetime'].strftime("%Y-%m-%d"),
            "time": data['datetime'].strftime("%H:%M"),
            "link": data['link'],
            "cars": cars
        }, participants)

        kb = daily_buttons(query.from_user.id, daily_id, cars, participants)
        await query.message.answer(text, reply_markup=kb, parse_mode="HTML")

        await state.clear()
        await query.answer("Дейлик успешно создан ✅")

    # Обработка отмены FSM
    @dp.callback_query(lambda c: c.data == "daily_cancel", StateFilter("*"))
    async def cancel_daily_creation(query: types.CallbackQuery, state: FSMContext):
        await state.clear()
        await query.message.edit_text(
            "Создание дейлика отменено ❌",
            reply_markup=None
        )
        await query.answer()

    @dp.message(Command("daily"))
    async def daily_menu(message: types.Message):
        chat_id = message.chat.id
        user_id = message.from_user.id

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
        await message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")

    # ==========================
    # CALLBACK-ОБРАБОТЧИКИ
    # ==========================
    @dp.callback_query(lambda c: c.data.startswith("daily_"))
    async def daily_callback(query: types.CallbackQuery, state: FSMContext):
        chat_id = query.message.chat.id
        user_id = query.from_user.id
        data = query.data

        def get_daily(daily_id: int) -> dict | None:
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM daily_events WHERE id = ?", (daily_id,))
                row = cur.fetchone()
                if row:
                    return dict(zip([column[0] for column in cur.description], row))
                return None

        async def refresh_message(daily_id: int):
            daily = get_daily(daily_id)
            participants = get_daily_participants(daily_id, chat_id)
            text = format_daily_text(daily, participants)
            kb = daily_buttons(user_id, daily_id, daily['cars'], participants)
            try:
                await query.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
            except:
                pass  # если сообщение не изменилось, пропускаем ошибку

        # ==========================
        # Работа с обычными кнопками
        # ==========================
        if data.startswith("daily_join:"):
            daily_id = int(data.split(":")[1])
            participants = get_daily_participants(daily_id, chat_id)
            if any(p['user_id'] == user_id for p in participants):
                await query.answer("Вы уже участвуете!")
                return
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO daily_participants(daily_id, user_id, is_driver) VALUES (?, ?, 0)",
                    (daily_id, user_id)
                )
                conn.commit()
            await refresh_message(daily_id)
            await query.answer("Вы присоединились к дейли! ✅")

        elif data.startswith("daily_leave:"):
            daily_id = int(data.split(":")[1])
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM daily_participants WHERE daily_id=? AND user_id=?",
                    (daily_id, user_id)
                )
                conn.commit()
            await refresh_message(daily_id)
            await query.answer("Вы отказались от участия ❌")

        elif data.startswith("daily_driver:") or data.startswith("daily_nodriver:"):
            daily_id = int(data.split(":")[1])
            participants = get_daily_participants(daily_id, chat_id)
            if not any(p['user_id'] == user_id for p in participants):
                await query.answer("Сначала нужно участвовать!", show_alert=True)
                return
            is_driver = data.startswith("daily_driver:")
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE daily_participants SET is_driver=? WHERE daily_id=? AND user_id=?",
                    (1 if is_driver else 0, daily_id, user_id)
                )
                conn.commit()
            await query.answer("Статус водителя обновлён!")
            await refresh_message(daily_id)

        elif data == "daily_my_dailies":
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT * FROM daily_events WHERE chat_id=? AND date || ' ' || time >= ?
                    ORDER BY date || ' ' || time ASC
                """, (chat_id, datetime.now().strftime("%Y-%m-%d %H:%M")))
                all_rows = cur.fetchall()

            await query.message.answer("<b>Список дейликов, в которых ты принимаешь участие</b>", parse_mode="HTML")
            for row in all_rows:
                daily = dict(zip([column[0] for column in cur.description], row))
                participants = get_daily_participants(daily['id'], chat_id)
                if any(p['user_id'] == user_id for p in participants):
                    text = format_daily_text(daily, participants)
                    kb = daily_buttons(user_id, daily['id'], daily['cars'], participants)
                    await query.message.answer(text, reply_markup=kb, parse_mode="HTML")

        elif data == "daily_all_dailies":
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT * FROM daily_events WHERE chat_id=? AND date || ' ' || time >= ?
                    ORDER BY date || ' ' || time ASC
                """, (chat_id, datetime.now().strftime("%Y-%m-%d %H:%M")))
                all_rows = cur.fetchall()

            await query.message.answer("<b>Список всех запланированных дейликов</b>", parse_mode="HTML")
            for row in all_rows:
                daily = dict(zip([column[0] for column in cur.description], row))
                participants = get_daily_participants(daily['id'], chat_id)
                text = format_daily_text(daily, participants)
                kb = daily_buttons(user_id, daily['id'], daily['cars'], participants)
                await query.message.answer(text, reply_markup=kb, parse_mode="HTML")

        # ==========================
        # FSM: создание нового дейлика
        # ==========================
        elif data == "daily_new_daily":
            # проверка блокировки
            if active_creators.get(chat_id):
                await query.answer("Кто-то уже создаёт дейлик, попробуйте позже.", show_alert=True)
                return
            active_creators[chat_id] = user_id

            await query.message.answer("Введите название дейлика:", reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Отмена")]],
                resize_keyboard=True,
                one_time_keyboard=True
            ))
            await state.set_state(CreateDaily.name)


        elif data == "daily_edit_daily":
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                # Админ видит все будущие дейлики
                if user_id in admin_ids:
                    cur.execute("""
                        SELECT * FROM daily_events
                        WHERE chat_id=? AND date || ' ' || time >= ?
                        ORDER BY date || ' ' || time ASC
                    """, (chat_id, datetime.now().strftime("%Y-%m-%d %H:%M")))
                else:
                    cur.execute("""
                        SELECT * FROM daily_events
                        WHERE creator_user_id=? AND chat_id=? AND date || ' ' || time >= ?
                        ORDER BY date || ' ' || time ASC
                    """, (user_id, chat_id, datetime.now().strftime("%Y-%m-%d %H:%M")))
                rows = cur.fetchall()
            if not rows:
                await query.answer("У вас нет дейликов для редактирования", show_alert=True)
                return
            await query.message.answer("<b>Все дейлики, доступные для редактирования</b>", parse_mode="HTML")
            for row in rows:
                daily = dict(zip([column[0] for column in cur.description], row))
                participants = get_daily_participants(daily['id'], chat_id)
                text = format_daily_text(daily, participants)
                kb = InlineKeyboardBuilder()
                kb.row(
                    InlineKeyboardButton(text="✏️ Изменить данные", callback_data=f"daily_edit:{daily['id']}"),
                    InlineKeyboardButton(text="🗑 Удалить", callback_data=f"daily_delete:{daily['id']}")
                )
                await query.message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")

        elif data.startswith("daily_edit:"):
            daily_id = int(data.split(":")[1])
            # Заглушка для редактирования
            await query.answer("Редактирование пока не реализовано", show_alert=True)

        elif data.startswith("daily_delete:"):
            daily_id = int(data.split(":")[1])
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT name FROM daily_events WHERE id=?", (daily_id,))
                row = cur.fetchone()
            if not row:
                await query.answer("Дейлик уже удалён", show_alert=True)
                return

            daily_name = row[0]
            kb = InlineKeyboardBuilder()
            kb.row(
                InlineKeyboardButton(text="✅ Да", callback_data=f"daily_confirm_delete:{daily_id}"),
                InlineKeyboardButton(text="❌ Нет", callback_data="daily_cancel_delete")
            )
            await query.message.answer(f"Уверен, что хочешь удалить дейлик <b>{daily_name}</b>?",
                                       reply_markup=kb.as_markup(), parse_mode="HTML")

        elif data.startswith("daily_confirm_delete:"):
            daily_id = int(data.split(":")[1])
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT name FROM daily_events WHERE id=?", (daily_id,))
                row = cur.fetchone()
                if row:
                    daily_name = row[0]
                    cur.execute("DELETE FROM daily_events WHERE id=?", (daily_id,))
                    cur.execute("DELETE FROM daily_participants WHERE daily_id=?", (daily_id,))
                    conn.commit()
                    await query.message.edit_text(f"Дейлик <b>{daily_name}</b> удалён", parse_mode="HTML")
                else:
                    await query.answer("Дейлик уже удалён", show_alert=True)

        elif data == "daily_cancel_delete":
            await query.message.delete()
            await query.answer()