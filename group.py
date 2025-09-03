# group.py
import asyncio
import random
from contextlib import closing
from typing import Dict, Set

from aiogram import types
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import Bot
from sosalsa import add_sits, get_sits

from db import get_connection

# ==========================
# НАСТРОЙКИ И ТЕКСТЫ
# ==========================

STICKER_FILE_ID = "CAACAgIAAyEFAASjKavKAAIDrGi31TwpfP-R-JI64M0v6eRnTCFxAAJMUAACITxRSq0hIi2dEdhQNgQ"
VOICE_PATH = "images/poehali.ogg"
EVENT_COST = 3  # Стоимость запуска ивента в ситах

PREPARE_DELAY_SEC = 10 * 60   # 10 минут ожидания
JOIN_WINDOW_SEC   = 5 * 60    # 5 минут окно присоединения

GROUP_JOIN_MESSAGES = [
    "{name} пристраивается сбоку",
    "{name} садится на диван и смотрит",
    "Все немного двигаются чтобы дать {name} место",
    "{name} садится в центр круга",
    "{name} немного стесняется и подсматривает из-за угла",
    "Для {name} не нашлось лишнего стула, поэтому пришлось сесть на полу",
]

# ==========================
# СОСТОЯНИЕ ИВЕНТА НА ЧАТ
# ==========================

class GroupEventState:
    __slots__ = ("participants", "joined_order", "names", "join_msg_id", "join_open", "lock")

    def __init__(self) -> None:
        self.participants: Set[int] = set()
        self.joined_order: list[int] = []
        self.names: Dict[int, str] = {}
        self.join_msg_id: int | None = None
        self.join_open: bool = False
        self.lock = asyncio.Lock()

# chat_id -> state
ACTIVE_GROUP_EVENTS: Dict[int, GroupEventState] = {}


# ==========================
# УТИЛИТЫ
# ==========================

def get_user_display_name(user_id: int, chat_id: int) -> str:
    """Пытаемся взять красивое имя из БД, иначе вернём id строкой."""
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
    return str(user_id)


def join_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Присоединиться", callback_data="group_join"))
    return kb.as_markup()


# ==========================
# ПУБЛИЧНЫЕ ТОЧКИ
# ==========================

def register_group_handlers(dp):
    """Регистрируем обработчик нажатия на кнопку «Присоединиться»."""
    @dp.callback_query(lambda c: c.data == "group_join")
    async def on_group_join(query: types.CallbackQuery):
        chat_id = query.message.chat.id
        user_id = query.from_user.id

        state = ACTIVE_GROUP_EVENTS.get(chat_id)
        if not state or not state.join_open:
            await query.answer("Окно регистрации закрыто.", show_alert=True)
            return

        async with state.lock:
            if user_id in state.participants:
                await query.answer("Ты уже присоединился!", show_alert=True)
                return

            # Регистрируем участника (бесплатно)
            state.participants.add(user_id)
            state.joined_order.append(user_id)

            # Определяем красивое имя
            display_name = get_user_display_name(user_id, chat_id)
            if display_name == str(user_id):  # если в БД не нашли — подстрахуемся на профиль TG
                display_name = query.from_user.full_name or (f"@{query.from_user.username}" if query.from_user.username else str(user_id))
            state.names[user_id] = display_name

        # Сообщение в чат о присоединении
        phrase = random.choice(GROUP_JOIN_MESSAGES).format(name=display_name)
        await query.message.answer(phrase)

        # Всплывашка для нажавшего
        await query.answer("Ты в деле!")

    # Ничего не возвращаем — dp уже знает про хендлер


async def start_group_event(message: types.Message, user_id: int):
    chat_id = message.chat.id
    """
    Точка входа при покупке товара.
    1) Проверяем баланс организатора
    2) Списываем стоимость ивента
    3) Отправляем стикер
    4) Ждем 10 минут
    5) Голосовое
    6) Сообщение с кнопкой «Присоединиться» (5 минут)
    7) Закрываем кнопку, шлём финальный список
    """

    # Проверяем баланс организатора
    balance = get_sits(chat_id, user_id)
    if balance < EVENT_COST:
        await message.answer(
            f"Недостаточно сит для запуска ивента! Нужно: {EVENT_COST}, у тебя: {balance}"
        )
        return

    # Не даём запустить параллельно второй ивент в том же чате
    if chat_id in ACTIVE_GROUP_EVENTS:
        await message.answer("Ивент уже идёт — подожди окончания текущего.")
        return

    # Списываем стоимость ивента с организатора
    add_sits(chat_id, user_id, -EVENT_COST)

    # Регистрируем состояние
    ACTIVE_GROUP_EVENTS[chat_id] = GroupEventState()

    # 1) Стикер
    await message.answer_sticker(STICKER_FILE_ID)

    # Уведомляем о списании
    await message.answer(f"С твоего счета списано {EVENT_COST} сит за запуск ивента")

    # Запускаем «флоу» как фоновую задачу, чтобы не блокировать остальной бот
    asyncio.create_task(_run_event_flow(message.bot, chat_id))


# ==========================
# ВНУТРЕННЯЯ ЛОГИКА
# ==========================

async def _run_event_flow(bot: Bot, chat_id: int):
    state = ACTIVE_GROUP_EVENTS.get(chat_id)
    if not state:
        return

    try:
        # 2) Ждём 10 минут
        await asyncio.sleep(PREPARE_DELAY_SEC)

        # 3) Отправляем голосовое
        try:
            voice = FSInputFile(VOICE_PATH)
            await bot.send_voice(chat_id, voice)
        except Exception:
            # Даже если голосовое не улетело — продолжаем сценарий
            pass

        # 4) Сообщение с кнопкой «Присоединиться»
        join_msg = await bot.send_message(
            chat_id,
            "Поехали! Для участия нажми на кнопку",
            reply_markup=join_keyboard()
        )
        state.join_msg_id = join_msg.message_id
        state.join_open = True

        # 5) Ждём 5 минут окно присоединения
        await asyncio.sleep(JOIN_WINDOW_SEC)

    finally:
        # Закрываем окно (даже если были исключения)
        if state:
            state.join_open = False

    # Убираем кнопку (не удаляя сообщение)
    if state and state.join_msg_id:
        try:
            await bot.edit_message_reply_markup(chat_id, state.join_msg_id, reply_markup=None)
        except Exception:
            pass

    # Финальный список
    if state:
        if not state.joined_order:
            await bot.send_message(chat_id, "Групповая мастурбация окончена! Никто не присоединился 😢")
        else:
            lines = []
            for uid in state.joined_order:
                lines.append(state.names.get(uid) or get_user_display_name(uid, chat_id))
            text = "Групповая мастурбация окончена! Спасибо всем участникам. Вот они сверху вниз:\n" + "\n".join(lines)
            await bot.send_message(chat_id, text)

            # 🎲 Выбираем победителя
            import random
            winner_id = random.choice(state.joined_order)
            winner_name = state.names.get(winner_id) or get_user_display_name(winner_id, chat_id)

            # Сообщение + выдача сита
            await bot.send_message(chat_id, f"Весь сит сегодня достался {winner_name}!")
            add_sits(chat_id, winner_id, 3)

        # Чистим состояние
        ACTIVE_GROUP_EVENTS.pop(chat_id, None)
