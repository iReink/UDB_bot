# weekly_awards.py
import asyncio
from datetime import datetime, timedelta
import logging

bot = None       # сюда пробрасывается экземпляр бота из main.py
stats = None     # сюда пробрасывается глобальный словарь статистики
add_sits = None  # сюда пробрасывается функция добавления сит

# Конфигурация наград
WEEKLY_TOP_REWARDS = [10, 7, 5, 3, 3, 2, 2, 2, 2, 2]  # за 1–10 места
ACHIEVEMENT_REWARD = 5  # за каждую ачивку

async def weekly_awards_task():
    """Бесконечная задача — каждое воскресенье в 23:00 подводит итоги недели."""
    while True:
        now = datetime.now()
        # Определяем дату ближайшего воскресенья 23:00
        days_ahead = 6 - now.weekday()  # weekday(): Пн=0, Вс=6
        if days_ahead < 0:
            days_ahead += 7
        award_time = (now + timedelta(days=days_ahead)).replace(hour=23, minute=0, second=0, microsecond=0)
        if award_time <= now:
            award_time += timedelta(days=7)

        wait_seconds = (award_time - now).total_seconds()
        logging.info(f"[weekly_awards] Следующее награждение через {wait_seconds/3600:.1f} часов ({award_time})")
        await asyncio.sleep(wait_seconds)

        await process_weekly_awards()


async def process_weekly_awards():
    """Подведение итогов недели по всем чатам."""
    for chat_id, users in stats.items():
        try:
            await award_weekly_top(chat_id, users)
            await award_stickerbomber(chat_id, users)
            await award_flooder(chat_id, users)
            await award_dushnila(chat_id, users)
            await award_skomrnyashka(chat_id, users)
        except Exception as e:
            logging.exception(f"[weekly_awards] Ошибка при награждении в чате {chat_id}: {e}")


async def award_weekly_top(chat_id, users):
    """Топ-10 флудеров недели + награждение."""
    totals = []
    for uid, data in users.items():
        week_msgs = sum(day["messages"] for day in data["daily"])
        if week_msgs > 0:
            totals.append((week_msgs, uid, data["name"]))

    totals.sort(reverse=True, key=lambda x: x[0])
    top10 = totals[:10]

    if not top10:
        return

    lines = ["🏆 Топ-10 флудеров недели:"]
    for i, (msgs, uid, name) in enumerate(top10):
        reward = WEEKLY_TOP_REWARDS[i]
        add_sits(chat_id, uid, reward)
        lines.append(f"{i+1}. {name} — {msgs} сообщений (+{reward} сит)")

    await bot.send_message(chat_id, "\n".join(lines))


async def award_stickerbomber(chat_id, users):
    """Стикербомбер недели — больше всего стикеров за неделю."""
    candidates = []
    for uid, data in users.items():
        week_stickers = sum(day["stickers"] for day in data["daily"])
        if week_stickers > 0:
            candidates.append((week_stickers, uid, data["name"]))

    if not candidates:
        return

    candidates.sort(reverse=True, key=lambda x: x[0])
    winner = candidates[0]
    add_sits(chat_id, winner[1], ACHIEVEMENT_REWARD)

    text = f"🎯 Стикербомбер недели — {winner[2]} ({winner[0]} стикеров)! +{ACHIEVEMENT_REWARD} сит"
    await bot.send_message(chat_id, text)


async def award_flooder(chat_id, users):
    """Флудер недели — среди топ-10 по сообщениям, наименьшее соотношение chars/messages."""
    totals = []
    for uid, data in users.items():
        week_msgs = sum(day["messages"] for day in data["daily"])
        if week_msgs > 0:
            totals.append((week_msgs, uid, data["name"]))

    totals.sort(reverse=True, key=lambda x: x[0])
    top10 = totals[:10]
    if not top10:
        return

    ratios = []
    for msgs, uid, name in top10:
        week_chars = sum(day["chars"] for day in users[uid]["daily"])
        ratio = week_chars / msgs if msgs else float("inf")
        ratios.append((ratio, uid, name))

    ratios.sort(key=lambda x: x[0])  # минимальное соотношение
    winner = ratios[0]
    add_sits(chat_id, winner[1], ACHIEVEMENT_REWARD)

    text = f"💬 Флудер недели — {winner[2]} (ср. длина {winner[0]:.1f} симв./сообщ.)! +{ACHIEVEMENT_REWARD} сит"
    await bot.send_message(chat_id, text)


async def award_dushnila(chat_id, users):
    """Душнила недели — среди топ-15 по сообщениям, наибольшее соотношение chars/messages."""
    totals = []
    for uid, data in users.items():
        week_msgs = sum(day["messages"] for day in data["daily"])
        if week_msgs > 0:
            totals.append((week_msgs, uid, data["name"]))

    totals.sort(reverse=True, key=lambda x: x[0])
    top15 = totals[:15]
    if not top15:
        return

    ratios = []
    for msgs, uid, name in top15:
        week_chars = sum(day["chars"] for day in users[uid]["daily"])
        ratio = week_chars / msgs if msgs else 0
        ratios.append((ratio, uid, name))

    ratios.sort(reverse=True, key=lambda x: x[0])  # максимальное соотношение
    winner = ratios[0]
    add_sits(chat_id, winner[1], ACHIEVEMENT_REWARD)

    text = f"📜 Душнила недели — {winner[2]} (ср. длина {winner[0]:.1f} симв./сообщ.)! +{ACHIEVEMENT_REWARD} сит"
    await bot.send_message(chat_id, text)


async def award_skomrnyashka(chat_id, users):
    """Скромняшка недели — наименьшее число сообщений среди тех, у кого >= 5 сообщений."""
    candidates = []
    for uid, data in users.items():
        week_msgs = sum(day["messages"] for day in data["daily"])
        if week_msgs >= 5:
            candidates.append((week_msgs, uid, data["name"]))

    if not candidates:
        return

    candidates.sort(key=lambda x: x[0])  # минимальное количество сообщений
    winner = candidates[0]
    add_sits(chat_id, winner[1], ACHIEVEMENT_REWARD)

    text = f"🙈 Скромняшка недели — {winner[2]} ({winner[0]} сообщений)! +{ACHIEVEMENT_REWARD} сит"
    await bot.send_message(chat_id, text)

