import json
import os
import asyncio
import re
from datetime import datetime, time, timedelta
from collections import defaultdict
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
import logging
from aiogram import types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types import FSInputFile, CallbackQuery
import aiocron
import math
import random
import weekly_awards
import sticker_manager
import sqlite3
import db
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
    get_user_sex
)
from reactions import router as reactions_router




TOKEN = "7566137789:AAGmm_djHOuqiL2WvAkKHuGoIfnkuPMLepY"
STATS_FILE = "stats.json"
MAKOVKA_FILE_ID = "CAACAgIAAyEFAASjKavKAAOcaJ95ivqdgkA5gstkAbRt25CCRLAAAkN5AAJTNbFKdWJ4ufamt9I2BA"

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
    }
}



logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


bot = Bot(token=TOKEN)
dp = Dispatcher()

# Подключаем роутер с реакциями
dp.include_router(reactions_router)

def ensure_user(chat_id: int, user_id: int, user_name: str):
    """
    Гарантирует, что пользователь есть в БД и все записи корректны.
    Создаёт пользователя, daily_stats за последние 7 дней и total_stats при необходимости.
    """
    # Проверяем пользователя
    user = db.get_user(user_id)
    if not user:
        db.add_or_update_user(user_id, chat_id, user_name, sits=0, punished=0, sex=None)
    else:
        # Обновляем имя, если пустое или изменилось
        if user["name"] != user_name:
            db.add_or_update_user(user_id, chat_id, user_name)

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
    if not db.get_total_stats(user_id):
        db.add_or_update_total_stats(user_id, chat_id, messages=0, words=0, chars=0, stickers=0, coffee=0)


from datetime import datetime
from datetime import date
import logging
import db  # предполагаем, что все функции из db.py доступны

def update_stats(chat_id, user_id, user_name, message, chat_name=None):
    """
    Обновляет статистику сообщения напрямую в БД.
    Если это стикер — увеличиваем только 'stickers'.
    Иначе — обновляем messages/words/chars как раньше.
    """
    # Гарантируем пользователя в БД
    add_or_update_user(user_id, chat_id, user_name)

    # Определяем дату сегодня
    today_str = date.today().isoformat()

    # Определяем, является ли сообщение стикером
    is_sticker = getattr(message, "sticker", None) is not None

    if is_sticker:
        # Увеличиваем только стикеры
        increment_daily_stats(user_id, chat_id, today_str, stickers=1)
        increment_total_stats(user_id, chat_id, stickers=1)

        if not chat_name:
            chat_name = chat_id

        sticker = message.sticker
        sticker_info = (
            f"file_id: {sticker.file_id}, "
            f"emoji: {sticker.emoji}, "
            f"set_name: {sticker.set_name}, "
            f"размер: {sticker.width}x{sticker.height}, "
            f"анимированный: {sticker.is_animated}, "
            f"видео: {sticker.is_video}"
        )
        logging.info(
            f"Обновлена статистика: чат \"{chat_name}\", пользователь {user_name}, +1 стикер | {sticker_info}"
        )

    else:
        # Обрабатываем текст / подпись / медиа
        text = getattr(message, "text", None) or getattr(message, "caption", None)
        if text:
            words = len(text.split())
            chars = len(text)
        else:
            words = 1
            chars = 1

        increment_daily_stats(user_id, chat_id, today_str, messages=1, words=words, chars=chars)
        increment_total_stats(user_id, chat_id, messages=1, words=words, chars=chars)

        if not chat_name:
            chat_name = chat_id

        logging.info(
            f"Обновлена статистика: чат \"{chat_name}\", пользователь {user_name}, +1 сообщение, +{words} слов, +{chars} символов"
        )

async def daily_punish_task():
    """
    Каждый день в 22:45 применяет реальные штрафы:
    для пользователей с punished==1 — уменьшает вдвое daily за сегодня и total,
    отправляет отчёт в чат и сбрасывает punished.
    """
    while True:
        now = datetime.now()
        punish_time = now.replace(hour=20, minute=45, second=0, microsecond=0)
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
        reward_time = now.replace(hour=21, minute=55, second=0, microsecond=0)

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

    text = (
        f"📈 Личная статистика для {name}:\n"
        f"За неделю: {week_msgs} сообщений (место #{week_position})\n"
        f"Всего: {total_msgs} сообщений (место #{total_position})"
    )
    text += f"\n☕️ Всего кофе: {total_coffee}"
    if sits_balance > 0:
        text += f"\n💦 Баланс сита: {sits_balance}"

    await message.reply(text)




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


