# group.py
import asyncio
import random
from contextlib import closing
from typing import Dict, Set, List

from aiogram import types, Bot
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from sosalsa import add_sits, get_sits
from db import get_connection

from quest import update_quest_progress

# ==========================
# НАСТРОЙКИ
# ==========================
STICKER_FILE_ID = "CAACAgIAAyEFAASjKavKAAIDrGi31TwpfP-R-JI64M0v6eRnTCFxAAJMUAACITxRSq0hIi2dEdhQNgQ"
VOICE_PATH = "images/poehali.ogg"
EVENT_COST = 1  # Стоимость запуска
JOIN_COST = 1   # Стоимость присоединения

PREPARE_DELAY_SEC = 10 * 60   # 10 минут ожидания
JOIN_WINDOW_SEC = 5 * 60      # 5 минут окно присоединения

GROUP_JOIN_MESSAGES = [
    "{name} пристраивается сбоку",
    "{name} садится на диван и смотрит",
    "Все немного двигаются чтобы дать {name} место",
    "{name} садится в центр круга",
    "{name} немного стесняется и активничает из-за угла",
    "Для {name} не нашлось лишнего стула, поэтому пришлось сесть на полу",
    "{name} тихонько подкрадывается и устраивается сзади",
    "{name} врывается в комнату с криком: «Я опоздал?»",
    "К всеобщей радости, {name} наконец-то с нами",
    "{name} аккуратно протискивается между диваном и столом со словами «Можно я тут?»",
    "{name} появляется с тарелкой печенья и моментально становится душой компании",
]

# ==========================
# СОСТОЯНИЕ ИВЕНТА
# ==========================
class GroupEventState:
    __slots__ = ("participants", "joined_order", "names", "join_msg_id", "join_open", "lock", "freebies", "reminders")

    def __init__(self) -> None:
        self.participants: Set[int] = set()       # user_id участников
        self.joined_order: List[int] = []         # порядок участников
        self.names: Dict[int, str] = {}           # user_id -> имя
        self.join_msg_id: int | None = None
        self.join_open: bool = False
        self.lock = asyncio.Lock()
        self.freebies: List[int] = []             # те, кто не смог заплатить
        self.reminders: Set[int] = set()          # список для напоминания



# chat_id -> state
ACTIVE_GROUP_EVENTS: Dict[int, GroupEventState] = {}

