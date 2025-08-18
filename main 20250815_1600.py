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

TOKEN = "7566137789:AAFk7sUaT4qFTV5xGzgO1Lh44hzr4bRU8hQ"
STATS_FILE = "stats.json"

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
    "sticker1000": {
        "name": "📝 Купить стикер",
        "price": 1000,
        "buy_text": "Воу воу! {user_name} выложил кругленькую сумму, чтобы купить свой стикер! \nНапиши министру стикеров что именно ты хочешь, но помни, что окончательное решение за ним."
    },
    "spider1": {
        "name": "🕷 Скинуть в чат паука 🕷",
        "price": 1,
        "buy_text": "🕷 {user_name} отправил паука в чат! 🕷",
        "action": "send_spider",
        "file": os.path.join("images", "spider.jpg")  # путь относительно проекта
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

stats = load_stats()

def ensure_user(chat_id, user_id, user_name):
    chat_id = str(chat_id)
    user_id = str(user_id)

    if chat_id not in stats:
        stats[chat_id] = {}
        logging.info(f"Создана статистика для нового чата {chat_id}")

    if user_id not in stats[chat_id]:
        stats[chat_id][user_id] = {
            "name": user_name,
            "daily": [{"messages": 0, "words": 0, "chars": 0} for _ in range(7)],
            "total": {"messages": 0, "words": 0, "chars": 0}
        }
        logging.info(f"Добавлен новый пользователь {user_name} (ID {user_id}) в чат {chat_id}")


def update_stats(chat_id, user_id, user_name, message, chat_name=None):
    ensure_user(chat_id, user_id, user_name)

    # Определяем статистику по типу сообщения
    if message.text:  # обычный текст
        words = len(message.text.split())
        chars = len(message.text)
    else:  # медиа, стикеры, голосовые и т.п.
        words = 1
        chars = 1

    user_data = stats[str(chat_id)][str(user_id)]
    user_data["daily"][0]["messages"] += 1
    user_data["daily"][0]["words"] += words
    user_data["daily"][0]["chars"] += chars

    user_data["total"]["messages"] += 1
    user_data["total"]["words"] += words
    user_data["total"]["chars"] += chars

    if not chat_name:
        chat_name = chat_id  # fallback
    logging.info(f"Обновлена статистика: чат \"{chat_name}\", пользователь {user_name}, +1 сообщение, +{words} слов, +{chars} символов")

    save_stats(stats)



def shift_daily_stats():
    for chat_id in stats:
        for user_id in stats[chat_id]:
            daily = stats[chat_id][user_id]["daily"]
            daily.pop(-1)  # удалить последний день
            daily.insert(0, {"messages": 0, "words": 0, "chars": 0})
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
        week_msgs = sum(day["messages"] for day in data["daily"])
        totals.append((week_msgs, data["name"]))
    totals.sort(reverse=True, key=lambda x: x[0])

    text = "🏆 Топ-10 за неделю:\n"
    for i, (count, name) in enumerate(totals[:10], 1):
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
        totals.append((data["total"]["messages"], data["name"]))
    totals.sort(reverse=True, key=lambda x: x[0])

    text = "📊 Топ-10 за всё время:\n"
    for i, (count, name) in enumerate(totals[:10], 1):
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
    week_msgs = sum(day["messages"] for day in data["daily"])
    week_sorted = sorted(
        stats[chat_id].items(),
        key=lambda item: sum(d["messages"] for d in item[1]["daily"]),
        reverse=True
    )
    week_position = next(
        (i + 1 for i, (uid, _) in enumerate(week_sorted) if uid == user_id),
        None
    )

    # Подсчёт общего топа
    total_sorted = sorted(
        stats[chat_id].items(),
        key=lambda item: item[1]["total"]["messages"],
        reverse=True
    )
    total_position = next(
        (i + 1 for i, (uid, _) in enumerate(total_sorted) if uid == user_id),
        None
    )

    # Формируем ответ
    text = (
        f"📈 Личная статистика:\n"
        f"За неделю: {week_msgs} сообщений (место #{week_position})\n"
        f"Всего: {data['total']['messages']} сообщений (место #{total_position})"
    )

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


@dp.message()
async def handle_message(message: types.Message):
    # игнорируем команды
    if message.text and message.text.startswith("/"):
        return

    # Игнорируем полностью пустые сообщения без медиа
    if not (message.text or message.sticker or message.photo or message.video or message.voice or message.animation):
        return

    # === Проверка на "красавчика дня" ===
    if message.text and "Сегодня красавчик дня" in message.text:
        import re
        match = re.search(r"Сегодня красавчик дня\s*-\s*(.*?)\s*\(", message.text)
        if match:
            winner_name = match.group(1).strip()
            chat_id_str = str(message.chat.id)
            # Ищем по имени в stats
            if chat_id_str in stats:
                for uid, data in stats[chat_id_str].items():
                    if data.get("name") == winner_name:
                        add_sits(chat_id_str, uid, 1)
                        await message.reply("Одна порция сита для красавчика!")
                        break

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


async def main():
    asyncio.create_task(daily_reward_task())  # награждение в 23:55
    asyncio.create_task(daily_shift_task())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
