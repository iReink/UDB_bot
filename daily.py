# daily.py
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import List
import re
import pytz # Добавляем импорт pytz
import asyncio # Добавляем импорт asyncio для create_task

from aiogram import types, Bot, Dispatcher
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.command import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters.callback_data import CallbackData
from aiogram import types
from aiogram.filters import Command
from contextlib import closing
import sqlite3
from db import get_user # Импортирую get_user для получения ника
from google_calendar_integration import create_calendar_event, update_calendar_event, delete_calendar_event, TARGET_CHAT_ID # Импорт для интеграции с Google Calendar

DB_PATH = "stats.db"
admin_ids = [6010666986, 884940984, 749027951]

# Глобальный экземпляр бота для использования в асинхронных функциях
bot_instance: Bot = None

# Словарь для блокировки кнопки создания нового дейлика
active_creators = {}

# --- Состояния редактирования ---
class EditDailyStates(StatesGroup):
    edit_name = State()
    edit_description = State()
    edit_datetime = State()
    edit_link = State()
    edit_cars = State()

# --- Клавиатура для редактирования дейлика ---
def get_edit_daily_keyboard(daily_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅ Назад", callback_data=f"daily_manage_back:{daily_id}"))
    kb.row(InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"edit_name:{daily_id}"))
    kb.row(InlineKeyboardButton(text="📝 Изменить описание", callback_data=f"edit_desc:{daily_id}"))
    kb.row(InlineKeyboardButton(text="📅 Изменить дату и время", callback_data=f"edit_dt:{daily_id}"))
    kb.row(InlineKeyboardButton(text="🔗 Изменить ссылку", callback_data=f"edit_link:{daily_id}"))
    kb.row(InlineKeyboardButton(text="🚗 Изменить машины", callback_data=f"edit_cars:{daily_id}"))
    return kb.as_markup()



