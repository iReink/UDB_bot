import os
import asyncio
import io
import re
from datetime import datetime, time, timedelta, date
from collections import defaultdict
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
import logging
from aiogram import types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types import FSInputFile, CallbackQuery, BufferedInputFile
import aiocron
import math
import random
import weekly_awards
import sticker_manager
import sqlite3
import db
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from db import get_connection, get_chat_users, get_total_stats
from contextlib import closing
from db import (
    get_daily_stats,
    increment_daily_stats,
    increment_total_stats,
    get_user,
    add_or_update_user,
    get_last_7_daily_stats,
    get_all_chats,
    get_user_sex,
    increment_sticker_stats,
    get_user_display_name,
    add_sits
)
from aiogram.filters import Command, CommandObject
from aiogram.types import Message


from aiogram.types import MessageReactionUpdated, MessageReactionCountUpdated
from sticker_manager import silence_checker_task
from mujlo import handle_mujlo_message, handle_mujlo_buy, reset_mujlo_daily
from quest import update_quest_progress

import sosalsa
from sosalsa import register_sos_handlers
from sosalsa import daily_regeneration_task, bot as daily_bot

from new_year import run_new_year


from aiogram.exceptions import TelegramNetworkError, TelegramServerError

# from daily import daily_reminder_loop

dp = Dispatcher()
import fight_club
fight_club.register_fight_club_handlers(dp) # Регистрируем хэндлеры бойцовского клуба

register_sos_handlers(dp)

import group
group.register_group_handlers(dp)

from help import register_help_handler
register_help_handler(dp)

from quest import register_quest_handlers
register_quest_handlers(dp)

from hall import register_hall_handlers
register_hall_handlers(dp)

from daily import register_daily_handlers, daily_reminder_loop
register_daily_handlers(dp)

from settings import register_settings_handlers, ADMIN_IDS
register_settings_handlers(dp)

import mujlo
mujlo.register_mujlo_handlers(dp)

import geyser
geyser.register_geyser_handlers(dp) # Регистрируем хэндлеры гейзера

import dick
dick.register_dick_handlers(dp)

import dashboard
dashboard.register_dashboard_handlers(dp)


from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")


bot = Bot(token=TOKEN)
sticker_manager.bot = bot


STATS_FILE = "stats.json"
MAKOVKA_FILE_ID = "CAACAgIAAyEFAASjKavKAAOcaJ95ivqdgkA5gstkAbRt25CCRLAAAkN5AAJTNbFKdWJ4ufamt9I2BA"

# Стикерпаки, за которыми следим
TRACKED_STICKERPACKS = {
    "UDB_true",
    # "AnotherPackName",
    # "CoolMemes2025",
}

# Конфигурация магазина
SHOP_ITEMS = {
    "piss8": {
        "name": "💦 8 литров мочи",
        "price": 8,
        "buy_text": "💦 {user_name} купил 8 литров мочи и забрызгал чят! \n💦💦💦💦💦💦💦💦"
    },
    "mic1": {
        "name": "🎤 Сказать в микрофон",
        "price": 1,
        "buy_text": "🎤 {user_name} вибрирует! 🎤"
    },
    "spider1": {
        "name": "🕷 Скинуть в чат паука 🕷",
        "price": 1,
        "buy_text": "🕷 {user_name} отправил паука в чат! 🕷",
        "action": "send_spider",
        "file": os.path.join("images", "spider.jpg")  # путь относительно проекта
    },
    "filtr0": {
        "name": "☕️ Выпить кофе",
        "price": 0,
        "buy_text": {
            "m": "{user_name} сладко попил фильтра и улыбнулся ☕️☕️☕️",
            "f": "{user_name} сладко попила фильтра и улыбнулась ☕️☕️☕️"
        },
        "action": "drink_coffee"
    },
    "sticker1000": {
        "name": "📝 Купить стикер",
        "price": 1000,
        "buy_text": "Воу воу! {user_name} выложил кругленькую сумму, чтобы купить свой стикер! \nНапиши министру стикеров что именно ты хочешь, но помни, что окончательное решение за ним."
    },
    "group": {
        "name": "Групповая мастурбация",
        "price": 1,
        "buy_text": {
            "m": "{user_name} всех зовёт на огонёк",
            "f": "{user_name} всех зовёт на огонёк"
        },
    "action": "group"
    }
}



logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


#переменная для счётчика количества лайков, за которые Виталик получил запрлату
last_reward_react_given = 0



def ensure_user(chat_id: int, user_id: int, user_name: str, username: str | None = None):
    """
    Гарантирует, что пользователь есть в БД и все записи корректны.
    Создаёт пользователя, daily_stats за последние 7 дней и total_stats при необходимости.
    Также обновляет ник (username).
    """
    # Получаем пользователя из БД
    user = db.get_user(user_id, chat_id)

    # Приводим username к виду '@username' или None
    nick = f"@{username}" if username else None

    if not user:
        # Создаём новую запись
        db.add_or_update_user(user_id, chat_id, user_name, sits=0, punished=0, sex=None, nick=nick)
    else:
        # Проверяем имя
        needs_update = False
        if user["name"] != user_name:
            needs_update = True

        # Проверяем ник
        db_nick = user.get("nick")
        if db_nick != nick:
            needs_update = True

        if needs_update:
            db.add_or_update_user(
                user_id,
                chat_id,
                user_name,
                sits=user.get("sits", 0),
                punished=user.get("punished", 0),
                sex=user.get("sex"),
                nick=nick
            )

    # Daily_stats: последние 7 дней
    today = datetime.now().date()
    for i in range(7):
        day_date = today - timedelta(days=i)
        if not db.get_daily_stats(user_id, day_date.isoformat()):
            db.add_or_update_daily_stats(
                user_id,
                chat_id,
                day_date.isoformat(),
                messages=0,
                words=0,
                chars=0,
                stickers=0,
                coffee=0
            )

    # Total_stats
    if not db.get_total_stats(user_id, chat_id):
        db.add_or_update_total_stats(user_id, chat_id, messages=0, words=0, chars=0, stickers=0, coffee=0)


from datetime import datetime
from datetime import date
import logging
import db  # предполагаем, что все функции из db.py доступны

