# weekly_awards.py
import asyncio
from datetime import datetime, timedelta
import logging
import sqlite3
import db
from db import add_or_update_user_achievement
from db import add_or_update_user_achievement, get_achievement_title, get_connection, get_user_sex


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
        award_time = (now + timedelta(days=days_ahead)).replace(hour=21, minute=5, second=0, microsecond=0)
        if award_time <= now:
            award_time += timedelta(days=7)

        wait_seconds = (award_time - now).total_seconds()
        logging.info(f"[weekly_awards] Следующее награждение через {wait_seconds/3600:.1f} часов ({award_time})")
        await asyncio.sleep(wait_seconds)

        await process_weekly_awards()


async def process_weekly_awards():
    """Подведение итогов недели по всем чатам с данными из SQLite."""
    DB_FILE = "stats.db"  # путь к базе
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Получаем список всех chat_id
        cur.execute("SELECT DISTINCT chat_id FROM users")
        chat_ids = [row["chat_id"] for row in cur.fetchall()]

        for chat_id in chat_ids:
            try:
                # Получаем статистику за последние 7 дней для всех пользователей чата
                cur.execute("""
                    SELECT u.user_id, u.name, u.sits, u.punished,
                           SUM(d.messages) as messages,
                           SUM(d.words) as words,
                           SUM(d.chars) as chars,
                           SUM(d.stickers) as stickers,
                           SUM(d.coffee) as coffee
                    FROM users u
                    JOIN daily_stats d ON u.user_id = d.user_id AND u.chat_id = d.chat_id
                    WHERE u.chat_id = ?
                      AND date(d.date) >= date('now', '-6 days')
                    GROUP BY u.user_id
                """, (chat_id,))
                users_rows = cur.fetchall()

                # Формируем словарь, как раньше
                users = {}
                for row in users_rows:
                    users[row["user_id"]] = {
                        "name": row["name"],
                        "sits": row["sits"],
                        "punished": row["punished"],
                        "messages": row["messages"],
                        "words": row["words"],
                        "chars": row["chars"],
                        "stickers": row["stickers"],
                        "coffee": row["coffee"]
                    }

                # Вызываем функции награждения
                await award_weekly_top(chat_id, users)
                await award_stickerbomber(chat_id, users)
                await award_flooder(chat_id)
                await award_dushnila(chat_id)
                await award_skomrnyashka(chat_id)
                await award_lubimka(chat_id)
                await award_likes_collector(chat_id)
                await award_dobroe_serdtse(chat_id)
                await award_tsarsky_like(chat_id)

            except Exception as e:
                logging.exception(f"[weekly_awards] Ошибка при награждении в чате {chat_id}: {e}")

    finally:
        conn.close()


async def award_weekly_top(chat_id, users):
    """Топ-10 флудеров недели + награждение (данные уже из БД)."""
    # users — словарь {user_id: {name, sits, punished, messages, words, chars, stickers, coffee}}
    totals = [(data["messages"], uid, data["name"]) for uid, data in users.items() if data["messages"] > 0]
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
    """Стикербомбер недели — больше всего стикеров за неделю (учитываем пол)."""


    # Выбираем кандидатов с количеством стикеров > 0
    candidates = [(data["stickers"], uid, data["name"]) for uid, data in users.items() if data["stickers"] > 0]

    if not candidates:
        return

    # Сортируем по количеству стикеров и выбираем победителя
    candidates.sort(reverse=True, key=lambda x: x[0])
    winner_stickers, winner_id, winner_name = candidates[0]

    # Начисляем ситы
    add_sits(chat_id, winner_id, ACHIEVEMENT_REWARD)

    # Определяем пол победителя
    sex = get_user_sex(winner_id, chat_id)
    title = "Стикербомбер" if sex == "m" else "Стикербомберка" if sex == "f" else "Стикербомбер(?)"

    # Добавляем запись о полученной ачивке в таблицу user_achievements
    add_or_update_user_achievement(winner_id, chat_id, "sticker_bomber")

    # Формируем и отправляем сообщение
    text = f"🎯 {title} недели — {winner_name} ({winner_stickers} стикеров)! +{ACHIEVEMENT_REWARD} сит"
    await bot.send_message(chat_id, text)