@dp.message()
async def handle_message(message: types.Message):
    # 0) всегда отмечаем активность (включая команды и любые типы сообщений)
    try:
        sticker_manager.note_activity(message.chat.id, message.date)
    except Exception:
        logging.exception("note_activity failed")

    # игнорируем команды
    if message.text and message.text.startswith("/"):
        return

    # Игнорируем полностью пустые сообщения без медиа
    if not (message.text or message.sticker or message.photo or message.video or message.voice or message.animation):
        return

    # Получаем название чата, если это группа/супергруппа
    chat_name = message.chat.title if message.chat.type in ["group", "supergroup"] else message.chat.id

    # Передаем сообщение в update_stats
    update_stats(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        user_name=message.from_user.full_name,
        message=message,
        chat_name=chat_name
    )

#склонение сита
def sit_word(n: int) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "сит"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "сита"
    return "сит"


def add_sits(chat_id: int, user_id: int, amount: int):
    """Добавляет или вычитает сит для пользователя."""
    from db import get_user, add_or_update_user

    user = get_user(user_id)
    if user is None:
        # создаём пользователя, если нет
        add_or_update_user(user_id, chat_id, name="", sits=amount)
    else:
        new_sits = (user["sits"] or 0) + amount
        add_or_update_user(user_id, chat_id, name=user["name"], sits=new_sits)


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