def update_stats(chat_id, user_id, user_name, message, chat_name=None):
    """
    Обновляет статистику сообщения напрямую в БД.
    Теперь также учитывает видеокружочки и голосовые в поле 'rounds'.
    """
    username = message.from_user.username
    nick = f"@{username}" if username else None

    # Гарантируем пользователя в БД
    add_or_update_user(user_id, chat_id, user_name, nick=nick)

    today_str = date.today().isoformat()

    # Определяем тип контента
    is_sticker = getattr(message, "sticker", None) is not None
    is_round = getattr(message, "video_note", None) is not None

    # === 1️⃣ Обработка стикеров ===
    if is_sticker:
        if message.sticker and message.sticker.set_name in TRACKED_STICKERPACKS:
            increment_sticker_stats(
                chat_id=message.chat.id,
                file_id=message.sticker.file_id,
                set_name=message.sticker.set_name,
                date_str=today_str
            )

        increment_daily_stats(user_id, chat_id, today_str, stickers=1)
        increment_total_stats(user_id, chat_id, stickers=1)
        asyncio.create_task(update_quest_progress(user_id, chat_id, "stickers_sent", 1, bot))

        if not chat_name:
            chat_name = chat_id

        sticker = message.sticker
        sticker = message.sticker
        sticker_info = (
            f"file_id: {sticker.file_id}, "
            f"emoji: {getattr(sticker, 'emoji', None)}, "
            f"set_name: {getattr(sticker, 'set_name', None)}, "
            f"size: {getattr(sticker, 'width', None)}x{getattr(sticker, 'height', None)}, "
            f"animated: {getattr(sticker, 'is_animated', None)}, "
            f"video: {getattr(sticker, 'is_video', None)}"
        )
        logging.info(
            f"Обновлена статистика: чат \"{chat_name}\", пользователь {user_name}, +1 стикер | {sticker_info}"
        )


    # === 2️⃣ Обработка кружочков (видео) ===
    elif is_round:
        increment_daily_stats(user_id, chat_id, today_str, rounds=1)
        increment_total_stats(user_id, chat_id, rounds=1)
        asyncio.create_task(update_quest_progress(user_id, chat_id, "round", 1, bot))

        if not chat_name:
            chat_name = chat_id
        logging.info(
            f"Обновлена статистика: чат \"{chat_name}\", пользователь {user_name}, +1 📹 кружочек"
        )

    # === 3️⃣ Остальные сообщения ===
    else:
        text = getattr(message, "text", None) or getattr(message, "caption", None)
        if text:
            words = len(text.split())
            chars = len(text)
        else:
            words = 1
            chars = 1

        increment_daily_stats(user_id, chat_id, today_str, messages=1, words=words, chars=chars)
        increment_total_stats(user_id, chat_id, messages=1, words=words, chars=chars)
        asyncio.create_task(update_quest_progress(user_id, chat_id, "messages_sent", 1, bot))

        if not chat_name:
            chat_name = chat_id

        logging.info(
            f"Обновлена статистика: чат \"{chat_name}\", пользователь {user_name}, "
            f"+1 сообщение, +{words} слов, +{chars} символов"
        )



from contextlib import closing
from db import get_connection

def find_user_id_by_nick(chat_id: int, nick: str) -> int | None:
    """Возвращает user_id по нику (@nick) внутри конкретного чата, либо None."""
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE chat_id = ? AND nick = ?", (chat_id, nick))
        row = cur.fetchone()
        return row[0] if row else None