# ==========================
# УТИЛИТЫ
# ==========================
def get_user_display_name(user_id: int, chat_id: int) -> str:
    """Берём красивое имя из БД или user_id."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM users WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        row = cur.fetchone()
    return row[0] if row and row[0] else str(user_id)


# ==========================
# КНОПКИ
# ==========================
def join_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="Присоединиться (1 сит)", callback_data="group_join"),
        InlineKeyboardButton(text="Смотреть (бесплатно)", callback_data="group_watch")
    )
    return kb.as_markup()

def remind_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔔 Напомнить", callback_data="group_remind"))
    return kb.as_markup()



# ==========================
# ОБРАБОТЧИКИ
# ==========================
def register_group_handlers(dp):
    @dp.callback_query(lambda c: c.data == "group_join")
    async def on_group_join(query: types.CallbackQuery):
        chat_id = query.message.chat.id
        user_id = query.from_user.id

        state = ACTIVE_GROUP_EVENTS.get(chat_id)
        if not state or not state.join_open:
            await query.answer("Окно регистрации закрыто.", show_alert=True)
            return

        async with state.lock:
            if user_id in state.participants or user_id in state.freebies:
                await query.answer("Ты уже присоединился!", show_alert=True)
                return

            balance = get_sits(chat_id, user_id)
            display_name = get_user_display_name(user_id, chat_id)
            if display_name == str(user_id):
                display_name = query.from_user.full_name or (
                    f"@{query.from_user.username}" if query.from_user.username else str(user_id)
                )
            state.names[user_id] = display_name

            if balance < JOIN_COST:
                # Недостаточно сита — в список freebies
                state.freebies.append(user_id)
                await query.answer("У вас недостаточно сита для групповой мастурбации, но мы всё запишем на cumеру", show_alert=True)
            else:
                # Списываем сит и добавляем участника
                add_sits(chat_id, user_id, -JOIN_COST)
                state.participants.add(user_id)
                state.joined_order.append(user_id)
                await query.answer("Ты в деле!")
                phrase = random.choice(GROUP_JOIN_MESSAGES).format(name=display_name)
                await query.message.answer(phrase)

    @dp.callback_query(lambda c: c.data == "group_watch")
    async def on_group_watch(query: types.CallbackQuery):
        chat_id = query.message.chat.id
        user_id = query.from_user.id

        state = ACTIVE_GROUP_EVENTS.get(chat_id)
        if not state or not state.join_open:
            await query.answer("Окно регистрации закрыто.", show_alert=True)
            return

        async with state.lock:
            if user_id in state.participants or user_id in state.freebies:
                await query.answer("Ты уже зарегистрировался!", show_alert=True)
                return

            # Добавляем как зрителя
            display_name = get_user_display_name(user_id, chat_id)
            if display_name == str(user_id):
                display_name = query.from_user.full_name or (
                    f"@{query.from_user.username}" if query.from_user.username else str(user_id)
                )
            state.names[user_id] = display_name
            state.freebies.append(user_id)

        await query.message.answer(f"👀 {display_name} просто посмотрит онлайн-трансляцию")
        await query.answer("Ты в списке зрителей!")

    @dp.callback_query(lambda c: c.data == "group_remind")
    async def on_group_remind(query: types.CallbackQuery):
        chat_id = query.message.chat.id
        user_id = query.from_user.id

        state = ACTIVE_GROUP_EVENTS.get(chat_id)
        if not state:
            await query.answer("Ивент ещё не запущен.", show_alert=True)
            return

        async with state.lock:
            if user_id in state.reminders:
                await query.answer("Ты уже в списке для напоминания!", show_alert=True)
                return
            state.reminders.add(user_id)

            display_name = query.from_user.full_name or (
                f"@{query.from_user.username}" if query.from_user.username else str(user_id)
            )
            state.names[user_id] = display_name

        await query.answer("✅ Напомню тебе перед стартом!")


# ==========================
# ЗАПУСК ИВЕНТА
# ==========================
async def start_group_event(message: types.Message, user_id: int):
    chat_id = message.chat.id

    # Проверка на баланс организатора
    balance = get_sits(chat_id, user_id)
    if balance < EVENT_COST:
        await message.answer(f"Недостаточно сит для запуска! Нужно {EVENT_COST}, у тебя {balance}")
        return

    # Проверка на активный ивент
    if chat_id in ACTIVE_GROUP_EVENTS:
        await message.answer("Ивент уже идёт, дождись окончания.")
        return

    # Списываем сит за запуск
    add_sits(chat_id, user_id, -EVENT_COST)
    state = GroupEventState()
    ACTIVE_GROUP_EVENTS[chat_id] = state

    # Организатор автоматически участвует
    state.participants.add(user_id)
    state.joined_order.append(user_id)
    name = get_user_display_name(user_id, chat_id)
    state.names[user_id] = name

    await message.answer_sticker(STICKER_FILE_ID)
    await message.answer(f"С твоего счёта списано {EVENT_COST} сит за запуск ивента")

    # Сообщение с кнопкой "Напомнить"
    await message.answer(
        "Хочешь напоминание о старте? Нажми кнопку!",
        reply_markup=remind_keyboard()
    )

    asyncio.create_task(_run_event_flow(message.bot, chat_id))

# ==========================
# ЛОГИКА ПРОВЕДЕНИЯ
# ==========================
async def _run_event_flow(bot: Bot, chat_id: int):
    state = ACTIVE_GROUP_EVENTS.get(chat_id)
    if not state:
        return

    try:
        # 10 минут ожидания
        await asyncio.sleep(PREPARE_DELAY_SEC - 7 * 60)  # Ждём до отметки 7 минут
        await bot.send_message(chat_id, "До групповой мастурбации осталось 7 минут!")

        await asyncio.sleep(3 * 60)  # Ждём до отметки 4 минут
        await bot.send_message(chat_id, "До групповой мастурбации осталось 4 минуты!")

        await asyncio.sleep(3 * 60)  # Ждём до отметки 1 минуты
        await bot.send_message(chat_id, "До групповой мастурбации осталась 1 минута!")

        await asyncio.sleep(1 * 60)  # Ждём оставшуюся 1 минуту

        # Напоминание всем за 10 секунд до старта
        if state.reminders:
            mentions = []
            for uid in state.reminders:
                username = None
                with closing(get_connection()) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT nick FROM users WHERE chat_id = ? AND user_id = ?", (chat_id, uid))
                    row = cur.fetchone()
                    username = row[0] if row and row[0] else None
                if username:
                    mentions.append(username)
                else:
                    mentions.append(state.names.get(uid, str(uid)))

            text = " ".join(mentions) + " — скоро начинаем!!"
            await bot.send_message(chat_id, text)

        # Голосовое перед стартом
        try:
            voice = FSInputFile(VOICE_PATH)
            await bot.send_voice(chat_id, voice)
        except Exception:
            pass

        # Сообщение с кнопкой
        msg = await bot.send_message(
            chat_id,
            "Поехали! Для участия нажми на кнопку",
            reply_markup=join_keyboard()
        )
        state.join_msg_id = msg.message_id
        state.join_open = True

        # Активная фаза с напоминаниями
        await asyncio.sleep(JOIN_WINDOW_SEC - 60 - 30 - 10 - 1)

        await bot.send_message(chat_id, "⏳ Осталась одна минута! Готовимся!")
        await asyncio.sleep(30)
        await bot.send_message(chat_id, "🎯 Целимся!!")
        await asyncio.sleep(20)
        await bot.send_message(chat_id, "🔟 10-секундная готовность!")
        await asyncio.sleep(9)
        await bot.send_message(chat_id, "💥 ПЛИ!")

        await asyncio.sleep(1)  # Дожидаемся финала

    finally:
        if state:
            state.join_open = False

    # Убираем кнопку
    if state and state.join_msg_id:
        try:
            await bot.edit_message_reply_markup(chat_id, state.join_msg_id, reply_markup=None)
        except Exception:
            pass

    # Финал (как было дальше)
    if state:
        participants = state.joined_order
        freebies = state.freebies

        if not participants:
            await bot.send_message(chat_id, "Групповая мастурбация окончена! Никто не присоединился 😢")
        else:
            lines = [state.names.get(uid) or get_user_display_name(uid, chat_id) for uid in participants]
            text = "Групповая мастурбация окончена! Спасибо всем участникам. Вот они сверху вниз:\n" + "\n".join(lines)
            await bot.send_message(chat_id, text)

            # Победитель
            winner_id = random.choice(participants)
            winner_name = state.names[winner_id]
            reward = len(participants) + 1
            add_sits(chat_id, winner_id, reward)
            await bot.send_message(chat_id, f"🎉 Победитель: {winner_name}! Получает {reward} сит!")
            # ОТправка уведомления в обработчик квестов
            update_quest_progress(winner_id, chat_id, "group_win", 1, bot=bot)

            # Бонус для одного из freebies
            if freebies:
                lucky = random.choice(freebies)
                lucky_name = state.names[lucky]
                add_sits(chat_id, lucky, 1)
                await bot.send_message(chat_id, f"✨ Также немножко капнуло на {lucky_name} — +1 сит!")

        ACTIVE_GROUP_EVENTS.pop(chat_id, None)
