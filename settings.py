# settings.py
import sqlite3
from contextlib import closing
from aiogram.filters import Command
from aiogram import Dispatcher, types
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton

DB_PATH = "stats.db"

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
        "name": "group_masturbation",
        "text_on": "Убрать групповую мастурбацию из магазина",
        "text_off": "Включить групповую мастурбацию",
        "confirm_on": "✅ Групповая мастурбация отключена",
        "confirm_off": "✅ Групповая мастурбация включена"
    }
]

# --- Утилиты для работы с БД ---
def get_setting(chat_id: int, name: str) -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE chat_id=? AND name=?", (chat_id, name))
        row = cur.fetchone()
        if row:
            return row[0]
        return 0  # по умолчанию выключено

def set_setting(chat_id: int, name: str, value: int):
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
        chat_id = message.chat.id
        kb = get_settings_keyboard(chat_id)
        await message.answer(f"Настройки бота для чата {message.chat.title or chat_id}", reply_markup=kb)

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