async def daily_punish_task():
    """
    Каждый день в 22:45 применяет реальные штрафы:
    для пользователей с punished==1 — уменьшает вдвое daily за сегодня и total,
    отправляет отчёт в чат и сбрасывает punished.
    """
    while True:
        now = datetime.now()
        punish_time = now.replace(hour=22, minute=45, second=0, microsecond=0)
        if now >= punish_time:
            punish_time += timedelta(days=1)

        wait_seconds = (punish_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        # Получаем всех наказанных пользователей
        with closing(get_connection()) as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id, chat_id FROM users WHERE punished=1")
            punished_users = cur.fetchall()

        for row in punished_users:
            user_id = row["user_id"]
            chat_id = row["chat_id"]

            # Получаем сегодняшнюю daily-статистику
            today_str = datetime.now().strftime("%Y-%m-%d")
            daily = get_daily_stats(user_id, chat_id, today_str)
            if not daily:
                continue

            # Вычисляем отрицательные значения для вычитания половины
            dm = daily["messages"] // 2
            dw = daily["words"] // 2
            dc = daily["chars"] // 2
            ds = daily["stickers"] // 2
            # Умножаем на -1 для вычитания через increment
            increment_daily_stats(user_id, chat_id, today_str,
                                  messages=-dm, words=-dw, chars=-dc, stickers=-ds)

            increment_total_stats(user_id, chat_id,
                                  messages=-dm, words=-dw, chars=-dc, stickers=-ds)

            # Сбрасываем punished
            with closing(get_connection()) as conn:
                cur = conn.cursor()
                cur.execute("UPDATE users SET punished=0 WHERE user_id=? AND chat_id=?", (user_id, chat_id))
                conn.commit()

            # Отправка отчета в чат
            name = get_user(user_id, chat_id)["name"] or str(user_id)
            try:
                await bot.send_message(chat_id,
                    f"Применены штрафы за чрезмерное потребление кофе:\n"
                    f"{name}: -{dm} сообщений, -{dw} слов, -{dc} символов, -{ds} стикеров"
                )
            except Exception as e:
                logging.exception(f"Не удалось отправить сообщение о штрафах в чат {chat_id}: {e}")


# ---------- Награждение топ-3 ----------

async def daily_reward_task():
    while True:
        now = datetime.now()
        reward_time = now.replace(hour=23, minute=45, second=0, microsecond=0)

        # Если текущее время уже позже 23:55, переносим на завтра
        if now >= reward_time:
            reward_time += timedelta(days=1)

        wait_seconds = (reward_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        # Вызываем награждение
        await reward_daily_top(bot)


# ---------- Хэндлеры ----------

@dp.message(Command("weeklytop"))
async def weekly_top(message: types.Message):
    chat_id = message.chat.id

    users = get_chat_users(chat_id)  # ожидается: list[sqlite3.Row] пользователей в этом чате
    if not users:
        await message.reply("Пока нет статистики.")
        return

    totals = []
    for user_row in users:
        user = dict(user_row)  # sqlite3.Row -> dict
        uid = int(user["user_id"])
        # Передаём chat_id
        daily = get_last_7_daily_stats(uid, chat_id, days=7)
        week_msgs = sum(d["messages"] for d in daily)
        name = user.get("name") or "Unknown"
        punished = int(user.get("punished") or 0)
        totals.append((week_msgs, uid, name, punished))

    totals.sort(reverse=True, key=lambda x: x[0])

    text = "🏆 Топ-10 за неделю:\n"
    for i, (count, uid, name, punished) in enumerate(totals[:10], 1):
        display_name = f"{name} ☠️" if punished else name
        text += f"{i}. {display_name} — {count} сообщений\n"

    await message.reply(text)



@dp.message(Command("totaltop"))
async def total_top(message: types.Message):
    chat_id = message.chat.id
    users = get_chat_users(chat_id)
    if not users:
        await message.reply("Пока нет статистики.")
        return

    totals = []
    for user in users:
        uid = user["user_id"]
        total = get_total_stats(uid, chat_id)
        total_msgs = int(total["messages"]) if total else 0
        name = user["name"] or "Unknown"
        punished = int(user["punished"] or 0)
        totals.append((total_msgs, uid, name, punished))

    totals.sort(reverse=True, key=lambda x: x[0])

    text = "📊 Топ-10 за всё время:\n"
    for i, (count, uid, name, punished) in enumerate(totals[:10], 1):
        display_name = f"{name} ☠️" if punished else name
        text += f"{i}. {display_name} — {count} сообщений\n"

    await message.reply(text)



from datetime import date, timedelta

@dp.message(Command("flood"))
async def flood_stats(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    users = get_chat_users(chat_id)
    if not users:
        await message.reply("Пока нет статистики по тебе.")
        return

    # Проверяем, что пользователь есть в списке чата
    if not any(int(u["user_id"]) == user_id for u in users):
        await message.reply("Пока нет статистики по тебе.")
        return

    # дни для недели
    today = date.today()
    # получаем недельную статистику для каждого пользователя
    week_totals = []
    for urow in users:
        u = dict(urow)
        uid = int(u["user_id"])
        daily = get_last_7_daily_stats(uid, chat_id, days=7)
        week_msgs = sum(d["messages"] for d in daily)
        week_totals.append((week_msgs, uid))

    week_totals.sort(reverse=True, key=lambda x: x[0])
    week_position = next((i + 1 for i, (_, uid) in enumerate(week_totals) if uid == user_id), None)
    week_msgs = next((w for w, uid in week_totals if uid == user_id), 0)

    # общее топ-ранжирование
    total_list = []
    for urow in users:
        uid = int(urow["user_id"])
        total = get_total_stats(uid, chat_id)
        total_msgs = int(total["messages"] or 0) if total else 0
        total_list.append((total_msgs, uid))
    total_list.sort(reverse=True, key=lambda x: x[0])
    total_position = next((i + 1 for i, (_, uid) in enumerate(total_list) if uid == user_id), None)
    total_msgs = next((t for t, uid in total_list if uid == user_id), 0)

    # Пользовательские данные
    user_row = get_user(user_id, chat_id)
    user = dict(user_row) if user_row else {}
    name = user.get("name") or message.from_user.full_name
    if int(user.get("punished", 0) or 0):
        name = f"{name} ☠️"

    # Кофе берем из total_stats
    total_stats = get_total_stats(user_id, chat_id)
    total_coffee = int(total_stats["coffee"] or 0) if total_stats else 0

    # Баланс sits
    sits_balance = int(user.get("sits") or 0)
    dick_stats = dick.get_dick(user_id, chat_id)
    dick_length = int(dick_stats.get("length") or 0)

    text = (
        f"📈 Личная статистика для {name}:\n"
        f"За неделю: {week_msgs} сообщений (место #{week_position})\n"
        f"Всего: {total_msgs} сообщений (место #{total_position})"
    )
    text += f"\n☕️ Всего кофе: {total_coffee}"
    text += f"\n🍆 Длина члена: {dick_length} см"
    if sits_balance > 0:
        text += f"\n💦 Баланс сита: {sits_balance}"

    await message.reply(text)


@dp.message(Command("polina_cum_win_on"))
async def polina_cum_win_on(message: types.Message):
    await message.reply("Теперь Полина будет выигрывать сит. Или не будет")


@dp.message(Command("polina_cum_win_off"))
async def polina_cum_win_off(message: types.Message):
    await message.reply("Теперь Полина не будет выигрывать сит. Или будет")


@dp.message(Command("shop"))
async def show_shop(message: types.Message):
    balance = get_sits(message.chat.id, message.from_user.id)
    await message.answer(
        "🏪 Магазинчик Дяди Доктора\n"
        f"Твой баланс: {balance} сит\n\n"
        "Выбирай товар:",
        reply_markup=build_shop_keyboard()
    )


@dp.message(Command("makovka"))
async def send_makovka(message: types.Message):
    """
    Отправляет в чат заранее определённый стикер.
    """
    await message.answer_sticker(MAKOVKA_FILE_ID)

from chat_stat import get_weekly_chat_stats
from aiogram import types
from aiogram.filters import Command

@dp.message(Command("stat"))
async def send_stat(message: types.Message):
    chat_id = message.chat.id
    await message.answer(get_weekly_chat_stats(chat_id))

def get_weekly_message_totals(chat_id: int, user_id: int, weeks: int = 26) -> tuple[list[str], list[int]]:
    end_date = date.today()
    start_date = end_date - timedelta(weeks=weeks - 1)
    start_monday = start_date - timedelta(days=start_date.weekday())

    week_starts = []
    current = start_monday
    while current <= end_date:
        week_starts.append(current)
        current += timedelta(weeks=1)

    with closing(get_connection()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT date(date, 'weekday 1', '-7 days') AS week_start,
                   SUM(messages) AS messages
            FROM daily_stats
            WHERE user_id = ? AND chat_id = ? AND date >= ?
            GROUP BY week_start
            ORDER BY week_start
        """, (user_id, chat_id, start_monday.isoformat()))
        rows = cur.fetchall()

    totals_by_week = {row[0]: int(row[1] or 0) for row in rows}
    labels = [week_start.strftime("%d.%m") for week_start in week_starts]
    values = [totals_by_week.get(week_start.isoformat(), 0) for week_start in week_starts]
    return labels, values

@dp.message(Command("graph"))
async def send_graph(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    labels, values = get_weekly_message_totals(chat_id, user_id)

    if not any(values):
        await message.answer("Нет данных по сообщениям за последние 6 месяцев.")
        return

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.bar(range(len(values)), values, color="#5B8FF9")
    ax.set_title("Сообщения по неделям (последние 6 месяцев)")
    ax.set_ylabel("Количество сообщений")
    ax.set_xlabel("Недели")
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    step = max(1, len(labels) // 10)
    ticks = list(range(0, len(labels), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([labels[i] for i in ticks], rotation=45, ha="right")

    fig.tight_layout()
    image_buffer = io.BytesIO()
    fig.savefig(image_buffer, format="png", dpi=150)
    plt.close(fig)
    image_buffer.seek(0)

    photo = BufferedInputFile(image_buffer.read(), filename="graph.png")
    user_name = message.from_user.full_name or message.from_user.username or "пользователя"
    await message.answer_photo(
        photo,
        caption=f"📊 Сообщения по неделям за последние 6 месяцев для {user_name}",
    )


import random

# Список ID стикеров
STICKERS = [
    "CAACAgIAAyEFAASjKavKAAICp2iy5hML1eFnIZwuKLpEpl9kmpfjAALwcAACZfRISVXIMpVstJbWNgQ",
    "CAACAgIAAyEFAASjKavKAAICqGiy5ik08bQH5g9omzfd7PBs7Z9WAALuPQACkhZpSxMWB6aTq90jNgQ",
    "CAACAgIAAyEFAASjKavKAAICqmiy5kLEuAKILCRckR7jDGGBM74QAAJJBQACIwUNAAEQwqY-etbwdDYE",
    "CAACAgIAAyEFAASjKavKAAICrWiy5mJIsVI1nVFUa-7JsyIol_hKAALLTgACphTRSjS9R-8OrOiBNgQ"
]
#Награда Виталику за каждые 300 стикеров
async def send_reaction_reward(bot: Bot, chat_id: int, user_id: int, total: int):
    # Выбираем случайный стикер
    sticker_id = random.choice(STICKERS)

    await bot.send_sticker(chat_id, sticker_id)
    await bot.send_message(
        chat_id,
        f"🎉 @Thehemyl Виталик, держи зарплату за лайки ❤️",
        parse_mode="Markdown"
    )

from aiogram.filters import Command
from aiogram.types import Message

@dp.message(Command("regenerate"))
async def regenerate_usernames(message: Message):
    with get_connection() as conn:
        cur = conn.cursor()
        # Пробегаем по всем юзерам в таблице users
        cur.execute("SELECT user_id, chat_id FROM users")
        rows = cur.fetchall()
        for row in rows:
            user_id, chat_id = row
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                full_name = member.user.full_name
                # Обновляем имя в таблице users
                cur.execute("UPDATE users SET name=? WHERE user_id=? AND chat_id=?", (full_name, user_id, chat_id))
            except Exception:
                logging.warning(f"Не удалось получить пользователя {user_id} в чате {chat_id}")
        conn.commit()
    await message.answer("Имена пользователей обновлены.")


# --- Меню лайков ---
def build_likes_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="Топ залайканых за неделю", callback_data="likes:weekly_top")],
        [InlineKeyboardButton(text="Топ залайканых за всё время", callback_data="likes:alltime_top")],
        [InlineKeyboardButton(text="Топ добряков недели", callback_data="likes:weekly_givers")],
        [InlineKeyboardButton(text="Топ добряков за всё время", callback_data="likes:alltime_givers")],
        [InlineKeyboardButton(text="Топ-5 сообщений недели", callback_data="likes:weekly_msgs")],
        [InlineKeyboardButton(text="Топ-5 сообщений за всё время", callback_data="likes:alltime_msgs")],
        [InlineKeyboardButton(text="Статистика чата", callback_data="likes:chat_stats")],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(Command("top_stickers"))
async def top_stickers(message: types.Message):
    chat_id = message.chat.id

    # парсим лимит: /top_stickers 42 -> 42, по умолчанию 5
    args = message.text.strip().split()
    try:
        limit = int(args[1]) if len(args) > 1 else 5
    except ValueError:
        limit = 5
    limit = max(1, min(limit, 100))  # защитимся от крайностей

    # достаём топ N, суммируя счётчики по всем датам
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT file_id, SUM(count) as total_count
            FROM sticker_stats
            WHERE chat_id = ?
            GROUP BY file_id
            ORDER BY total_count DESC, file_id ASC
            LIMIT ?
        """, (chat_id, limit))
        rows = cur.fetchall()

    if not rows:
        await message.answer("В этом чате пока нет статистики по отслеживаемым стикерам.")
        return

    await message.answer(f"🏆 Топ-{len(rows)} популярных стикеров (подпись → стикер):")

    # для каждого: сначала текст-«подпись», затем стикер как reply на неё
    for i, (file_id, total_count) in enumerate(rows, start=1):
        caption_msg = await message.answer(f"{i}. Использовали {total_count} раз(а)")
        try:
            await message.bot.send_sticker(
                chat_id=chat_id,
                sticker=file_id,
                reply_to_message_id=caption_msg.message_id
            )
        except Exception:
            await message.answer(f"(не удалось отправить стикер {file_id})")


@dp.message(Command("like"))
async def cmd_like(message: Message):
    await message.answer(
        "❤️ Самая добрая статистика про ваши лайки ❤️",
        reply_markup=build_likes_keyboard()
    )

# --- Обработчик кнопок меню лайков ---
@dp.callback_query(F.data.startswith("likes:"))
async def likes_menu_callback(callback_query: CallbackQuery):
    chat_id = callback_query.message.chat.id
    data = callback_query.data

    # удаляем старое сообщение с меню
    try:
        await callback_query.message.delete()
    except Exception:
        pass  # игнорируем если сообщение уже удалено

    text = ""
    with get_connection() as conn:
        cur = conn.cursor()

        if data == "likes:weekly_top":
            cur.execute("""
                SELECT u.name, SUM(d.react_taken) as likes
                FROM users u
                JOIN daily_stats d ON u.user_id = d.user_id AND u.chat_id = d.chat_id
                WHERE u.chat_id = ? AND d.date >= date('now','-6 days')
                GROUP BY u.user_id
                ORDER BY likes DESC
                LIMIT 10
            """, (chat_id,))
            rows = cur.fetchall()
            text = "🏆 Топ получателей лайков за неделю:\n"
            text += "\n".join([f"{i + 1}. {name} — {likes} ❤️" for i, (name, likes) in enumerate(rows)])

        elif data == "likes:alltime_top":
            cur.execute("""
                SELECT u.name, t.react_taken
                FROM total_stats t
                JOIN users u ON u.user_id = t.user_id AND u.chat_id = t.chat_id
                WHERE t.chat_id = ?
                ORDER BY t.react_taken DESC
                LIMIT 10
            """, (chat_id,))
            rows = cur.fetchall()
            text = "🏆 Топ получателей лайков за всё время:\n"
            text += "\n".join([f"{i + 1}. {name} — {likes} ❤️" for i, (name, likes) in enumerate(rows)])


        elif data == "likes:weekly_givers":
            cur.execute("""
                SELECT u.name, SUM(d.react_given) as likes
                FROM users u
                JOIN daily_stats d ON u.user_id = d.user_id AND u.chat_id = d.chat_id
                WHERE u.chat_id = ? AND d.date >= date('now','-6 days')
                GROUP BY u.user_id
                ORDER BY likes DESC
                LIMIT 10
            """, (chat_id,))
            rows = cur.fetchall()
            text = "💖 Топ добряков недели:\n"
            text += "\n".join([f"{i + 1}. {name} — {likes} ❤️" for i, (name, likes) in enumerate(rows)])

        elif data == "likes:alltime_givers":
            cur.execute("""
                SELECT u.name, t.react_given
                FROM total_stats t
                JOIN users u ON u.user_id = t.user_id AND u.chat_id = t.chat_id
                WHERE t.chat_id = ?
                ORDER BY t.react_given DESC
                LIMIT 10
            """, (chat_id,))
            rows = cur.fetchall()
            text = "💖 Топ добряков за всё время:\n"
            text += "\n".join([f"{i + 1}. {name} — {likes} ❤️" for i, (name, likes) in enumerate(rows)])

        elif data == "likes:weekly_msgs":
            cur.execute("""
                SELECT message_id, reactions_count, message_text
                FROM messages_reactions
                WHERE chat_id = ? AND date >= date('now','-6 days')
                ORDER BY reactions_count DESC
                LIMIT 5
            """, (chat_id,))
            rows = cur.fetchall()
            text = "💬 Топ-5 сообщений недели:\n"
            for message_id, react_taken, msg_text in rows:
                link = f"https://t.me/c/{str(chat_id)[4:]}/{message_id}"
                snippet = (msg_text[:50] + "...") if msg_text else ""
                text += f"❤️ {react_taken} — {link} — {snippet}\n"

        elif data == "likes:alltime_msgs":
            cur.execute("""
                SELECT message_id, reactions_count, message_text
                FROM messages_reactions
                WHERE chat_id = ?
                ORDER BY reactions_count DESC
                LIMIT 5
            """, (chat_id,))
            rows = cur.fetchall()
            text = "💬 Топ-5 сообщений за всё время:\n"
            for message_id, react_taken, msg_text in rows:
                link = f"https://t.me/c/{str(chat_id)[4:]}/{message_id}"
                snippet = (msg_text[:50] + "...") if msg_text else ""
                text += f"❤️ {react_taken} — {link} — {snippet}\n"

        elif data == "likes:chat_stats":
            cur.execute("""
                SELECT SUM(react_taken) as week_likes, SUM(messages) as week_msgs
                FROM daily_stats
                WHERE chat_id = ? AND date >= date('now','-6 days')
            """, (chat_id,))
            week_likes, week_msgs = cur.fetchone()
            week_avg = week_likes / week_msgs if week_msgs else 0

            cur.execute("""
                SELECT SUM(react_taken) as all_likes, SUM(messages) as all_msgs
                FROM total_stats
                WHERE chat_id = ?
            """, (chat_id,))
            all_likes, all_msgs = cur.fetchone()
            all_avg = all_likes / all_msgs if all_msgs else 0

            text = (
                f"📊 Статистика чата:\n"
                f"За неделю: {week_likes} лайков, ср. на сообщение {week_avg:.2f}\n"
                f"За всё время: {all_likes} лайков, ср. на сообщение {all_avg:.2f}"
            )
    # отправляем новое сообщение с результатом через бота
    await bot.send_message(chat_id, text)
    # отвечаем на callback, чтобы кнопка визуально отпустилась
    await callback_query.answer()


from aiogram import types
from aiogram.filters import Command

@dp.message(Command("charity"))
async def charity_command(message: types.Message):
    import logging
    from db import get_user_display_name

    admin_ids = ADMIN_IDS  # Используем импортированный ADMIN_IDS

    caller_id = message.from_user.id
    logging.info(f"[charity] Команда вызвана пользователем {caller_id} ({message.from_user.username})")

    if caller_id not in admin_ids:
        await message.answer("Команда только для администраторов доната")
        return

    # Берём текст команды (если есть фото/документ → caption)
    text = message.text or message.caption
    if not text:
        await message.answer("Ошибка: не удалось распознать команду.")
        return

    args = text.strip().split()
    logging.info(f"[charity] Получены аргументы: {args}")

    if len(args) < 3:
        await message.answer("Ошибка: нужно указать user_id или @ник и количество ситов.\nПример: /charity 884940984 50 или /charity @nickname 50")
        return

    target_arg = args[1]
    amount_arg = args[2]

    # Определяем user_id цели
    target_user_id = None
    if target_arg.startswith("@"):
        #target_nick = target_arg[1:]
        target_user_id = find_user_id_by_nick(message.chat.id, target_arg)
        if not target_user_id:
            await message.answer("Ошибка: не удалось найти пользователя по нику в базе.")
            return
    else:
        try:
            target_user_id = int(target_arg)
        except ValueError:
            await message.answer("Ошибка: user_id должен быть числом.")
            return

    # Количество ситов
    try:
        amount = int(amount_arg)
    except ValueError:
        await message.answer("Ошибка: количество ситов должно быть числом.")
        return

    # Начисляем ситы
    add_sits(message.chat.id, target_user_id, amount)

    # Получаем имя пользователя для упоминания
    target_name = get_user_display_name(target_user_id, message.chat.id)

    await message.answer(f"Спасибо {target_name} за доброе дело! {amount} сита начислено")
    logging.info(f"[charity] Начислено {amount} сита пользователю {target_user_id} ({target_name})")





@dp.message(Command("give"))
async def handle_give(message: types.Message):
    chat_id = message.chat.id
    sender_id = message.from_user.id

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("❌ Использование: /give @nick amount\nПример: /give @vasya 3")
        return

    nick_raw = parts[1].strip()
    amount_raw = parts[2].strip()

    if not nick_raw.startswith("@") or len(nick_raw) < 2:
        await message.answer("❌ Укажи ник в формате @username")
        return

    try:
        amount = int(amount_raw)
    except ValueError:
        await message.answer("❌ Сумма должна быть целым числом")
        return

    if amount < 0:
        await message.answer("🚫 Нет, мы закрыли эту дыру в безопасности.")
        return
    if amount == 0:
        await message.answer("ℹ️ Ноль сит? Операция бессмысленна, ничего не перевожу.")
        return

    receiver_id = find_user_id_by_nick(chat_id, nick_raw)
    if receiver_id is None:
        await message.answer(
            "❌ Пользователь с таким ником не найден в базе этого чата.\n"
            "Попроси его написать хоть одно сообщение, чтобы я запомнил ник."
        )
        return

    if receiver_id == sender_id:
        await message.answer("🤔 Самому себе переводить смысла нет.")
        return

    from sosalsa import get_sits
    balance = get_sits(chat_id, sender_id)
    if balance < amount:
        await message.answer(f"❌ Недостаточно сит. Нужно: {amount}, у тебя: {balance}")
        return

    # Списываем/начисляем
    add_sits(chat_id, sender_id, -amount)
    add_sits(chat_id, receiver_id, amount)

    sender_name = get_user_display_name(sender_id, chat_id)
    receiver_name = get_user_display_name(receiver_id, chat_id)

    # Определяем глагол по полу отправителя
    sender_sex = get_user_sex(sender_id, chat_id)
    verb = "передала" if sender_sex == "f" else "передал"

    await message.answer(
        f"💦 {sender_name} {verb} {amount} сит пользователю {receiver_name} {nick_raw}."
    )



# --- /all ---
@dp.message(Command("all"))
async def cmd_all(message: types.Message):

    now_hour = datetime.now().hour
    if now_hour < 9:
        await message.answer("Сейчас слишком поздно чтобы всех звать. Попробуй после 9 утра")
        return

    chat_id = message.chat.id
    user_name = message.from_user.full_name

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT nick FROM users
            WHERE chat_id=? AND is_all=1 AND nick IS NOT NULL AND nick != ''
        """, (chat_id,))
        rows = cur.fetchall()

    if not rows:
        await message.answer("Никого не удалось собрать 😅. Добавь себя через /addme")
        return

    nicks = " ".join([row["nick"] for row in rows])
    text = (
        f"{user_name} решил всех собрать!\n"
        f"{nicks}\n\n"
        "Хочешь чтобы тебя тоже звали этой командой? Пиши /addme\n"
        "Хочешь удалить себя из этого списка? Жми /deleteme"
    )
    await message.answer(text)

# --- /addme ---
@dp.message(Command("addme"))
async def cmd_addme(message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        user = cur.fetchone()

    if not user:
        await message.answer("Сначала бот должен знать о вас. Отправьте любое сообщение.")
        return

    # безопасно достаём ник
    nick = user["nick"] if "nick" in user and user["nick"] else ""
    add_or_update_user(user_id, chat_id, is_all=1)
    await message.answer("✅ Вы добавлены в список /all")

# --- /deleteme ---
@dp.message(Command("deleteme"))
async def cmd_deleteme(message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=? AND chat_id=?", (user_id, chat_id))
        user = cur.fetchone()

    if not user:
        await message.answer("Вы ещё не известны боту 😅")
        return

    # безопасно достаём ник
    nick = user["nick"] if "nick" in user and user["nick"] else ""
    add_or_update_user(user_id, chat_id, is_all=0)
    await message.answer("❌ Вы удалены из списка /all")




# ------------------------------
# ------------------------------
# Когда новое сообщение
@dp.message()
async def handle_message(message: types.Message):

    if message.text and message.text.startswith("/"):
        return

    if not (
            message.text or message.sticker or message.photo or message.video or message.voice or message.animation or message.video_note):
        return

    chat_name = message.chat.title if message.chat.type in ["group", "supergroup"] else message.chat.id

    # Передаём в старую статистику
    update_stats(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        user_name=message.from_user.full_name,
        message=message,
        chat_name=chat_name
    )

    # ---- Добавляем сообщение в базу для реакций ----
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO messages_reactions
            (chat_id, message_id, user_id, message_text, reactions_count, date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            message.chat.id,
            message.message_id,
            message.from_user.id,
            message.text or "",
            0,
            datetime.now().isoformat() # Записываем локальное время сервера
        ))
        conn.commit()

    # проверка на тише мужло
    await handle_mujlo_message(message)


from datetime import date

# ------------------------------
# Когда пользователь изменяет свои реакции
# ------------------------------
@dp.message_reaction()
async def on_reaction(event: MessageReactionUpdated):
    chat_id = event.chat.id
    msg_id = event.message_id
    user_id = event.user.id if event.user else None

    old = [r.type for r in event.old_reaction] if event.old_reaction else []
    new = [r.type for r in event.new_reaction] if event.new_reaction else []

    logging.info(
        f"В чате '{event.chat.title or 'личный чат'}' пользователь {event.user.full_name if event.user else 'неизвестный'} "
        f"поменял реакции на сообщение {msg_id}: {new} (старые: {old})"
    )

    if not user_id:
        return  # анонимные реакции игнорируем

    delta_given = len(new) - len(old)   # сколько реакций поставлено или снято
    today = date.today()

    with get_connection() as conn:
        cur = conn.cursor()

        # Получаем автора сообщения
        cur.execute(
            "SELECT user_id, reactions_count FROM messages_reactions WHERE chat_id=? AND message_id=?",
            (chat_id, msg_id)
        )
        row = cur.fetchone()
        if not row:
            logging.warning(f"Сообщение {msg_id} не найдено в базе")
            return
        author_id, current_count = row

        # --- Обновляем счётчики ---
        # 1) Сообщение
        new_count = current_count + delta_given
        cur.execute(
            "UPDATE messages_reactions SET reactions_count=? WHERE chat_id=? AND message_id=?",
            (new_count, chat_id, msg_id)
        )

        # 2) Отправленные реакции у того, кто ставит реакцию
        cur.execute("""
            INSERT INTO daily_stats (chat_id, user_id, date, react_given)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id, date) DO UPDATE SET react_given = react_given + ?
        """, (chat_id, user_id, today, delta_given, delta_given))
        cur.execute("""
            INSERT INTO total_stats (chat_id, user_id, react_given)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET react_given = react_given + ?
        """, (chat_id, user_id, delta_given, delta_given))

        #отправка события в обработчик квестов на отправленные лайки
        asyncio.create_task(update_quest_progress(user_id, chat_id, "likes_given", 1, bot))

        global last_reward_react_given
        # --- Проверка на достижение кратности 300 реакций для конкретного пользователя ---
        cur.execute("""
            SELECT react_given FROM total_stats
            WHERE chat_id=? AND user_id=?
        """, (chat_id, user_id))
        row = cur.fetchone()
        if row:
            total_react_given = row[0]
            global last_reward_react_given

            # Проверяем: пользователь нужный, достигнут новый порог, и награда ещё не выдавалась за него
            if user_id == 765591886 and total_react_given % 400 == 0 and total_react_given > last_reward_react_given:
                await send_reaction_reward(bot, chat_id, user_id, total_react_given)
                last_reward_react_given = total_react_given  # Запоминаем порог

        # 3) Полученные реакции у автора
        cur.execute("""
            INSERT INTO daily_stats (chat_id, user_id, date, react_taken)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id, date) DO UPDATE SET react_taken = react_taken + ?
        """, (chat_id, author_id, today, delta_given, delta_given))
        cur.execute("""
            INSERT INTO total_stats (chat_id, user_id, react_taken)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET react_taken = react_taken + ?
        """, (chat_id, author_id, delta_given, delta_given))

        # отправка события в обработчик квестов на полученные лайки
        asyncio.create_task(update_quest_progress(author_id, chat_id, "likes_received", 1, bot))

        conn.commit()



# ------------------------------
# Когда обновляется общий счётчик реакций (например, анонимные)
# ------------------------------
@dp.message_reaction_count()
async def on_reaction_count(event: MessageReactionCountUpdated):
    chat_id = event.chat.id
    msg_id = event.message_id
    total = sum(r.count for r in event.reactions)
    reactions_text = ", ".join(f"{r.type}: {r.count}" for r in event.reactions)

    logging.info(
        f"В чате '{event.chat.title or 'личный чат'}' сообщение {msg_id} теперь имеет реакции: {reactions_text}. "
        f"Общее количество: {total}"
    )

    # Обновляем reactions_count в таблице сообщений
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE messages_reactions SET reactions_count=? WHERE chat_id=? AND message_id=?",
                    (total, chat_id, msg_id))
        conn.commit()

#склонение сита
def sit_word(n: int) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "сит"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "сита"
    return "сит"


#получение баланса сита
def get_sits(chat_id: int, user_id: int) -> int:
    from db import get_user
    user = get_user(user_id, chat_id)
    if user and user["chat_id"] == chat_id:
        return user["sits"] or 0
    return 0



def spend_sits(chat_id: int, user_id: int, amount: int) -> tuple[bool, int]:
    """
    Пытается списать amount сит.
    Возвращает (успех: bool, новый_или_текущий_баланс: int).
    """
    user = get_user(user_id, chat_id)
    if user and user["chat_id"] == chat_id:
        current = user["sits"] or 0
        if current >= amount:
            new_balance = current - amount
            add_or_update_user(user_id, chat_id, user["name"], sits=new_balance)
            return True, new_balance
        else:
            return False, current
    else:
        # создаем пользователя с нулевым балансом, если нет
        add_or_update_user(user_id, chat_id, "", sits=0)
        return False, 0

# Разрешённые user_id для использования команды
# ADMIN_IDS = {6010666986, 884940984, 749027951} # Удаляем старое определение
from settings import ADMIN_IDS # Импортируем ADMIN_IDS из settings.py

#клавиатура магазина сита
def build_shop_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for key, item in SHOP_ITEMS.items():
        buttons.append([InlineKeyboardButton(
            text=f"{item['name']} ({item['price']} сит)",
            callback_data=f"shop:buy:{key}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------- Обработка нажатия кнопок магазина ----------
@dp.callback_query(F.data.startswith("shop:buy:"))
async def handle_shop_buy(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    user_name = callback.from_user.full_name

    item_key = callback.data.split(":")[-1]
    item = SHOP_ITEMS.get(item_key)

    if not item:
        await callback.answer("❌ Товар не найден", show_alert=True)
        return

    action = item.get("action")
    try:
        if action == "send_spider":
            await action_send_spider(callback, item)
            return
        if action == "drink_coffee":
            await action_drink_coffee(callback, item)
            return
        if action == "group":
            from group import start_group_event
            await callback.message.delete()
            await start_group_event(callback.message, callback.from_user.id)
            return

        price = item["price"]
        ok, new_balance = spend_sits(chat_id, user_id, price)

        if ok:
            buy_text = item["buy_text"].format(user_name=user_name)
            try:
                await callback.message.edit_text(f"{buy_text}\nОстаток: {new_balance} сит")
            except Exception as e:
                logging.debug(f"Не удалось отредактировать сообщение магазина: {e}")
            logging.info(f"{user_name} купил '{item['name']}' за {price} сит в чате {chat_id}. Остаток: {new_balance}")
            await callback.answer()
        else:
            await callback.answer(f"❌ Недостаточно сит. Твой баланс: {new_balance}", show_alert=True)
    except Exception as e:
        logging.exception(f"Ошибка при покупке товара: {e}")
        await callback.answer("❌ Произошла ошибка при покупке.", show_alert=True)


# ---------- Покупка/выпивание кофе ----------
async def action_drink_coffee(callback: types.CallbackQuery, item: dict):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    user_name = callback.from_user.full_name or str(user_id)

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    daily = get_daily_stats(user_id, chat_id, today_str)
    n = daily["coffee"] if daily else 0

    try:
        # 0) Проверка времени
        if 22 <= now.hour:
            await callback.answer(f"После 22:00 фильтр больше не наливают, {user_name} ☕️❌", show_alert=True)
            return

        user = get_user(user_id, chat_id)
        if user and user["punished"] == 1:
            await callback.answer(f"Дно уже прорвано, на сегодня тебе хватит, {user_name}", show_alert=True)
            return

        sex = get_user_sex(user_id, chat_id)

        increment_daily_stats(user_id, chat_id, today_str, coffee=1)
        increment_total_stats(user_id, chat_id, coffee=1)
        n += 1

        buy_text_template = item.get("buy_text")
        if isinstance(buy_text_template, dict):
            base_text = buy_text_template.get("f") if sex == "f" else buy_text_template.get("m")
        else:
            base_text = buy_text_template or "{user_name} купил вещь"

        coffee_emoji = "☕️" * n  # генерируем строку с количеством кружек = числу кофе
        buy_text = base_text.format(user_name=user_name).replace("☕️☕️☕️", coffee_emoji)

        if n >= 2:
            buy_text += " ...в животе начинает бурчать"

        try:
            await callback.message.edit_text(buy_text)
        except Exception as e:
            logging.debug(f"Не удалось отредактировать сообщение магазина: {e}")

        # Шанс штрафа
        punished_now = False
        if n > 2:
            chance = 1 - math.exp(-0.21 * (n - 2))
            punished_now = random.random() < chance

        if punished_now:
            add_or_update_user(user_id, chat_id, user_name, punished=1)
            msg = f"💀 Дно прорвано! До конца дня {user_name} получает штраф — его сообщения будут считаться наполовину"
            await callback.message.answer(msg)
            logging.info(f"{user_name} получил флаг punished (кофе {n}) в чате {chat_id}")
            await callback.answer()
            from quest import update_quest_progress
            await update_quest_progress(user_id, chat_id, "coffee_fail", 1, bot=bot)

            return

        if n >= 4:
            add_sits(chat_id, user_id, 1)
            new_bal = get_user(user_id, chat_id)["sits"]
            msg = f"{user_name} получил 1 сит за фильтр. Остаток: {new_bal} сит"
            await callback.message.answer(msg)
            from quest import update_quest_progress
            if n >= 5:
                asyncio.create_task(update_quest_progress(user_id, chat_id, "coffee_safe", 1, bot))
            return



    except Exception as e:
        logging.exception(f"Ошибка при действии drink_coffee: {e}")
        await callback.answer("❌ Произошла ошибка при покупке кофе.", show_alert=True)
        return

    # ✅ гарантируем callback.answer() для всех остальных случаев
    await callback.answer()


# ---------- Отправка паука ----------
async def action_send_spider(callback: types.CallbackQuery, item: dict):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    user_name = callback.from_user.full_name or str(user_id)
    price = int(item.get("price", 0))
    is_tass = (user_name.strip().lower() == "tass") or ((callback.from_user.username or "").lower() == "tass")
    new_balance = None

    try:
        if not is_tass and price > 0:
            ok, new_balance = spend_sits(chat_id, user_id, price)
            if not ok:
                await callback.answer(f"❌ Недостаточно сит. Твой баланс: {get_sits(chat_id, user_id)}", show_alert=True)
                return

        file_path = item.get("file", "images/spider.jpg")
        if not os.path.isabs(file_path):
            file_path = os.path.join(os.path.dirname(__file__), file_path)

        caption = item.get("buy_text", "{user_name} купил вещь").format(user_name=user_name)
        if is_tass:
            caption = f"Tass, для тебя этот товар всегда бесплатно\n{caption}"

        photo = FSInputFile(file_path)
        await callback.message.answer_photo(photo, caption=caption)

        if new_balance is None:
            new_balance = get_sits(chat_id, user_id)

        confirmation = f"✅ {user_name}, вы купили паука за {price} {sit_word(price)}. Остаток: {new_balance} сит"
        if is_tass:
            confirmation = f"🎁 {user_name}, для тебя этот товар был бесплатным — паук в чате!"

        try:
            await callback.message.edit_text(confirmation)
        except Exception as e:
            logging.debug(f"Не удалось отредактировать сообщение магазина: {e}")

    except FileNotFoundError:
        logging.exception(f"Файл товара не найден: {file_path}")
        if not is_tass and price > 0:
            add_sits(chat_id, user_id, price)
        await callback.answer("❌ Ошибка: файл товара не найден на сервере.", show_alert=True)
        return
    except Exception as e:
        logging.exception(f"Ошибка при отправке паука: {e}")
        if not is_tass and price > 0:
            add_sits(chat_id, user_id, price)
        await callback.answer("❌ Произошла ошибка при отправке товара.", show_alert=True)
        return

    # ✅ гарантируем callback.answer()
    await callback.answer()


async def reward_daily_top(bot: Bot):
    """
    Награждает топ-3 пользователей по количеству сообщений за текущий день.
    Начисляет ситы: 1 место — 2, 2-3 места — 1.
    """
    from datetime import date
    today_str = date.today().isoformat()
    from db import get_chat_users, get_daily_stats, get_user, add_or_update_user

    # Получаем список всех чатов
    # Здесь нужно явно перечислить chat_id ваших чатов или хранить их в БД
    chat_ids = get_all_chats() # get_all_chats() — функция, возвращающая все чаты

    for chat_id in chat_ids:
        users = get_chat_users(chat_id)  # list[sqlite3.Row] пользователей чата
        if not users:
            continue

        user_counts = []
        for user_row in users:
            uid = int(user_row["user_id"])
            user = get_user(uid, chat_id)
            daily = get_daily_stats(uid, chat_id, today_str)
            if not user or not daily:
                continue

            messages = daily["messages"] if daily else 0
            if messages > 0:
                name = user["name"] or str(uid)
                user_counts.append((uid, messages, name))

        if not user_counts:
            continue

        # Сортируем по сообщениям за сегодня и берём топ-3
        user_counts.sort(key=lambda x: x[1], reverse=True)
        top3 = user_counts[:3]
        rewards = [2, 1, 1]

        text_lines = ["За ежедневный вклад во флуд в чяте награждаются:"]
        for i, (uid, count, name) in enumerate(top3):
            amount = rewards[i]
            # Добавляем ситы
            add_sits(chat_id, uid, amount=amount)
            text_lines.append(f"{i + 1} место — {name} — {amount} сит")

        # Отправка сообщения в чат
        try:
            await bot.send_message(chat_id, "\n".join(text_lines))
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение в чат {chat_id}: {e}")

async def new_year_scheduler(bot):
    """
    Раз в минуту проверяет — пора ли запускать новогодний скрипт.
    Сам run_new_year защищён от повторного запуска.
    """
    while True:
        try:
            await run_new_year(bot)
        except Exception as e:
            # логируй, если есть логгер
            print(f"[new_year] error: {e}")

        await asyncio.sleep(60)



# ---------- Запуск ----------

weekly_awards.bot = bot
weekly_awards.add_sits = add_sits

async def main():
    # Запускаем фоновые задачи
    asyncio.create_task(daily_reward_task())  # награждение в 23:55
    asyncio.create_task(weekly_awards.weekly_awards_task())  # еженедельные награды
    asyncio.create_task(daily_punish_task())  # Ежедневное наказание за кофе
    asyncio.create_task(silence_checker_task())
    asyncio.create_task(mujlo.reset_mujlo_daily())  # сброс покупок мужла по утру
    asyncio.create_task(daily_reminder_loop(bot))
    # Ежедневная регенерация частей тела
    # Передаем объект бота в модуль
    daily_bot = bot  # bot — объект Bot из aiogram
    sosalsa.bot = bot
    # Запускаем фоновую задачу
    asyncio.create_task(daily_regeneration_task())
    # Задачи для гейзера
    asyncio.create_task(geyser.schedule_daily_geysers(bot)) # Ежедневное планирование гейзеров (долгоживущая корутина)
    asyncio.create_task(geyser.geyser_loop_task(bot)) # Непрерывный цикл для запуска гейзеров
    asyncio.create_task(new_year_scheduler(bot))

    # Цикл polling с автоперезапуском при ошибках
    while True:
        try:
            await dp.start_polling(
                bot,
                allowed_updates=["message", "callback_query", "message_reaction", "message_reaction_count"]
            )
        except (TelegramNetworkError, TelegramServerError) as e:
            logging.warning(f"Ошибка Telegram: {e}. Перезапуск polling через 5 секунд...")
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            logging.info("Polling остановлен по сигналу CancelledError")
            break
        except Exception as e:
            logging.exception(f"Неожиданная ошибка: {e}. Перезапуск polling через 5 секунд...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