from db import get_user_sex, DB_FILE
import sqlite3

async def award_flooder(chat_id: int):
    """Флудер недели — среди топ-10 по сообщениям, наименьшее соотношение chars/messages."""
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        # Получаем данные за последние 7 дней для всех пользователей чата
        cur.execute("""
            SELECT u.user_id, u.name,
                   SUM(d.messages) as week_msgs,
                   SUM(d.chars) as week_chars
            FROM users u
            JOIN daily_stats d ON u.user_id = d.user_id AND u.chat_id = d.chat_id
            WHERE u.chat_id = ?
              AND d.date >= date('now','-6 days')
            GROUP BY u.user_id
            HAVING week_msgs > 0
            ORDER BY week_msgs DESC
            LIMIT 10
        """, (chat_id,))
        top10 = cur.fetchall()

        if not top10:
            return

        # Вычисляем соотношение chars/messages
        ratios = [(week_chars / week_msgs, user_id, name) for user_id, name, week_msgs, week_chars in top10]
        ratios.sort(key=lambda x: x[0])  # минимальное соотношение
        ratio, winner_id, winner_name = ratios[0]

        # Начисляем сит
        add_sits(chat_id, winner_id, ACHIEVEMENT_REWARD)

        # Получаем пол пользователя
        sex = get_user_sex(winner_id, chat_id)

        # Достаём названия ачивки из БД
        cur.execute("""
            SELECT name_m, name_f FROM achievements WHERE key = 'fluder'
        """)
        row = cur.fetchone()
        if row:
            name_m, name_f = row
        else:
            name_m, name_f = "Флудер", "Флудерка"

        if sex == "m":
            title = name_m
        elif sex == "f":
            title = name_f
        else:
            title = f"{name_m}(?)"

        # Отправка сообщения в чат
        text = f"💬 {title} недели — {winner_name} (ср. длина {ratio:.1f} симв./сообщ.)! +{ACHIEVEMENT_REWARD} сит"
        await bot.send_message(chat_id, text)

        # Добавление записи в user_achievements
        add_or_update_user_achievement(winner_id, chat_id, "fluder")

    finally:
        conn.close()




from db import get_user_sex, DB_FILE
import sqlite3

async def award_dushnila(chat_id: int):
    """Душнила недели — среди топ-15 по сообщениям, наибольшее соотношение chars/messages."""
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        # Получаем данные за последние 7 дней для всех пользователей чата
        cur.execute("""
            SELECT u.user_id, u.name,
                   SUM(d.messages) as week_msgs,
                   SUM(d.chars) as week_chars
            FROM users u
            JOIN daily_stats d ON u.user_id = d.user_id AND u.chat_id = d.chat_id
            WHERE u.chat_id = ?
              AND d.date >= date('now','-6 days')
            GROUP BY u.user_id
            HAVING week_msgs > 0
            ORDER BY week_msgs DESC
            LIMIT 15
        """, (chat_id,))
        top15 = cur.fetchall()

        if not top15:
            return

        # Вычисляем соотношение chars/messages
        ratios = [(week_chars / week_msgs, user_id, name) for user_id, name, week_msgs, week_chars in top15]
        ratios.sort(reverse=True, key=lambda x: x[0])  # максимальное соотношение
        ratio, winner_id, winner_name = ratios[0]

        # Начисляем сит
        add_sits(chat_id, winner_id, ACHIEVEMENT_REWARD)

        # Получаем пол пользователя
        sex = get_user_sex(winner_id, chat_id)

        # Достаём названия ачивки из БД
        cur.execute("""
            SELECT name_m, name_f FROM achievements WHERE key = 'dushnila'
        """)
        row = cur.fetchone()
        if row:
            name_m, name_f = row
        else:
            name_m, name_f = "Душнила", "Душнила"

        if sex == "m":
            title = name_m
        elif sex == "f":
            title = name_f
        else:
            title = f"{name_m}(?)"

        # Отправка сообщения в чат
        text = f"📜 {title} недели — {winner_name} (ср. длина {ratio:.1f} симв./сообщ.)! +{ACHIEVEMENT_REWARD} сит"
        await bot.send_message(chat_id, text)

        # Добавление записи в user_achievements
        add_or_update_user_achievement(winner_id, chat_id, "dushnila")

    finally:
        conn.close()