#клавиатура магазина сита
def build_shop_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for key, item in SHOP_ITEMS.items():
        buttons.append([InlineKeyboardButton(
            text=f"{item['name']} ({item['price']} сит)",
            callback_data=f"shop:buy:{key}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.callback_query(F.data.startswith("shop:buy:"))
async def handle_shop_buy(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    user_name = callback.from_user.full_name

    # Получаем ключ товара
    item_key = callback.data.split(":")[-1]
    item = SHOP_ITEMS.get(item_key)

    if not item:
        await callback.answer("❌ Товар не найден", show_alert=True)
        return

    # Если у товара задано действие — делегируем
    action = item.get("action")
    if action == "send_spider":
        await action_send_spider(callback, item)
        return
    if action == "drink_coffee":
        await action_drink_coffee(callback, item);
        return

    price = item["price"]
    ok, new_balance = spend_sits(chat_id, user_id, price)

    if ok:
        # Сообщение при успешной покупке
        buy_text = item["buy_text"].format(user_name=user_name)
        await callback.message.edit_text(f"{buy_text}\nОстаток: {new_balance} сит")
        logging.info(f"{user_name} купил '{item['name']}' за {price} сит в чате {chat_id}. Остаток: {new_balance}")
        await callback.answer()
    else:
        await callback.answer(f"❌ Недостаточно сит. Твой баланс: {new_balance}", show_alert=True)


async def action_drink_coffee(callback: CallbackQuery, item: dict):
    """
    Обработка покупки/выпивания кофе:
    - учитываем ежедневный и общий счетчик кофе;
    - рассчитываем шанс штрафа;
    - при n>=4 и без штрафа — начисляем 1 сит.
    """
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    user_name = callback.from_user.full_name or callback.from_user.username or str(user_id)

    # 0) Проверяем время (пить после 22:00 нельзя)
    now = datetime.now()
    if 20 <= now.hour < 22:
        await callback.answer(f"После 22:00 фильтр больше не наливают, {user_name} ☕️❌", show_alert=True)
        return

    # 1) Получаем сегодняшнюю статистику
    today_str = now.strftime("%Y-%m-%d")
    daily = get_daily_stats(user_id, chat_id, today_str)
    n = daily["coffee"] if daily else 0

    # 2) Проверяем, есть ли штраф
    user = get_user(user_id, chat_id)
    if user and user["punished"] == 1:
        await callback.answer(f"Дно уже прорвано, на сегодня тебе хватит, {user_name}", show_alert=True)
        return

    # Определяем пол пользователя
    sex = get_user_sex(user_id, chat_id)  # 'male' / 'female' / None

    # 3) Увеличиваем счетчики кофе
    increment_daily_stats(user_id, chat_id, today_str, coffee=1)
    increment_total_stats(user_id, chat_id, coffee=1)
    n += 1

    # 4) Формируем текст покупки
    # buy_text может быть строкой или словарём {"m": "...", "f": "..."}
    buy_text_template = item.get("buy_text")

    if isinstance(buy_text_template, dict):
        if sex == "f":
            base_text = buy_text_template.get("f") or buy_text_template.get("m")
        else:
            base_text = buy_text_template.get("m") or buy_text_template.get("f")
    else:
        base_text = buy_text_template or "{user_name} купил вещь"

    buy_text = base_text.format(user_name=user_name)

    if n >= 3:
        buy_text += " ...в животе начинает бурчать"

    try:
        await callback.message.edit_text(buy_text)
    except Exception as e:
        logging.debug(f"Не удалось отредактировать сообщение магазина: {e}")

    # 5) Рассчитываем шанс штрафа (с третьей кружки)
    punished_now = False
    if n > 2:
        chance = 1 - math.exp(-0.5 * (n - 2))
        punished_now = random.random() < chance

    if punished_now:
        add_or_update_user(user_id, chat_id, user_name, punished=1)
        if sex == "f":
            msg = f"💀 Дно прорвано! До конца дня {user_name} получает штраф — её сообщения будут считаться наполовину"
        else:
            msg = f"💀 Дно прорвано! До конца дня {user_name} получает штраф — его сообщения будут считаться наполовину"

        await callback.message.answer(msg)
        logging.info(f"{user_name} получил флаг punished (кофе {n}) в чате {chat_id}")
        await callback.answer()
        return

    # 6) Если кофе 4+ и нет штрафа — даём 1 сит
    if n >= 4:
        add_sits(chat_id, user_id, 1)
        new_bal = get_user(user_id, chat_id)["sits"]
        if sex == "f":
            msg = f"{user_name} преисполнилась от выпитого фильтра и получила 1 сит. Остаток: {new_bal} сит"
        else:
            msg = f"{user_name} преисполнился от выпитого фильтра и получил 1 сит. Остаток: {new_bal} сит"

        await callback.message.answer(msg)

    await callback.answer()



async def action_send_spider(callback: CallbackQuery, item: dict):
    """
    Универсальное действие для отправки паука.
    callback  - CallbackQuery от нажатия кнопки.
    item      - запись из SHOP_ITEMS (должна содержать price и file).
    """
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    user_name = callback.from_user.full_name or callback.from_user.username or str(user_id)
    user_username = (callback.from_user.username or "").lower()
    user_name_lc = user_name.strip().lower()

    price = int(item.get("price", 0))
    is_tass = (user_name_lc == "tass") or (user_username == "tass")

    # 1) Попытка списать ситы (если не бесплатный)
    new_balance = None
    if not is_tass and price > 0:
        ok, new_balance = spend_sits(chat_id, user_id, price)
        if not ok:
            # недостаточно — показываем alert и не выполняем покупку
            await callback.answer(f"❌ Недостаточно сит. Твой баланс: {new_balance}", show_alert=True)
            return

    # 2) Подготовка пути к файлу
    file_path = item.get("file", "images/spider.jpg")
    if not os.path.isabs(file_path):
        file_path = os.path.join(os.path.dirname(__file__), file_path)

    # 3) Подготовка подписи
    caption = item.get("buy_text", "{user_name} купил вещь").format(user_name=user_name)
    if is_tass:
        caption = f"Tass, для тебя этот товар всегда бесплатно\n{caption}"

    # 4) Попытка отправить файл; при провале — вернуть деньги (если уже списали)
    try:
        photo = FSInputFile(file_path)
        await callback.message.answer_photo(photo, caption=caption)
    except FileNotFoundError:
        logging.exception(f"Файл товара не найден: {file_path}")
        if not is_tass and price > 0:
            add_sits(chat_id, user_id, price)  # возврат средств
        await callback.answer("❌ Ошибка: файл товара не найден на сервере.", show_alert=True)
        return
    except Exception as e:
        logging.exception(f"Ошибка при отправке паука: {e}")
        if not is_tass and price > 0:
            add_sits(chat_id, user_id, price)  # возврат средств
        await callback.answer("❌ Произошла ошибка при отправке товара.", show_alert=True)
        return

    # 5) Успешная отправка — подготовка текста подтверждения и редактирование меню
    if new_balance is None:
        # если бесплатный или price==0, получаем актуальный баланс
        new_balance = get_sits(chat_id, user_id)

    if is_tass:
        confirmation = f"🎁 {user_name}, для тебя этот товар был бесплатным — паук в чате!"
    else:
        confirmation = f"✅ {user_name}, вы купили паука за {price} {sit_word(price)}. Остаток: {new_balance} сит"

    try:
        # Редактируем исходное сообщение с магазином (это уберёт кнопки)
        await callback.message.edit_text(confirmation)
    except Exception as e:
        logging.debug(f"Не удалось отредактировать сообщение магазина: {e}")

    # убираем "часики" у пользователя
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
            add_or_update_user(uid, chat_id, user_row["name"], sits=amount)
            text_lines.append(f"{i + 1} место — {name} — {amount} сит")

        # Отправка сообщения в чат
        try:
            await bot.send_message(chat_id, "\n".join(text_lines))
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение в чат {chat_id}: {e}")









# ---------- Запуск ----------

weekly_awards.bot = bot
weekly_awards.add_sits = add_sits

# Сообщим менеджеру «тихих» стикеров о уже известных чатах
sticker_manager.seed_known_chats_from_db()



async def main():
    asyncio.create_task(daily_reward_task())  # награждение в 23:55
    asyncio.create_task(weekly_awards.weekly_awards_task())  # еженедельные награды
    asyncio.create_task(sticker_manager.silence_checker_task(bot))
    asyncio.create_task(daily_punish_task())  # Ежедневное наказание за кофе

    await dp.start_polling(
        bot,
        allowed_updates=["message", "message_reaction_updated", "message_reaction_count_updated"]
    )


if __name__ == "__main__":
    asyncio.run(main())
