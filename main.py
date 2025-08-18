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


TOKEN = "7566137789:AAFk7sUaT4qFTV5xGzgO1Lh44hzr4bRU8hQ"
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
        "buy_text": "{user_name} сладко попил фильтра и улыбнулся ☕️☕️☕️",
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

# ---------- Работа с JSON ----------

def load_stats():
    if not os.path.exists(STATS_FILE):
        return {}
    with open(STATS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_stats(data):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_user(chat_id, user_id, user_name):
    """
    Гарантирует корректную структуру stats[chat_id][user_id].
    Приводит daily к длине 7 и добавляет ключи 'stickers' и 'coffee' в day и total.
    Также добавляет 'punished' на уровне пользователя.
    """
    chat_id = str(chat_id)
    user_id = str(user_id)

    if chat_id not in stats:
        stats[chat_id] = {}
        logging.info(f"Создана статистика для нового чата {chat_id}")

    if user_id not in stats[chat_id]:
        # новая структура для пользователя
        stats[chat_id][user_id] = {
            "name": user_name,
            "daily": [{"messages": 0, "words": 0, "chars": 0, "stickers": 0, "coffee": 0} for _ in range(7)],
            "total": {"messages": 0, "words": 0, "chars": 0, "stickers": 0, "coffee": 0},
            "punished": 0
        }
        logging.info(f"Добавлен новый пользователь {user_name} (ID {user_id}) в чат {chat_id}")
        return

    # если пользователь уже есть — убеждаемся, что все поля присутствуют и корректны
    user_data = stats[chat_id][user_id]

    # имя
    if not user_data.get("name"):
        user_data["name"] = user_name

    # daily: нормализуем в список длины 7, и добавляем ключи при необходимости
    daily = user_data.get("daily")
    if not isinstance(daily, list):
        daily = [{"messages": 0, "words": 0, "chars": 0, "stickers": 0, "coffee": 0} for _ in range(7)]
    else:
        # добавляем недостающие ключи в существующие элементы
        for i in range(len(daily)):
            day = daily[i] or {}
            day.setdefault("messages", 0)
            day.setdefault("words", 0)
            day.setdefault("chars", 0)
            day.setdefault("stickers", 0)
            day.setdefault("coffee", 0)
            daily[i] = day
        # если элементов < 7 — дополняем нулевыми в конец (старые дни — дальше по массиву)
        if len(daily) < 7:
            for _ in range(7 - len(daily)):
                daily.append({"messages": 0, "words": 0, "chars": 0, "stickers": 0, "coffee": 0})
        # если элементов > 7 — обрезаем до 7 (правая часть — старые дни)
        if len(daily) > 7:
            daily = daily[:7]

    user_data["daily"] = daily

    # total: гарантируем наличие ключей
    total = user_data.get("total", {})
    total.setdefault("messages", 0)
    total.setdefault("words", 0)
    total.setdefault("chars", 0)
    total.setdefault("stickers", 0)
    total.setdefault("coffee", 0)
    user_data["total"] = total

    # punished: флаг штрафа (0/1)
    user_data.setdefault("punished", 0)

    # сохраняем назад (stats — глобальный)
    stats[chat_id][user_id] = user_data



def migrate_stats_add_fields():
    """
    Пройти по существующему stats и добавить поля 'stickers', 'coffee' в daily и total,
    а также поле 'punished' на уровне пользователя — если их нет, для обратной совместимости.
    """
    changed = False
    for chat_id, users in list(stats.items()):
        for user_id, user_data in list(users.items()):
            # используем ensure_user, он нормализует структуру
            ensure_user(chat_id, user_id, user_data.get("name", ""))
            changed = True
    if changed:
        save_stats(stats)
        logging.info("Миграция: добавлены поля 'stickers', 'coffee', 'punished' в stats (если нужно).")



stats = load_stats()
migrate_stats_add_fields()

def update_stats(chat_id, user_id, user_name, message, chat_name=None):
    """
    Обновляет статистику для сообщения message.
    Если это стикер — увеличиваем только 'stickers'.
    Иначе — обновляем messages/words/chars как раньше.
    """
    ensure_user(chat_id, user_id, user_name)

    cid = str(chat_id)
    uid = str(user_id)
    user_data = stats[cid][uid]

    # Определяем, является ли сообщение стикером
    is_sticker = getattr(message, "sticker", None) is not None

    if is_sticker:
        # Увеличиваем только счётчик стикеров
        user_data["daily"][0]["stickers"] += 1
        user_data["total"]["stickers"] += 1

        # Логируем с расширенной информацией
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
        # Обрабатываем текст / подпись / медиа как раньше (медиа = 1 слово / 1 символ)
        text = ""
        if getattr(message, "text", None):
            text = message.text
        elif getattr(message, "caption", None):
            text = message.caption

        if text:
            words = len(text.split())
            chars = len(text)
        else:
            # медиа без текста (photo, video, voice, animation и т.п.)
            words = 1
            chars = 1

        user_data["daily"][0]["messages"] += 1
        user_data["daily"][0]["words"] += words
        user_data["daily"][0]["chars"] += chars

        user_data["total"]["messages"] += 1
        user_data["total"]["words"] += words
        user_data["total"]["chars"] += chars

        if not chat_name:
            chat_name = chat_id
        logging.info(
            f"Обновлена статистика: чат \"{chat_name}\", пользователь {user_name}, +1 сообщение, +{words} слов, +{chars} символов"
        )

    # Сохраняем изменения
    save_stats(stats)




def shift_daily_stats():
    for chat_id in stats:
        for user_id in stats[chat_id]:
            daily = stats[chat_id][user_id]["daily"]
            # удалить самый старый день справа и вставить новый нулевой в начало
            daily.pop(-1)
            daily.insert(0, {"messages": 0, "words": 0, "chars": 0, "stickers": 0, "coffee": 0})
    save_stats(stats)
    logging.info("Суточная статистика сдвинута")



# ---------- Периодический сдвиг ----------

async def daily_shift_task():
    while True:
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (tomorrow - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        shift_daily_stats()

async def daily_punish_task():
    """
    Каждый день в 22:45 применяет реальные штрафы:
    для пользователей с punished==1 — уменьшает вдвое daily[0] (messages/words/chars/stickers),
    вычитает уменьшение из total, отправляет отчёт в чат и сбрасывает punished.
    """
    while True:
        now = datetime.now()
        punish_time = now.replace(hour=22, minute=45, second=0, microsecond=0)
        if now >= punish_time:
            punish_time += timedelta(days=1)

        wait_seconds = (punish_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        # применяем штрафы по всем чатам
        for chat_id, users in list(stats.items()):
            lines = []
            changed = False
            for uid, data in list(users.items()):
                if int(data.get("punished", 0)) != 1:
                    continue

                # текущие значения сегодня
                d0 = data.get("daily", [])[0] if data.get("daily") else {"messages":0,"words":0,"chars":0,"stickers":0,"coffee":0}
                m_old = int(d0.get("messages", 0) or 0)
                w_old = int(d0.get("words", 0) or 0)
                c_old = int(d0.get("chars", 0) or 0)
                s_old = int(d0.get("stickers", 0) or 0)

                # новые (пополам, целые)
                m_new = m_old // 2
                w_new = w_old // 2
                c_new = c_old // 2
                s_new = s_old // 2

                dm = m_old - m_new
                dw = w_old - w_new
                dc = c_old - c_new
                ds = s_old - s_new

                # применяем изменения
                data["daily"][0]["messages"] = m_new
                data["daily"][0]["words"] = w_new
                data["daily"][0]["chars"] = c_new
                data["daily"][0]["stickers"] = s_new

                # корректировка total (не опускаем ниже 0)
                data["total"]["messages"] = max(0, int(data["total"].get("messages", 0)) - dm)
                data["total"]["words"] = max(0, int(data["total"].get("words", 0)) - dw)
                data["total"]["chars"] = max(0, int(data["total"].get("chars", 0)) - dc)
                data["total"]["stickers"] = max(0, int(data["total"].get("stickers", 0)) - ds)

                # снимаем метку punished
                data["punished"] = 0

                save_stats(stats)
                changed = True
                name = data.get("name", str(uid))
                lines.append(f"{name}: -{dm} сообщений, -{dw} слов, -{dc} символов, -{ds} стикеров")

            if changed:
                try:
                    await bot.send_message(chat_id, "Применены штрафы за чрезмерное потребление кофе:\n" + "\n".join(lines))
                except Exception as e:
                    logging.exception(f"Не удалось отправить сообщение о штрафах в чат {chat_id}: {e}")



# ---------- Награждение топ-3 ----------

async def daily_reward_task():
    while True:
        now = datetime.now()
        reward_time = now.replace(hour=23, minute=55, second=0, microsecond=0)

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
    chat_id = str(message.chat.id)
    if chat_id not in stats:
        await message.reply("Пока нет статистики.")
        return

    totals = []
    for user_id, data in stats[chat_id].items():
        week_msgs = sum(day.get("messages", 0) for day in data.get("daily", []))
        totals.append((week_msgs, user_id))
    totals.sort(reverse=True, key=lambda x: x[0])

    text = "🏆 Топ-10 за неделю:\n"
    for i, (count, uid) in enumerate(totals[:10], 1):
        data = stats[chat_id][uid]
        name = data.get("name", "Unknown")
        if int(data.get("punished", 0)):
            name = f"{name} ☠️"
        text += f"{i}. {name} — {count} сообщений\n"

    await message.reply(text)


@dp.message(Command("totaltop"))
async def total_top(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id not in stats:
        await message.reply("Пока нет статистики.")
        return

    totals = []
    for user_id, data in stats[chat_id].items():
        totals.append((int(data.get("total", {}).get("messages", 0) or 0), user_id))
    totals.sort(reverse=True, key=lambda x: x[0])

    text = "📊 Топ-10 за всё время:\n"
    for i, (count, uid) in enumerate(totals[:10], 1):
        data = stats[chat_id][uid]
        name = data.get("name", "Unknown")
        if int(data.get("punished", 0)):
            name = f"{name} ☠️"
        text += f"{i}. {name} — {count} сообщений\n"

    await message.reply(text)


@dp.message(Command("flood"))
async def flood_stats(message: types.Message):
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id)

    if chat_id not in stats or user_id not in stats[chat_id]:
        await message.reply("Пока нет статистики по тебе.")
        return

    data = stats[chat_id][user_id]

    # Подсчёт за неделю
    week_msgs = sum(day.get("messages", 0) for day in data.get("daily", []))
    week_sorted = sorted(
        stats[chat_id].items(),
        key=lambda item: sum(d.get("messages", 0) for d in item[1].get("daily", [])),
        reverse=True
    )
    week_position = next(
        (i + 1 for i, (uid, _) in enumerate(week_sorted) if uid == user_id),
        None
    )

    # Подсчёт общего топа
    total_sorted = sorted(
        stats[chat_id].items(),
        key=lambda item: int(item[1].get("total", {}).get("messages", 0) or 0),
        reverse=True
    )
    total_position = next(
        (i + 1 for i, (uid, _) in enumerate(total_sorted) if uid == user_id),
        None
    )

    # Формируем ответ
    name = data.get("name", message.from_user.full_name)
    if int(data.get("punished", 0)):
        name = f"{name} ☠️"

    text = (
        f"📈 Личная статистика для {name}:\n"
        f"За неделю: {week_msgs} сообщений (место #{week_position})\n"
        f"Всего: {data.get('total', {}).get('messages', 0)} сообщений (место #{total_position})"
    )

    # Добавляем общее количество кофе
    total_coffee = int(data.get("total", {}).get("coffee", 0) or 0)
    text += f"\n☕️ Всего кофе: {total_coffee}"

    # Если есть баланс сита
    sits_balance = data.get("sits", 0)
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

@dp.message(Command("addsit"))
async def add_sit_command(message: types.Message):
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) != 2:
        await message.reply("Использование: /addsit N (N — целое число > 0)")
        return

    try:
        amount = int(parts[1])
    except ValueError:
        await message.reply("N должно быть целым числом.")
        return

    if amount <= 0:
        await message.reply("N должно быть больше нуля.")
        return

    add_sits(message.chat.id, message.from_user.id, amount)
    new_balance = get_sits(message.chat.id, message.from_user.id)
    await message.reply(f"✅ Добавлено {amount} сит. Текущий баланс: {new_balance}")

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


#добавление сита
def add_sits(chat_id, user_id, amount):
    ensure_user(chat_id, user_id, "")  # создаём пользователя, если нет
    user_data = stats[str(chat_id)][str(user_id)]
    if "sits" not in user_data:
        user_data["sits"] = 0
    user_data["sits"] += amount
    save_stats(stats)

#получение баланса сита
def get_sits(chat_id, user_id):
    if str(chat_id) not in stats or str(user_id) not in stats[str(chat_id)]:
        return 0
    return stats[str(chat_id)][str(user_id)].get("sits", 0)

def spend_sits(chat_id, user_id, amount) -> tuple[bool, int]:
    """
    Пытается списать amount сит.
    Возвращает (успех: bool, новый_или_текущий_баланс: int).
    """
    chat_id = str(chat_id)
    user_id = str(user_id)

    # гарантируем наличие записи пользователя
    ensure_user(chat_id, user_id, "")

    current = stats[chat_id][user_id].get("sits", 0)
    if current >= amount:
        stats[chat_id][user_id]["sits"] = current - amount
        save_stats(stats)
        return True, stats[chat_id][user_id]["sits"]
    else:
        return False, current

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
    Обработка покупки/выпивания фильтра (coffee).
    - Проверяет, есть ли уже штраф (punished) и блокирует при необходимости
    - Обновляет daily[0].coffee и total.coffee
    - Рассчитывает шанс штрафа и при срабатывании ставит punished=1
    - При n>=3 добавляет "...в животе начинает бурчать" к buy_text
    - Если n>=4 и НЕ был штраф — начисляет 1 сит сразубля
    """
    chat_id = str(callback.message.chat.id)
    user_id = str(callback.from_user.id)
    user_name = callback.from_user.full_name or callback.from_user.username or str(user_id)

    # Убедимся, что пользователь есть в stats
    ensure_user(chat_id, user_id, user_name)
    user_data = stats[chat_id][user_id]

    # 0) Если пользователь уже получил штраф — блокируем кофе
    if user_data.get("punished", 0) == 1:
        await callback.answer(f"Дно уже прорвано, тебе хватит, {user_name}", show_alert=True)
        return

    # 0.1) После 22:00 пить кофе нельзя
    from datetime import datetime
    now = datetime.now()
    if now.hour >= 22:
        await callback.answer(f"После 22:00 фильтр больше не наливают, {user_name} ☕️❌", show_alert=True)
        return

    # 1) Увеличиваем счётчики кофе
    user_data["daily"][0]["coffee"] = int(user_data["daily"][0].get("coffee", 0)) + 1
    user_data["total"]["coffee"] = int(user_data["total"].get("coffee", 0)) + 1
    n = user_data["daily"][0]["coffee"]

    save_stats(stats)

    # 2) Формируем текст покупки
    buy_text = item.get("buy_text", "{user_name} купил вещь").format(user_name=user_name)
    if n >= 3:
        buy_text += " ...в животе начинает бурчать"

    # 3) Рассчитываем шанс штрафа (с третьего кофе)
    punished_now = False
    if n > 2:
        chance = 1 - math.exp(-0.8 * (n - 2))
        punished_now = random.random() < chance

    # 4) Отправляем подтверждающее сообщение
    try:
        await callback.message.edit_text(buy_text)
    except Exception as e:
        logging.debug(f"Не удалось отредактировать сообщение магазина: {e}")

    # 5) Если штраф выпал
    if punished_now:
        user_data["punished"] = 1
        save_stats(stats)
        await callback.message.answer(
            f"💀 Дно прорвано! До конца дня {user_name} получает штраф на снижение числа сообщений вдвое")
        logging.info(f"{user_name} получил флаг punished (кофе {n}) в чате {chat_id}")
        await callback.answer()
        return

    # 6) Если кофе 4+ и нет штрафа — даём 1 сит
    if n >= 4:
        add_sits(chat_id, user_id, 1)
        new_bal = get_sits(chat_id, user_id)
        await callback.message.answer(
            f"{user_name} преисполнился от выпитого фильтра и получил 1 сит. Остаток: {new_bal} сит")

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

#награждение топ-3 ситом
async def reward_daily_top(bot: Bot):
    for chat_id, users in stats.items():
        # Сортировка по количеству сообщений за сегодня
        sorted_users = sorted(
            users.items(),
            key=lambda item: item[1]["daily"][0]["messages"],
            reverse=True
        )

        top3 = sorted_users[:3]
        if not top3 or top3[0][1]["daily"][0]["messages"] == 0:
            continue  # нет активности за сегодня

        rewards = [2, 1, 1]
        text_lines = ["За ежедневный вклад во флуд в чяте награждаются:"]

        for i, (user_id, data) in enumerate(top3):
            amount = rewards[i]
            add_sits(chat_id, user_id, amount)
            text_lines.append(f"{i+1} место — {data['name']} — {amount} сит")

        try:
            await bot.send_message(chat_id, "\n".join(text_lines))
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение в чат {chat_id}: {e}")


# ---------- Запуск ----------

weekly_awards.bot = bot
weekly_awards.stats = stats
weekly_awards.add_sits = add_sits

# Сообщим менеджеру «тихих» стикеров о уже известных чатах
sticker_manager.seed_known_chats_from_stats(stats)



async def main():
    asyncio.create_task(daily_reward_task())  # награждение в 23:55
    asyncio.create_task(daily_shift_task())
    asyncio.create_task(weekly_awards.weekly_awards_task())  # еженедельные награды
    asyncio.create_task(sticker_manager.silence_checker_task(bot))
    asyncio.create_task(daily_punish_task())  # Ежедневное наказание за кофе

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
