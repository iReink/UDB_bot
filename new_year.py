import asyncio
import random
import sqlite3
from datetime import datetime, timedelta
from contextlib import closing

from db import add_sits

# ================== НАСТРОЙКИ ==================

CHAT_ID = -1002737417162

# Дата и время срабатывания (ТЕСТ: 13:26)
TRIGGER_DATETIME_STR = "2025-12-27 15:55"
TRIGGER_DATETIME = datetime.strptime(TRIGGER_DATETIME_STR, "%Y-%m-%d %H:%M")

# допустимое окно срабатывания после триггера (в минутах)
ALLOWED_DELAY_MINUTES = 3

# если разница между прошлым запуском и конфигом >= этого значения —
# считаем, что это НОВЫЙ запуск
REEXECUTE_DIFF_MINUTES = 10

DB_FILE = "stats.db"

MESSAGE_DELAY = 10  # секунд между сообщениями

# ================== ВНУТРЕННЕЕ ==================


def ensure_service_table():
    """Таблица для защиты от повторного запуска"""
    with closing(sqlite3.connect(DB_FILE)) as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS new_year_runs (
                chat_id INTEGER PRIMARY KEY,
                executed_at TEXT NOT NULL
            )
        """)
        conn.commit()


def get_last_execution(chat_id: int):
    with closing(sqlite3.connect(DB_FILE)) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT executed_at FROM new_year_runs WHERE chat_id = ?",
            (chat_id,)
        )
        row = cur.fetchone()
        return datetime.fromisoformat(row[0]) if row else None


def mark_executed(chat_id: int):
    with closing(sqlite3.connect(DB_FILE)) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO new_year_runs (chat_id, executed_at) VALUES (?, ?)",
            (chat_id, TRIGGER_DATETIME_STR)
        )
        conn.commit()


def is_time_to_run() -> bool:
    now = datetime.now()

    if now < TRIGGER_DATETIME:
        return False

    delta = now - TRIGGER_DATETIME
    return delta <= timedelta(minutes=ALLOWED_DELAY_MINUTES)


def already_executed_and_valid(chat_id: int) -> bool:
    """
    Возвращает True, если запуск уже был и
    время в БД близко к текущему TRIGGER_DATETIME_STR
    """
    last_run = get_last_execution(chat_id)
    if not last_run:
        return False

    diff_minutes = abs(
        (TRIGGER_DATETIME - last_run).total_seconds()
    ) / 60

    if diff_minutes < REEXECUTE_DIFF_MINUTES:
        return True

    # время изменилось существенно — считаем новым запуском
    return False


# ================== ОСНОВНАЯ ЛОГИКА ==================


def get_active_users():
    """Пользователи, писавшие хотя бы одно сообщение за последнюю неделю"""
    week_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    with closing(sqlite3.connect(DB_FILE)) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT user_id
            FROM daily_stats
            WHERE chat_id = ?
              AND date >= ?
              AND messages > 0
        """, (CHAT_ID, week_ago))
        rows = cur.fetchall()

    return [r[0] for r in rows]


def enrich_users(user_ids):
    """Добавляем имя, пол и ник"""
    if not user_ids:
        return []

    placeholders = ",".join("?" for _ in user_ids)

    with closing(sqlite3.connect(DB_FILE)) as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT user_id, name, sex, nick
            FROM users
            WHERE chat_id = ?
              AND user_id IN ({placeholders})
        """, (CHAT_ID, *user_ids))

        rows = cur.fetchall()

    users = []
    for user_id, name, sex, nick in rows:
        users.append({
            "user_id": user_id,
            "name": name or "Безымянный",
            "sex": sex if sex in ("m", "f") else "m",
            "nick": nick
        })

    return users


def get_greetings():
    with closing(sqlite3.connect(DB_FILE)) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT text_m, text_f, gift_name, gift_sits
            FROM new_year_greetings
        """)
        rows = cur.fetchall()

    greetings = [
        {
            "text_m": r[0],
            "text_f": r[1],
            "gift_name": r[2],
            "gift_sits": r[3],
        }
        for r in rows
    ]

    random.shuffle(greetings)
    return greetings


def format_greeting(user, greeting):
    text = greeting["text_f"] if user["sex"] == "f" else greeting["text_m"]
    gift = greeting["gift_name"]
    sits = greeting["gift_sits"]

    mention = f"{user['nick']}" if user["nick"] else user["name"]
    sign = "+" if sits > 0 else ""

    return (
        f"🎄 {mention} {user['name']}, {text}\n"
        f"🎁 Твой подарок: {gift} ({sign}{sits} сит)"
    )


# ================== ПУБЛИЧНЫЙ ENTRY ==================


async def run_new_year(bot):
    ensure_service_table()

    if already_executed_and_valid(CHAT_ID):
        return

    if not is_time_to_run():
        return

    # блокируем повторные запуски СРАЗУ
    mark_executed(CHAT_ID)

    async def send(text):
        await bot.send_message(CHAT_ID, text)
        await asyncio.sleep(MESSAGE_DELAY)

    await send("✨🎄 Готовим новогоднее чудо…")

    active_ids = get_active_users()

    await send("🔥 Отделяем угольки от ситольков…")

    users = enrich_users(active_ids)

    if not users:
        await send("🤷 Никого не нашли, но чудо всё равно случилось.")
        return

    await send("🦌 Натираем бруньку Рудольфу…")

    greetings = get_greetings()

    await send("🎅 Лезем в чей-то дымоход…")

    for user, greeting in zip(users, greetings):
        text = format_greeting(user, greeting)
        await bot.send_message(CHAT_ID, text)
        add_sits(CHAT_ID, user["user_id"], greeting["gift_sits"])
        await asyncio.sleep(MESSAGE_DELAY)