def get_manage_daily_keyboard(daily_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✏️ Изменить данные", callback_data=f"daily_edit:{daily_id}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"daily_delete:{daily_id}")
    )
    return kb.as_markup()


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
def format_daily_text(daily: dict, participants: List[dict], with_turbo_link: bool = False) -> str:
    dt_obj = datetime.strptime(f"{daily['date']} {daily['time']}", "%Y-%m-%d %H:%M")
    date_str = dt_obj.strftime("%d.%m")

    WEEKDAYS = {
        0: "ПН",
        1: "ВТ",
        2: "СР",
        3: "ЧТ",
        4: "ПТ",
        5: "СБ",
        6: "ВС",
    }
    weekday_str = WEEKDAYS[dt_obj.weekday()]  # 0 = ПН ... 6 = ВС

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
        f"📆 {date_str} ({weekday_str}) {daily['time']}. Осталось: {remaining}",
        "",
        f"🎉 <b>{daily['name']}</b> — {daily['description']}"
    ]

    if daily.get('link'):
        lines.append(f'<a href="{daily["link"]}">Информация</a>')

    # Добавляем турбо-ссылку, если нужно
    if with_turbo_link and daily.get("id"):
        lines.append(f"\n🚀 Присоединиться одним кликом: /daily_{daily['id']}")

    lines.append("")

    participants_names = [p["name"] for p in participants if not p.get("is_driver")]
    drivers_names = [p["name"] for p in participants if p.get("is_driver")]

    lines.append(
        "👨‍👩‍👦‍👦 Участвуют: " +
        (", ".join(participants_names) if participants_names else "никого")
    )

    if drivers_names:
        lines.append("🚗 Водители: " + ", ".join(drivers_names))

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

    # Универсальная кнопка участия
    kb.row(
        InlineKeyboardButton(
            text="👋 Присоединиться / Отказаться",
            callback_data=f"daily_toggle_participation:{daily_id}"
        )
    )

    # Универсальная кнопка водителя (если машины нужны)
    if cars in ('да', '1'):
        kb.row(
            InlineKeyboardButton(
                text="🚗 Я водитель / Я не водитель",
                callback_data=f"daily_toggle_driver:{daily_id}"
            )
        )
    
    # Кнопка "Тегнуть участников"
    kb.row(
        InlineKeyboardButton(
            text="Тегнуть участников",
            callback_data=f"daily_tag_participants:{daily_id}"
        )
    )

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
    # Удаляю глобальный bot_instance и второй аргумент
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

    # --- Назад (возвращаем клавиатуру в предыдущее состояние) ---
    @dp.callback_query(lambda c: c.data.startswith("edit_back:"))
    async def edit_back_handler(callback: types.CallbackQuery):
        daily_id = int(callback.data.split(":")[1])
        # тут возвращаешь старую клавиатуру (которая была с "Изменить данные" и "Удалить")
        await callback.message.edit_reply_markup(reply_markup=get_manage_daily_keyboard(daily_id))
        await callback.answer()

    # --- Изменить название ---
    @dp.callback_query(lambda c: c.data.startswith("edit_name:"))
    async def edit_name_handler(callback: types.CallbackQuery, state: FSMContext):
        daily_id = int(callback.data.split(":")[1])
        await state.update_data(daily_id=daily_id)
        await state.set_state(EditDailyStates.edit_name)
        await callback.message.answer("✏ Введите новое название:")
        await callback.answer()

    @dp.message(EditDailyStates.edit_name)
    async def process_edit_name(message: types.Message, state: FSMContext):
        data = await state.get_data()
        daily_id = data["daily_id"]

        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute("UPDATE daily_events SET name=? WHERE id=?", (message.text.strip(), daily_id))
            conn.commit()

            # После обновления в БД, обновляем событие в Google Календаре
            cur.execute("SELECT * FROM daily_events WHERE id = ?", (daily_id,))
            daily = dict(zip([col[0] for col in cur.description], cur.fetchone()))

            if daily.get('calendar_event_id'):
                asyncio.create_task(update_calendar_event(
                    calendar_event_id=daily['calendar_event_id'],
                    chat_id=daily['chat_id'],
                    daily_name=message.text.strip(), # Новое название
                    daily_description=daily['description'],
                    daily_datetime=pytz.timezone('Asia/Yekaterinburg').localize(datetime.strptime(f"{daily['date']} {daily['time']}", "%Y-%m-%d %H:%M")),
                    daily_link=daily['link'],
                    daily_id=daily_id,
                    bot_instance=message.bot
                ))

        await message.answer("✅ Название изменено")
        await state.clear()

    @dp.callback_query(lambda c: c.data.startswith("edit_desc:"))
    async def edit_desc_handler(callback: types.CallbackQuery, state: FSMContext):
        daily_id = int(callback.data.split(":")[1])
        await state.update_data(daily_id=daily_id)
        await state.set_state(EditDailyStates.edit_description)
        await callback.message.answer("✏ Введите новое описание:")
        await callback.answer()

    @dp.message(EditDailyStates.edit_description)
    async def process_edit_description(message: types.Message, state: FSMContext):
        data = await state.get_data()
        daily_id = data["daily_id"]

        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute("UPDATE daily_events SET description=? WHERE id=?", (message.text.strip(), daily_id))
            conn.commit()

            # После обновления в БД, обновляем событие в Google Календаре
            cur.execute("SELECT * FROM daily_events WHERE id = ?", (daily_id,))
            daily = dict(zip([col[0] for col in cur.description], cur.fetchone()))

            if daily.get('calendar_event_id'):
                asyncio.create_task(update_calendar_event(
                    calendar_event_id=daily['calendar_event_id'],
                    chat_id=daily['chat_id'],
                    daily_name=daily['name'],
                    daily_description=message.text.strip(), # Новое описание
                    daily_datetime=pytz.timezone('Asia/Yekaterinburg').localize(datetime.strptime(f"{daily['date']} {daily['time']}", "%Y-%m-%d %H:%M")),
                    daily_link=daily['link'],
                    daily_id=daily_id,
                    bot_instance=message.bot
                ))

        await message.answer("✅ Описание изменено")
        await state.clear()

    @dp.callback_query(lambda c: c.data.startswith("edit_dt:"))
    async def edit_dt_handler(callback: types.CallbackQuery, state: FSMContext):
        daily_id = int(callback.data.split(":")[1])
        await state.update_data(daily_id=daily_id)
        await state.set_state(EditDailyStates.edit_datetime)
        await callback.message.answer("🕒 Введи новые дату и время в формате дд.мм чч:мм")
        await callback.answer()

    @dp.message(EditDailyStates.edit_datetime)
    async def process_edit_datetime(message: types.Message, state: FSMContext):
        try:
            # Парсим ввод без года
            dt_naive = datetime.strptime(message.text.strip(), "%d.%m %H:%M")

            tz = pytz.timezone('Asia/Yekaterinburg')
            now_aware = datetime.now(tz)

            # Определяем год
            dt_candidate_aware = tz.localize(dt_naive.replace(year=now_aware.year))

            if dt_candidate_aware < now_aware:
                # Если дата с текущим годом в прошлом, устанавливаем следующий год
                dt_final_aware = tz.localize(dt_naive.replace(year=now_aware.year + 1))
            else:
                dt_final_aware = dt_candidate_aware

            await state.update_data(datetime=dt_final_aware)
        except ValueError:
            await message.answer("⚠ Неверный формат! Используй: дд.мм чч:мм")
            return

        data = await state.get_data()
        daily_id = data["daily_id"]

        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE daily_events SET date=?, time=? WHERE id=?",
                (dt_final_aware.strftime("%Y-%m-%d"), dt_final_aware.strftime("%H:%M"), daily_id)
            )
            conn.commit()

            # После обновления в БД, обновляем событие в Google Календаре
            cur.execute("SELECT * FROM daily_events WHERE id = ?", (daily_id,))
            daily = dict(zip([col[0] for col in cur.description], cur.fetchone()))

            if daily.get('calendar_event_id'):
                asyncio.create_task(update_calendar_event(
                    calendar_event_id=daily['calendar_event_id'],
                    chat_id=daily['chat_id'],
                    daily_name=daily['name'],
                    daily_description=daily['description'],
                    daily_datetime=dt_final_aware, # Новая дата и время (уже aware)
                    daily_link=daily['link'],
                    daily_id=daily_id,
                    bot_instance=message.bot
                ))

        await message.answer("✅ Дата и время изменены")
        await state.clear()

    @dp.callback_query(lambda c: c.data.startswith("edit_link:"))
    async def edit_link_handler(callback: types.CallbackQuery, state: FSMContext):
        daily_id = int(callback.data.split(":")[1])
        await state.update_data(daily_id=daily_id)
        await state.set_state(EditDailyStates.edit_link)
        await callback.message.answer("🔗 Введи новую ссылку на инфу или прочерк (-):")
        await callback.answer()

    @dp.message(EditDailyStates.edit_link)
    async def process_edit_link(message: types.Message, state: FSMContext):
        link = message.text.strip()
        if link in ["-", "–", "—", "−"]:
            link = None

        data = await state.get_data()
        daily_id = data["daily_id"]

        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute("UPDATE daily_events SET link=? WHERE id=?", (link, daily_id))
            conn.commit()

            # После обновления в БД, обновляем событие в Google Календаре
            cur.execute("SELECT * FROM daily_events WHERE id = ?", (daily_id,))
            daily = dict(zip([col[0] for col in cur.description], cur.fetchone()))

            if daily.get('calendar_event_id'):
                asyncio.create_task(update_calendar_event(
                    calendar_event_id=daily['calendar_event_id'],
                    chat_id=daily['chat_id'],
                    daily_name=daily['name'],
                    daily_description=daily['description'],
                    daily_datetime=pytz.timezone('Asia/Yekaterinburg').localize(datetime.strptime(f"{daily['date']} {daily['time']}", "%Y-%m-%d %H:%M")),
                    daily_link=link, # Новая ссылка
                    daily_id=daily_id,
                    bot_instance=message.bot
                ))

        await message.answer("✅ Ссылка изменена")
        await state.clear()


    @dp.callback_query(lambda c: c.data.startswith("edit_cars:"))
    async def edit_cars_handler(callback: types.CallbackQuery):
        daily_id = int(callback.data.split(":")[1])
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Да", callback_data=f"cars_yes:{daily_id}")],
            [InlineKeyboardButton(text="Нет", callback_data=f"cars_no:{daily_id}")]
        ])
        await callback.message.answer("🚗 Нужно ли ехать на машинах?", reply_markup=kb)
        await callback.answer()

    @dp.callback_query(lambda c: c.data.startswith("cars_yes:") or c.data.startswith("cars_no:")) # Исправлен фильтр callback_data
    async def cars_choice_handler(callback: types.CallbackQuery):
        daily_id = int(callback.data.split(":")[1])
        cars_value = "да" if callback.data.startswith("cars_yes") else "нет"

        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute("UPDATE daily_events SET cars=? WHERE id=?", (cars_value, daily_id))
            conn.commit()

            # После обновления в БД, обновляем событие в Google Календаре
            cur.execute("SELECT * FROM daily_events WHERE id = ?", (daily_id,))
            daily = dict(zip([col[0] for col in cur.description], cur.fetchone()))

            if daily.get('calendar_event_id'):
                asyncio.create_task(update_calendar_event(
                    calendar_event_id=daily['calendar_event_id'],
                    chat_id=daily['chat_id'],
                    daily_name=daily['name'],
                    daily_description=daily['description'],
                    daily_datetime=pytz.timezone('Asia/Yekaterinburg').localize(datetime.strptime(f"{daily['date']} {daily['time']}", "%Y-%m-%d %H:%M")),
                    daily_link=daily['link'],
                    daily_id=daily_id,
                    bot_instance=callback.bot
                ))

        # Редактируем сообщение: убираем кнопки и показываем подтверждение
        try:
            await callback.message.edit_text(
                f"🚗 Настройка машин обновлена: <b>{cars_value}</b>",
                reply_markup=None, # Убираем кнопки
                parse_mode="HTML"
            )
        except Exception as e:
            # Возможно, сообщение уже было изменено или удалено
            print(f"Ошибка при редактировании сообщения о машинах: {e}")

        await callback.answer(f"✅ Настройка обновлена: машины = {cars_value}")

    @dp.message(lambda m: m.text and m.text.startswith("/daily_"))
    async def daily_go_command(message: types.Message):
        user_id = message.from_user.id
        chat_id = message.chat.id

        # Убираем суффикс @username, если есть
        command_text = message.text.split('@')[0]  # /daily_11

        # Извлекаем ID дейлика
        match = re.match(r"^/daily_(\d+)$", command_text)
        if not match:
            return  # если вдруг формат некорректный

        daily_id = int(match.group(1))

        # Работа с БД
        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()

            # Проверяем, есть ли такой дейлик
            cur.execute("SELECT name FROM daily_events WHERE id=?", (daily_id,))
            row = cur.fetchone()
            if not row:
                await message.answer("Дейлик с таким ID не найден")
                return
            daily_name = row[0]

            # Проверяем, участвует ли пользователь
            cur.execute(
                "SELECT 1 FROM daily_participants WHERE daily_id=? AND user_id=?",
                (daily_id, user_id)
            )
            if cur.fetchone():
                await message.answer(f"Вы уже участвуете в дейлике {daily_name}", show_alert=True)
                return

            # Добавляем пользователя в участники
            cur.execute(
                "INSERT INTO daily_participants(daily_id, user_id, is_driver) VALUES (?, ?, 0)",
                (daily_id, user_id)
            )
            conn.commit()

        await message.answer(f"Вы успешно присоединились к дейлику {daily_name} ✅", show_alert=True)
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
            dt_naive = datetime.strptime(message.text, "%d.%m %H:%M")

            tz = pytz.timezone('Asia/Yekaterinburg')
            now_aware = datetime.now(tz)

            dt_candidate_aware = tz.localize(dt_naive.replace(year=now_aware.year))

            if dt_candidate_aware < now_aware:
                dt_final_aware = tz.localize(dt_naive.replace(year=now_aware.year + 1))
            else:
                dt_final_aware = dt_candidate_aware
            await state.update_data(datetime=dt_final_aware)
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

        calendar_event_id = None
        # --- Интеграция с Google Календарем ---
        # Запускаем создание события в календаре в фоновом режиме
        event_id = await create_calendar_event(
            chat_id=chat_id,
            daily_name=data['name'],
            daily_description=data['description'],
            daily_datetime=data['datetime'],
            daily_link=data['link'],
            daily_id=daily_id,
            bot_instance=query.bot  # Передаем экземпляр бота
        )

        if event_id:
            calendar_event_id = event_id
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("UPDATE daily_events SET calendar_event_id = ? WHERE id = ?", (calendar_event_id, daily_id))
                conn.commit()

        await query.message.answer("📆 Дейлик создан!", reply_markup=types.ReplyKeyboardRemove(), disable_web_page_preview=True)

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
        }, participants, with_turbo_link=True)

        kb = daily_buttons(query.from_user.id, daily_id, cars, participants)
        await query.message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)

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
        now_dt = datetime.now()
        today_str = now_dt.strftime("%Y-%m-%d")

        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM daily_events
                WHERE chat_id = ? AND date >= ?
                ORDER BY date || ' ' || time ASC
            """, (chat_id, today_str))
            rows = cur.fetchall()
            if not rows:
                keyboard = InlineKeyboardBuilder()
                keyboard.add(
                    InlineKeyboardButton(text="👾 Создать новый дейлик", callback_data="daily_new_daily")
                )
                await message.answer("Запланированных дейли нет.", reply_markup=keyboard.as_markup())
                return

            columns = [column[0] for column in cur.description]
            dailies = [dict(zip(columns, row)) for row in rows]

        today_dailies = [d for d in dailies if d["date"] == today_str]
        if today_dailies:
            upcoming_today = []
            for d in today_dailies:
                d_dt = datetime.strptime(f"{d['date']} {d['time']}", "%Y-%m-%d %H:%M")
                if d_dt >= now_dt:
                    upcoming_today.append((d_dt, d))
            if upcoming_today:
                daily = min(upcoming_today, key=lambda item: item[0])[1]
            else:
                daily = max(
                    today_dailies,
                    key=lambda item: datetime.strptime(
                        f"{item['date']} {item['time']}", "%Y-%m-%d %H:%M"
                    ),
                )
        else:
            daily = dailies[0]

        dt_obj = datetime.strptime(f"{daily['date']} {daily['time']}", "%Y-%m-%d %H:%M")
        date_str = dt_obj.strftime("%d.%m")
        time_str = dt_obj.strftime("%H:%M")
        weekdays = {
            0: "ПН",
            1: "ВТ",
            2: "СР",
            3: "ЧТ",
            4: "ПТ",
            5: "СБ",
            6: "ВС",
        }
        weekday_str = weekdays[dt_obj.weekday()]

        total_seconds = max(0, int((dt_obj - now_dt).total_seconds()))
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes = remainder // 60
        if days > 0:
            remaining = f"{days}д {hours}ч"
        elif hours > 0:
            remaining = f"{hours}ч {minutes}м"
        else:
            remaining = f"{minutes}м"
        turbo_join = f"/daily_{daily['id']}"

        text = (
            f"Ближайший дейли — <b>{daily['name']}</b>\n"
            f"📆 {date_str} ({weekday_str}) {time_str}. Осталось: {remaining}\n"
            f"Для присоединения нажми на {turbo_join}\n\n"
            "Для полной информации открой список по кнопкам ниже."
        )

        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(text="🚩 Дейли, на которые я иду", callback_data="daily_my_dailies")
        )
        kb.row(
            InlineKeyboardButton(text="📆 Все дейли", callback_data="daily_all_dailies")
        )
        kb.row(
            InlineKeyboardButton(text="👾 Создать новый дейлик", callback_data="daily_new_daily")
        )
        kb.row(
            InlineKeyboardButton(text="✍️ Редактировать дейлик", callback_data="daily_edit_daily")
        )
        await message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML", disable_web_page_preview=True)

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
                await query.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
            except:
                pass  # если сообщение не изменилось, пропускаем ошибку

        # ==========================
        # Работа с обычными кнопками
        # ==========================
        if data.startswith("daily_toggle_participation:"):
            daily_id = int(data.split(":")[1])
            participants = get_daily_participants(daily_id, chat_id)
            if any(p['user_id'] == user_id for p in participants):
                # Удаляем участие
                with closing(sqlite3.connect(DB_PATH)) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "DELETE FROM daily_participants WHERE daily_id=? AND user_id=?",
                        (daily_id, user_id)
                    )
                    conn.commit()
                await query.answer("Вы отказались от участия ❌")
            else:
                # Добавляем участие
                with closing(sqlite3.connect(DB_PATH)) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO daily_participants(daily_id, user_id, is_driver) VALUES (?, ?, 0)",
                        (daily_id, user_id)
                    )
                    conn.commit()
                await query.answer("Вы присоединились к дейли! ✅")
            await refresh_message(daily_id)

        elif data.startswith("daily_toggle_driver:"):
            daily_id = int(data.split(":")[1])
            participants = get_daily_participants(daily_id, chat_id)
            if not any(p['user_id'] == user_id for p in participants):
                await query.answer("Сначала нужно участвовать!", show_alert=True)
                return
            is_driver = next((p['is_driver'] for p in participants if p['user_id'] == user_id), False)
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE daily_participants SET is_driver=? WHERE daily_id=? AND user_id=?",
                    (0 if is_driver else 1, daily_id, user_id)
                )
                conn.commit()
            await query.answer("Статус водителя обновлён!")
            await refresh_message(daily_id)


        elif data == "daily_my_dailies":
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT * FROM daily_events WHERE chat_id=? AND date >= ?
                    ORDER BY date || ' ' || time ASC
                """, (chat_id, datetime.now().strftime("%Y-%m-%d")))
                all_rows = cur.fetchall()

            await query.message.answer("<b>Список дейликов, в которых ты принимаешь участие</b>", parse_mode="HTML")
            for row in all_rows:
                daily = dict(zip([column[0] for column in cur.description], row))
                participants = get_daily_participants(daily['id'], chat_id)
                if any(p['user_id'] == user_id for p in participants):
                    text = format_daily_text(daily, participants)
                    kb = daily_buttons(user_id, daily['id'], daily['cars'], participants)
                    await query.message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)

        elif data == "daily_all_dailies":
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT * FROM daily_events WHERE chat_id=? AND date >= ?
                    ORDER BY date || ' ' || time ASC
                """, (chat_id, datetime.now().strftime("%Y-%m-%d")))
                all_rows = cur.fetchall()

            await query.message.answer("<b>Список всех запланированных дейликов</b>", parse_mode="HTML")
            for row in all_rows:
                daily = dict(zip([column[0] for column in cur.description], row))
                participants = get_daily_participants(daily['id'], chat_id)
                text = format_daily_text(daily, participants)
                kb = daily_buttons(user_id, daily['id'], daily['cars'], participants)
                await query.message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


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
                await query.message.answer(
                    text,
                    reply_markup=get_manage_daily_keyboard(daily['id']),
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )

        elif data.startswith("daily_edit:"):
            daily_id = int(data.split(":")[1])
            # Проверка прав
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT creator_user_id FROM daily_events WHERE id=? AND chat_id=?", (daily_id, chat_id))
                row = cur.fetchone()
            if not row:
                await query.answer("Дейлик не найден", show_alert=True)
                return
            creator_id = row[0]
            if user_id != creator_id and user_id not in admin_ids:
                await query.answer("У вас нет прав для редактирования этого дейлика", show_alert=True)
                return
            # Меняем клавиатуру под сообщением
            await query.message.edit_reply_markup(reply_markup=get_edit_daily_keyboard(daily_id))

        elif data.startswith("daily_manage_back:"):
            daily_id = int(data.split(":")[1])

            # Проверка прав
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT creator_user_id FROM daily_events WHERE id=? AND chat_id=?", (daily_id, chat_id))
                row = cur.fetchone()

            if not row:
                await query.answer("Дейлик не найден", show_alert=True)
                return

            creator_id = row[0]
            if user_id != creator_id and user_id not in admin_ids:
                await query.answer("У вас нет прав для управления этим дейликом", show_alert=True)
                return

            # Возвращаем предыдущую клавиатуру
            await query.message.edit_reply_markup(reply_markup=get_manage_daily_keyboard(daily_id))
            await query.answer()


        elif data.startswith("daily_delete:"):
            daily_id = int(data.split(":")[1])
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, name, creator_user_id FROM daily_events WHERE id=?", (daily_id,))
                row = cur.fetchone()
            if not row:
                await query.answer("Дейлик уже удалён", show_alert=True)
                return
            daily_id, daily_name, creator_id = row
            # Проверка прав
            if user_id != creator_id and user_id not in admin_ids:
                await query.answer("Удалять может только создатель или админ", show_alert=True)
                return
            kb = InlineKeyboardBuilder()
            kb.row(
                InlineKeyboardButton(text="✅ Да", callback_data=f"daily_confirm_delete:{daily_id}"),
                InlineKeyboardButton(text="❌ Нет", callback_data=f"daily_cancel_delete:{daily_id}")
            )
            await query.message.edit_text(
                f"Уверен, что хочешь удалить дейлик <b>{daily_name}</b>?",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
            await query.answer()

        elif data.startswith("daily_confirm_delete:"):
            daily_id = int(data.split(":")[1])
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                # Получаем calendar_event_id перед удалением дейлика
                cur.execute("SELECT name, creator_user_id, calendar_event_id FROM daily_events WHERE id=?", (daily_id,))
                row = cur.fetchone()
                if not row:
                    await query.answer("Дейлик уже удалён", show_alert=True)
                    return
                daily_name, creator_id, calendar_event_id = row
                # Проверка прав
                if user_id != creator_id and user_id not in admin_ids:
                    await query.answer("Удалять может только создатель или админ", show_alert=True)
                    return
                
                # Удаляем событие из Google Календаря, если оно есть
                if calendar_event_id:
                    asyncio.create_task(delete_calendar_event(
                        calendar_event_id=calendar_event_id,
                        chat_id=chat_id,
                        bot_instance=query.bot
                    ))

                cur.execute("DELETE FROM daily_events WHERE id=?", (daily_id,))
                cur.execute("DELETE FROM daily_participants WHERE daily_id=?", (daily_id,))
                conn.commit()
            await query.message.edit_text(f"Дейлик <b>{daily_name}</b> удалён ✅", parse_mode="HTML")

        elif data.startswith("daily_cancel_delete:"):
            daily_id = int(data.split(":")[1])
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM daily_events WHERE id=?", (daily_id,))
                row = cur.fetchone()

            if not row:
                await query.answer("Дейлик уже удалён", show_alert=True)
                return

            daily = dict(zip([column[0] for column in cur.description], row))
            participants = get_daily_participants(daily['id'], chat_id)
            text = format_daily_text(daily, participants)
            kb = InlineKeyboardBuilder()
            kb.row(
                InlineKeyboardButton(text="✏️ Изменить данные", callback_data=f"daily_edit:{daily['id']}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"daily_delete:{daily['id']}")
            )

            await query.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
            await query.answer("Удаление отменено")
        
        elif data.startswith("daily_tag_participants:"):
            daily_id = int(data.split(":")[1])
            daily = get_daily(daily_id)

            if not daily:
                await query.answer("Дейлик не найден", show_alert=True)
                return
            
            # Получаем ник пользователя, который нажал кнопку
            clicked_user_name = query.from_user.full_name or str(query.from_user.id)

            participants = get_daily_participants(daily_id, chat_id)
            mentions = []
            for p in participants:
                user_data = get_user(p['user_id'], chat_id)
                if user_data and user_data['nick']:
                    mentions.append(f"@{user_data['nick'].lstrip('@')}") # Убираем лишний @ из начала, чтобы не дублировать
                else:
                    # Если нет ника, можно использовать имя из Telegram или просто ID
                    mentions.append(f"<a href=\"tg://user?id={p['user_id']}\">{p['name']}</a>")

            if not mentions:
                await query.answer("В этом дейлике пока нет участников.", show_alert=True)
                return
            
            mentions_text = ", ".join(mentions)
            message_text = (
                f"{clicked_user_name} тегает всех участников дейлика <b>{daily['name']}</b>:\n\n"
                f"{mentions_text}"
            )
            
            await query.message.answer(message_text, parse_mode="HTML", disable_web_page_preview=True)
            await query.answer("Участники отмечены!")

import asyncio
from datetime import datetime, timedelta
from contextlib import closing
import sqlite3
from aiogram import Bot, types
from settings import get_setting  # функция из settings.py

REMINDER_INTERVAL = 300  # проверка каждые 5 минут

async def daily_reminder_loop(bot: Bot):
    while True:
        now = datetime.now()
        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            # Берём дейлики, где reminder не отправлен
            cur.execute("""
                SELECT d.id, d.name, d.date, d.time, d.chat_id
                FROM daily_events d
                WHERE d.reminded = 0
            """)
            rows = cur.fetchall()
            for row in rows:
                daily_id, daily_name, date_str, time_str, chat_id = row

                # Проверка настройки чата: включены ли напоминания?
                if not get_setting(chat_id, "daily_reminders"):
                    continue  # пропускаем этот чат

                dt_obj = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                delta = dt_obj - now
                total_seconds = delta.total_seconds()

                # Проверка интервала: 22–24 часа
                if 22*3600 < total_seconds < 24*3600:
                    # Получаем участников с nick
                    cur.execute("""
                        SELECT u.nick
                        FROM daily_participants p
                        JOIN users u ON u.user_id = p.user_id AND u.chat_id = ?
                        WHERE p.daily_id = ?
                    """, (chat_id, daily_id))
                    participants = cur.fetchall()
                    mentions = [f"@{p[0].lstrip('@')}" for p in participants if p[0]]
                    mentions_text = ", ".join(mentions) if mentions else "никого"

                    text = (
                        f"⏰ Напоминание о дейлике завтра!\n\n"
                        f"🎉 <b>{daily_name}</b>\n"
                        f"Участвуют: {mentions_text}"
                    )
                    try:
                        await bot.send_message(chat_id, text, parse_mode="HTML")
                        # Отмечаем, что напоминание отправлено
                        cur.execute("UPDATE daily_events SET reminded = 1 WHERE id=?", (daily_id,))
                        conn.commit()
                    except Exception as e:
                        print(f"Ошибка при отправке напоминания для дейлика {daily_id}: {e}")

        await asyncio.sleep(REMINDER_INTERVAL)