from db import get_user_sex, DB_FILE
import sqlite3

async def award_skomrnyashka(chat_id: int):
    """Скромняшка недели — наименьшее число сообщений среди тех, у кого >= 5 сообщений."""
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        # Получаем суммарные сообщения за последние 7 дней для всех пользователей чата
        cur.execute("""
            SELECT u.user_id, u.name, SUM(d.messages) as week_msgs
            FROM users u
            JOIN daily_stats d ON u.user_id = d.user_id AND u.chat_id = d.chat_id
            WHERE u.chat_id = ?
              AND d.date >= date('now','-6 days')
            GROUP BY u.user_id
            HAVING week_msgs >= 5
        """, (chat_id,))
        candidates = cur.fetchall()

        if not candidates:
            return

        # Находим пользователя с минимальным количеством сообщений
        candidates.sort(key=lambda x: x[2])  # x[2] = week_msgs
        winner_id, winner_name, week_msgs = candidates[0]

        # Начисляем сит
        add_sits(chat_id, winner_id, ACHIEVEMENT_REWARD)

        # Получаем пол пользователя
        sex = get_user_sex(winner_id, chat_id)

        # Достаём названия ачивки из БД
        cur.execute("""
            SELECT name_m, name_f FROM achievements WHERE key = 'skomrnyashka'
        """)
        row = cur.fetchone()
        if row:
            name_m, name_f = row
        else:
            name_m, name_f = "Скромняшек", "Скромняшка"

        if sex == "m":
            title = name_m
        elif sex == "f":
            title = name_f
        else:
            title = f"{name_f}(?)"

        # Отправка сообщения в чат
        text = f"🙈 {title} недели — {winner_name} ({week_msgs} сообщений)! +{ACHIEVEMENT_REWARD} сит"
        await bot.send_message(chat_id, text)

        # Добавление записи в user_achievements
        add_or_update_user_achievement(winner_id, chat_id, "skromnyashka")

    finally:
        conn.close()

async def award_lubimka(chat_id: int):
    """Любимка недели — юзер с наибольшим средним числом полученных лайков за сообщение за неделю (≥5 сообщений)."""


    with get_connection() as conn:
        cur = conn.cursor()
        # Считаем лайки за 7 дней
        cur.execute("""
            SELECT u.user_id, u.name,
                   SUM(d.react_taken) as likes_taken,
                   SUM(d.messages) as msgs
            FROM users u
            JOIN daily_stats d ON u.user_id = d.user_id AND u.chat_id = d.chat_id
            WHERE u.chat_id = ?
              AND date(d.date) >= date('now','-6 days')
            GROUP BY u.user_id
            HAVING msgs >= 5
        """, (chat_id,))
        rows = cur.fetchall()

    if not rows:
        return

    # Среднее число лайков на сообщение
    candidates = [(likes_taken / msgs, user_id, name) for user_id, name, likes_taken, msgs in rows if msgs > 0]
    if not candidates:
        return

    candidates.sort(reverse=True, key=lambda x: x[0])
    avg_likes, winner_id, winner_name = candidates[0]

    # Добавляем ачивку
    add_or_update_user_achievement(winner_id, chat_id, "lubimka")

    # Берём название из БД (у Любимки одинаковое для m/f)
    sex = get_user_sex(winner_id, chat_id)
    title = get_achievement_title("lubimka", sex)

    # Сообщение в чат
    text = f"💖 {title} недели — {winner_name} (в среднем {avg_likes:.2f} лайка/сообщение)!"
    await bot.send_message(chat_id, text)



