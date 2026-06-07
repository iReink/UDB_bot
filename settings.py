# settings.py
import sqlite3
from contextlib import closing
from aiogram.filters import Command
from aiogram import Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton

# from main import ADMIN_IDS # Импортируем список админов - Эту строку удаляем
ADMIN_IDS = {6010666986, 884940984, 749027951} # Переносим определение ADMIN_IDS сюда

DB_PATH = "stats.db"
AI_RESPONSE_CHANCE_SETTING = "ai_response_chance_percent"
AI_RESPONSE_CHANCE_DEFAULT = 3.0
AI_RESPONSE_CHANCE_MIN = 0.0
AI_RESPONSE_CHANCE_MAX = 10.0


class SettingsStates(StatesGroup):
    waiting_ai_response_chance = State()

# --- Настройки ---
SETTINGS_OPTIONS = [
    {
        "name": "daily_reminders",
        "text_on": "Не напоминать о дейликах",
        "text_off": "Включить напоминания о дейлике за сутки",
        "confirm_on": "✅ Напоминания о дейликах выключены",
        "confirm_off": "✅ Напоминания о дейликах включены"
    },
    {
        "name": "forbid_mujlo",
        "text_on": "Разрешить говорить мужлу",
        "text_off": "Запретить говорить мужлу",
        "confirm_on": "✅ Говорить мужлу разрешено",
        "confirm_off": "✅ Говорить мужлу запрещено"
    },
    {
        "name": "enable_geyser",
        "text_on": "Выключить Гейзер",
        "text_off": "Включить Гейзер",
        "confirm_on": "✅ Гейзер выключен",
        "confirm_off": "✅ Гейзер включен"
    }
]

# --- Утилиты для работы с БД ---
def get_setting(chat_id: int, name: str) -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE chat_id=? AND name=?", (chat_id, name))
        row = cur.fetchone()
        if row:
            return int(float(row[0]))
        return 0  # по умолчанию выключено

def get_float_setting(chat_id: int, name: str, default: float = 0.0) -> float:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE chat_id=? AND name=?", (chat_id, name))
        row = cur.fetchone()
        if not row:
            return float(default)
        try:
            return float(row[0])
        except (TypeError, ValueError):
            return float(default)


def get_ai_response_chance_percent(chat_id: int) -> float:
    value = get_float_setting(chat_id, AI_RESPONSE_CHANCE_SETTING, AI_RESPONSE_CHANCE_DEFAULT)
    return max(AI_RESPONSE_CHANCE_MIN, min(AI_RESPONSE_CHANCE_MAX, value))


def set_setting(chat_id: int, name: str, value):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO settings(chat_id, name, value)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, name) DO UPDATE SET value=excluded.value
        """, (chat_id, name, value))
        conn.commit()

# --- Генератор клавиатуры ---
def get_settings_keyboard(chat_id: int) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    chance = get_ai_response_chance_percent(chat_id)
    kb.row(
        InlineKeyboardButton(
            text=f"Шанс AI-ответа: {chance:g}%",
            callback_data="setting_ai_response_chance",
        )
    )
    for opt in SETTINGS_OPTIONS:
        current_value = get_setting(chat_id, opt["name"])
        button_text = opt["text_on"] if current_value else opt["text_off"]
        kb.row(
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"setting_toggle:{opt['name']}"
            )
        )
    return kb.as_markup()


# --- Регистрация хендлеров ---
def register_settings_handlers(dp: Dispatcher):

    @dp.message(Command("settings"))
    async def settings_command(message: types.Message):
        if message.from_user.id not in ADMIN_IDS:
            await message.answer("❌ У вас нет прав для вызова этой команды.")
            return

        chat_id = message.chat.id
        kb = get_settings_keyboard(chat_id)
        await message.answer(f"Настройки бота для чата {message.chat.title or chat_id}", reply_markup=kb)

    @dp.callback_query(lambda c: c.data == "setting_ai_response_chance")
    async def ai_response_chance_button(callback: types.CallbackQuery, state: FSMContext):
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("Нет прав для изменения настройки.", show_alert=True)
            return

        chat_id = callback.message.chat.id
        current = get_ai_response_chance_percent(chat_id)
        await state.set_state(SettingsStates.waiting_ai_response_chance)
        await state.update_data(chat_id=chat_id)
        await callback.message.answer(
            f"Текущий шанс AI-ответа: {current:g}%.\n"
            "Введите новое значение от 0 до 10. Можно дробное, например 3.5"
        )
        await callback.answer()

    @dp.message(SettingsStates.waiting_ai_response_chance)
    async def ai_response_chance_input(message: types.Message, state: FSMContext):
        if not message.from_user or message.from_user.id not in ADMIN_IDS:
            await message.answer("Нет прав для изменения настройки.")
            await state.clear()
            return

        raw_value = (message.text or "").strip().replace(",", ".")
        try:
            value = float(raw_value)
        except ValueError:
            await message.answer("Введите число от 0 до 10. Например: 3.5")
            return

        if not (AI_RESPONSE_CHANCE_MIN <= value <= AI_RESPONSE_CHANCE_MAX):
            await message.answer("Значение должно быть от 0 до 10.")
            return

        data = await state.get_data()
        chat_id = int(data.get("chat_id") or message.chat.id)
        set_setting(chat_id, AI_RESPONSE_CHANCE_SETTING, value)
        await state.clear()
        await message.answer(
            f"Шанс AI-ответа установлен: {value:g}%",
            reply_markup=get_settings_keyboard(chat_id),
        )

    @dp.callback_query(lambda c: c.data.startswith("setting_toggle:"))
    async def toggle_setting_handler(callback: types.CallbackQuery):
        chat_id = callback.message.chat.id
        setting_name = callback.data.split(":")[1]

        # Найти опцию в словаре
        opt = next((o for o in SETTINGS_OPTIONS if o["name"] == setting_name), None)
        if not opt:
            await callback.answer("❌ Настройка не найдена", show_alert=True)
            return

        current_value = get_setting(chat_id, setting_name)
        new_value = 0 if current_value else 1
        set_setting(chat_id, setting_name, new_value)

        confirm_text = opt["confirm_on"] if current_value else opt["confirm_off"]

        # Редактируем сообщение с подтверждением и скрываем меню
        await callback.message.edit_text(confirm_text)
        await callback.answer()