async def award_likes_collector(chat_id: int):
    """Лайкосборник недели — автор с наибольшим числом полученных лайков за неделю."""
    DB_FILE = "stats.db"
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        # Берём суммарные полученные лайки за последние 7 дней
        cur.execute("""
            SELECT u.user_id, u.name, SUM(d.react_taken) as week_likes
            FROM users u
            JOIN daily_stats d ON u.user_id = d.user_id AND u.chat_id = d.chat_id
            WHERE u.chat_id = ?
              AND d.date >= date('now','-6 days')
            GROUP BY u.user_id
            HAVING week_likes > 0
            ORDER BY week_likes DESC
            LIMIT 1
        """, (chat_id,))
        row = cur.fetchone()

        if not row:
            return

        winner_id, winner_name, week_likes = row

        # добавляем ачивку
        add_or_update_user_achievement(winner_id, chat_id, "likes_collector")
        add_sits(chat_id, winner_id, ACHIEVEMENT_REWARD)

        # получаем правильное название ачивки из БД
        sex = get_user_sex(winner_id, chat_id)
        title = get_achievement_title("likesobornik", sex)

        text = f"👍 {title} недели — {winner_name} ({week_likes} лайков)! +{ACHIEVEMENT_REWARD} сит"
        await bot.send_message(chat_id, text)

    finally:
        conn.close()

async def award_dobroe_serdtse(chat_id: int):
    """Большое доброе сердце недели — поставивший больше всех лайков за неделю."""
    DB_FILE = "stats.db"
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        # Берём суммарные отданные лайки за последние 7 дней
        cur.execute("""
            SELECT u.user_id, u.name, SUM(d.react_given) as week_given
            FROM users u
            JOIN daily_stats d ON u.user_id = d.user_id AND u.chat_id = d.chat_id
            WHERE u.chat_id = ?
              AND d.date >= date('now','-6 days')
            GROUP BY u.user_id
            HAVING week_given > 0
            ORDER BY week_given DESC
            LIMIT 1
        """, (chat_id,))
        row = cur.fetchone()

        if not row:
            return

        winner_id, winner_name, week_given = row

        # Добавляем ачивку
        add_or_update_user_achievement(winner_id, chat_id, "dobroe_serdtse")
        add_sits(chat_id, winner_id, ACHIEVEMENT_REWARD)

        # Получаем название ачивки из БД
        sex = get_user_sex(winner_id, chat_id)
        title = get_achievement_title("dobroe_serdtse", sex)

        text = f"💖 {title} недели — {winner_name} (поставил {week_given} лайков)! +{ACHIEVEMENT_REWARD} сит"
        await bot.send_message(chat_id, text)

    finally:
        conn.close()

async def award_tsarsky_like(chat_id: int):
    """Царский лайк недели — самое маленькое соотношение отданных/полученных лайков среди топ-20 по полученным лайкам."""
    DB_FILE = "stats.db"
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        # Получаем топ-20 пользователей по полученным лайкам за неделю
        cur.execute("""
            SELECT u.user_id, u.name,
                   SUM(d.react_taken) as week_taken,
                   SUM(d.react_given) as week_given
            FROM users u
            JOIN daily_stats d ON u.user_id = d.user_id AND u.chat_id = d.chat_id
            WHERE u.chat_id = ?
              AND d.date >= date('now','-6 days')
            GROUP BY u.user_id
            HAVING week_taken > 0
            ORDER BY week_taken DESC
            LIMIT 20
        """, (chat_id,))
        top20 = cur.fetchall()

        if not top20:
            return

        # Вычисляем соотношение отданных/полученных лайков
        ratios = [(row[3] / row[2] if row[2] > 0 else float('inf'), row[0], row[1]) for row in top20]
        ratios.sort(key=lambda x: x[0])  # минимальное соотношение
        ratio, winner_id, winner_name = ratios[0]

        # Добавляем ачивку
        add_or_update_user_achievement(winner_id, chat_id, "tsarsky_like")
        add_sits(chat_id, winner_id, ACHIEVEMENT_REWARD)

        sex = get_user_sex(winner_id, chat_id)
        title = get_achievement_title("tsarsky_like", sex)

        text = f"👑 {title} недели — {winner_name} (соотношение лайков: {ratio:.2f})! +{ACHIEVEMENT_REWARD} сит"
        await bot.send_message(chat_id, text)

    finally:
        conn.close()
